# video_quality.py
import os
import cv2
import numpy as np

def _load_haar(paths):
    for p in paths:
        if os.path.exists(p):
            c = cv2.CascadeClassifier(p)
            if not c.empty():
                return c
    return None

# Pokušaj učitavanja frontal-face kaskade (nije obavezno; bez nje face-score=0)
HAAR_FACE = _load_haar([
    "/home/student/haarcascade_frontalface_default.xml",
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
])

def _brightness_score(gray):
    """
    Širi 'zeleni' raspon: puna ocjena u 0.35–0.75 (dovoljno dobro osvjetljenje).
    Izvan toga linearni pad prema 0 na rubovima 0.15 i 0.95.
    """
    m = gray.mean() / 255.0
    if 0.35 <= m <= 0.75:
        return 1.0
    if m < 0.35:
        return max(0.0, (m - 0.15) / (0.35 - 0.15))
    return max(0.0, (0.95 - m) / (0.95 - 0.75))

def _sharpness_score(gray):
    """
    Oštrina preko varijance Laplaciana.
    Blaža normalizacija: val/200 + blaga kompresija tanh funkcijom.
    """
    val = cv2.Laplacian(gray, cv2.CV_64F).var()
    raw = val / 200.0
    return float(np.clip(0.85 * np.tanh(raw) + 0.15 * np.clip(raw, 0.0, 1.0), 0.0, 1.0))

def _face_score(frame):
    """
    Ako je detektirano lice: bazni boost 0.30 + fino bodovanje (coverage + centriranost).
    Sweet-spot za coverage ~8–20% kadra; izvan toga linearan pad do 35%.
    """
    if HAAR_FACE is None:
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = HAAR_FACE.detectMultiScale(gray, 1.1, 3, minSize=(60, 60))
    if len(faces) == 0:
        return 0.0

    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    H, W = gray.shape[:2]
    coverage = (w * h) / (W * H)

    # Coverage: idealno 0.08–0.20
    if 0.08 <= coverage <= 0.20:
        cov = 1.0
    elif coverage < 0.08:
        cov = max(0.0, coverage / 0.08)
    else:
        cov = max(0.0, (0.35 - coverage) / (0.35 - 0.20))

    # Centriranost
    cx, cy = x + w / 2.0, y + h / 2.0
    dx, dy = abs(cx - W / 2.0) / (W / 2.0), abs(cy - H / 2.0) / (H / 2.0)
    center = 1.0 - float(np.clip(np.hypot(dx, dy), 0.0, 1.0))

    fine = 0.6 * cov + 0.4 * center
    return float(np.clip(0.30 + 0.70 * fine, 0.0, 1.0))

def score_video(path, max_frames=120):
    """
    Vraća (score[0..1], detalji). Agregacija: 25% svjetlina + 25% oštrina + 50% lice.
    Za lice se uzima 90. percentil kroz video (stabilnije od maksimuma).
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0.0, {"error": "open"}

    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    step = max(1, n // max_frames)

    b, s, f = [], [], []
    idx = 0
    ok, frame = cap.read()
    while ok:
        if idx % step == 0:
            frm = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frm, cv2.COLOR_BGR2GRAY)
            b.append(_brightness_score(gray))
            s.append(_sharpness_score(gray))
            f.append(_face_score(frm))
        idx += 1
        ok, frame = cap.read()
    cap.release()

    if not b:
        return 0.0, {"error": "empty"}

    B = float(np.median(b))
    S = float(np.median(s))
    F = float(np.percentile(np.array(f), 90)) if f else 0.0  # stabilnije od max

    score = 0.25 * B + 0.25 * S + 0.50 * F
    return float(np.clip(score, 0.0, 1.0)), {"brightness": B, "sharpness": S, "face": F}