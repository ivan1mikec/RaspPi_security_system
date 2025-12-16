import os
import time
import queue
import threading
from datetime import datetime
from collections import deque
from threading import Lock
from progressive_enroll import should_collect, get_policy, record_accepted_clip
from camera.video_quality import score_video
import cv2
from picamera2 import Picamera2


# Stability / OpenCV

cv2.setNumThreads(1)


# Log + GUI preview state

log_event = lambda msg, tag="C": None  # default no-op logger


def set_camera_logger(func):
    global log_event
    log_event = func


_latest_frame = None          # BGR frame for GUI 
_recording_status = "Not Recording"
frame_lock = Lock()


def get_latest_frame_and_status():
    with frame_lock:
        return (None if _latest_frame is None else _latest_frame.copy()), _recording_status


def _set_status(s: str):
    global _recording_status
    with frame_lock:
        _recording_status = s



# Configuration
MAIN_SIZE = (1280, 720)      # output video (reasonable resolution for CPU)
RING_SIZE = (960, 540)       # pre-roll in RAM (smaller than MAIN to save memory)
LORES_SIZE = (424, 240)      # for detection
FPS = 12.0

RECOG_PRE_SEC = 20           # recognized: 20 s before
RECOG_POST_SEC = 10          # recognized: 10 s after

UNREC_TAIL_SEC = 10          # unrecognized: 10 s after last detected human motion

# MOG2 thresholds (on low-res)
MOG_HISTORY = 300
MOG_VARTHRESH = 16
MOTION_MIN_AREA = 1200       # tune per scene

# HOG people-detector (not every frame)
HOG_EVERY_N = 2
HOG_WIN_STRIDE = (8, 8)

# Paths
BASE_DIR = "recordings"
REC_DIR_RECOGNIZED = os.path.join(BASE_DIR, "recognized")
REC_DIR_UNRECOGNIZED = os.path.join(BASE_DIR, "unrecognized")
os.makedirs(REC_DIR_RECOGNIZED, exist_ok=True)
os.makedirs(REC_DIR_UNRECOGNIZED, exist_ok=True)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _recognized_path(user_id: int) -> str:
    return os.path.join(REC_DIR_RECOGNIZED, f"{_ts()}_{int(user_id)}.avi")

def _recognized_tmp_path(user_id: int) -> str:
    return os.path.join(REC_DIR_RECOGNIZED, f"{_ts()}_{int(user_id)}.tmp.avi")


def _unrecognized_path() -> str:
    return os.path.join(REC_DIR_UNRECOGNIZED, f"{_ts()}_UNKNOWN.avi")


# Background VideoWriter
class VideoWriterThread:
    def __init__(self, filename: str, frame_size=(1280, 720), fps=12.0, fourcc_str="XVID"):
        self.filename = filename
        fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
        self.writer = cv2.VideoWriter(filename, fourcc, fps, frame_size)
        if not self.writer.isOpened():
            raise RuntimeError(f"VideoWriter failed to open: {filename}")

        self.q = queue.Queue(maxsize=1024)
        self.stop_flag = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        try:
            while not self.stop_flag.is_set():
                try:
                    frame = self.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if frame is None:  # shutdown signal
                    break
                self.writer.write(frame)
        finally:
            try:
                self.writer.release()
            except Exception:
                pass

    def push(self, bgr_frame):
        if not self.stop_flag.is_set():
            try:
                self.q.put_nowait(bgr_frame.copy())
            except queue.Full:
                # Drop if full to avoid blocking
                pass

    def close(self):
        if not self.stop_flag.is_set():
            self.stop_flag.set()
            try:
                self.q.put_nowait(None)
            except queue.Full:
                pass
        self.t.join(timeout=2.0)



# Recognized signal (called from main/fingerprint)
_recognized_signal = {"pending": False, "user_id": None, "lock": Lock()}


def mark_recognized_event(user_id: int):
    with _recognized_signal["lock"]:
        _recognized_signal["pending"] = True
        _recognized_signal["user_id"] = int(user_id)


# backward-compat
def notify_recognized_event(user_id: int):
    return mark_recognized_event(user_id)


# compatibility with typo in older main
def notify_recoadgnized_event(user_id: int):
    return mark_recognized_event(user_id)


# Helpers
def _ensure_size(bgr, size_xy):
    w, h = size_xy
    if bgr.shape[1] == w and bgr.shape[0] == h:
        return bgr
    return cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)


def _load_haar(paths):
    for p in paths:
        if os.path.exists(p):
            c = cv2.CascadeClassifier(p)
            if not c.empty():
                return c
    return None


HAAR_FACE = _load_haar([
    "/home/student/haarcascade_frontalface_default.xml",
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
])

HAAR_UPPER = _load_haar([
    "/usr/share/opencv4/haarcascades/haarcascade_upperbody.xml",
    "/usr/share/opencv/haarcascades/haarcascade_upperbody.xml",
])


def _haar_has_human(gray_small) -> bool:
    if HAAR_FACE is not None:
        faces = HAAR_FACE.detectMultiScale(gray_small, 1.1, 2, minSize=(20, 20))
        if len(faces) > 0:
            return True
    if HAAR_UPPER is not None:
        ups = HAAR_UPPER.detectMultiScale(gray_small, 1.05, 2, minSize=(28, 28))
        if len(ups) > 0:
            return True
    return False


# Main loop (preview + recording)
def start_camera_recording():
    global _latest_frame

    # Picamera2 setup: preview (lower resolution for CPU) 
    picam2 = Picamera2()
    picam2.preview_configuration.main.size = MAIN_SIZE
    picam2.preview_configuration.main.format = "RGB888"
    picam2.configure("preview")
    picam2.start()

    # MOG2 for motion (on downscaled frame)
    mog = cv2.createBackgroundSubtractorMOG2(
        history=MOG_HISTORY, varThreshold=MOG_VARTHRESH, detectShadows=False
    )

    # HOG people-detector
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    # ring buffer for pre-roll
    pre_frames = deque(maxlen=int(RECOG_PRE_SEC * FPS))

    # recording state
    recognized_writer = None
    recognized_active = False
    recognized_until = 0.0
    
    recognized_last_tmp = None
    recognized_uid = None

    unrec_writer = None
    human_active = False
    last_human_ts = 0.0
    hog_counter = 0

    _set_status("Not Recording")
    log_event("Camera started (preview + recording)", "C")

    try:
        while True:
            # recognized signal?
            with _recognized_signal["lock"]:
                if _recognized_signal["pending"]:
                    _recognized_signal["pending"] = False
                    uid = _recognized_signal["user_id"]
                    _recognized_signal["user_id"] = None

                    # stop unrecognized if running
                    if unrec_writer is not None:
                        try:
                            unrec_writer.close()
                        except Exception:
                            pass
                        unrec_writer = None
                        human_active = False
                        log_event("UNRECOGNIZED stopped (recognized override)", "C")

                    # start recognized
                    try:
                        if not should_collect(uid):
                            log_event(f"RECOGNIZED skipped: user {uid} dataset ready", "C")
                            recognized_writer = None
                            recognized_active = False
                            recognized_until = 0.0
                            _set_status("Not Recording")
                        else:
                            tmp_path = _recognized_tmp_path(uid)   # temporary file
                            recognized_writer = VideoWriterThread(tmp_path, frame_size=MAIN_SIZE, fps=FPS)
                            for rf in pre_frames:
                                recognized_writer.push(_ensure_size(rf, MAIN_SIZE))
                            recognized_active = True
                            recognized_until = time.time() + RECOG_POST_SEC
                            recognized_last_tmp = tmp_path
                            recognized_uid = uid
                            _set_status("Recording")
                            log_event(f"RECOGNIZED start (tmp): {tmp_path}", "C")
                    except Exception as e:
                        log_event(f"RECOGNIZED start fail: {e}", "C")
                        recognized_writer = None
                        recognized_active = False
                        recognized_last_tmp = None
                        recognized_uid = None

            # Capture one frame (RGB -> BGR)
            frame_rgb = picam2.capture_array()
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            # GUI preview (store copy)
            with frame_lock:
                _latest_frame = frame_bgr.copy()

            # pre-roll: keep RING_SIZE frame 
            if frame_bgr.shape[:2] != (RING_SIZE[1], RING_SIZE[0]):
                ring_frame = cv2.resize(frame_bgr, RING_SIZE, interpolation=cv2.INTER_AREA)
            else:
                ring_frame = frame_bgr
            pre_frames.append(ring_frame)

            # human motion detection (on low-res) 
            lores = cv2.resize(frame_bgr, LORES_SIZE, interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(lores, cv2.COLOR_BGR2GRAY)

            fg = mog.apply(gray)
            fg = cv2.medianBlur(fg, 5)
            contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            motion = any(cv2.contourArea(c) > MOTION_MIN_AREA for c in contours)

            # HOG periodically + Haar fallback
            hog_counter = (hog_counter + 1) % HOG_EVERY_N
            human = False
            if motion and hog_counter == 0:
                rects, _ = hog.detectMultiScale(
                    lores, winStride=HOG_WIN_STRIDE, padding=(8, 8), scale=1.05
                )
                human = len(rects) > 0
                if not human and (HAAR_FACE is not None or HAAR_UPPER is not None):
                    human = _haar_has_human(gray)

            # Unrecognized recording (only when recognized is NOT active) 
            if not recognized_active:
                if motion and human:
                    last_human_ts = time.time()
                    if not human_active:
                        human_active = True
                        try:
                            path = _unrecognized_path()
                            unrec_writer = VideoWriterThread(
                                path, frame_size=MAIN_SIZE, fps=FPS
                            )
                            _set_status("Recording")
                            log_event(f"UNRECOGNIZED start: {path}", "C")
                        except Exception as e:
                            log_event(f"UNRECOGNIZED start fail: {e}", "C")
                            unrec_writer = None
                            human_active = False
                    if unrec_writer:
                        unrec_writer.push(_ensure_size(frame_bgr, MAIN_SIZE))
                else:
                    if human_active and (time.time() - last_human_ts) >= UNREC_TAIL_SEC:
                        human_active = False
                        if unrec_writer:
                            try:
                                unrec_writer.close()
                            except Exception:
                                pass
                            unrec_writer = None
                        _set_status("Not Recording")
                        log_event("UNRECOGNIZED stop (timeout)", "C")

            # Recognized post recording 
            if recognized_active:
                if recognized_writer:
                    recognized_writer.push(_ensure_size(frame_bgr, MAIN_SIZE))
                if time.time() >= recognized_until:
                    recognized_active = False
                    if recognized_writer:
                        try:
                            recognized_writer.close()
                        except Exception:
                            pass
                        recognized_writer = None
                    _set_status("Not Recording")
                    log_event("RECOGNIZED stop", "C")
                    # Quality post-processing (only if we have tmp and uid)
                    if recognized_last_tmp and recognized_uid is not None:
                        try:
                            score, details = score_video(recognized_last_tmp)
                            goal, minq = get_policy()
                            if score >= minq:
                                # rename tmp -> final (visible in recognized/)
                                final_path = _recognized_path(recognized_uid)
                                try:
                                    os.replace(recognized_last_tmp, final_path)
                                except Exception:
                                    import shutil
                                    shutil.copy2(recognized_last_tmp, final_path)
                                    try: os.remove(recognized_last_tmp)
                                    except Exception: pass
                                # record progress
                                record_accepted_clip(recognized_uid, final_path, score, details)
                                log_event(f"RECOGNIZED saved (q={score:.3f}) -> {final_path}", "C")
                            else:
                                # poor recording - remove temporary file
                                try: os.remove(recognized_last_tmp)
                                except Exception: pass
                                log_event(f"RECOGNIZED dropped (q={score:.3f})", "C")
                        except Exception as e:
                            log_event(f"RECOGNIZED post-process error: {e}", "C")

                    recognized_last_tmp = None
                    recognized_uid = None

            # pace ~ FPS
            time.sleep(max(0.0, (1.0 / FPS) - 0.001))

    except Exception as e:
        log_event(f"Camera error: {e}", "C")
        _set_status("Not Recording")
    finally:
        try:
            if recognized_writer:
                recognized_writer.close()
            if unrec_writer:
                unrec_writer.close()
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass
        log_event("Camera stopped", "C")
        _set_status("Not Recording")
