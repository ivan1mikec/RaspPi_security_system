# Security System (fingerprint + PIN + camera)

Local Raspberry Pi security system with fingerprint identification, keypad entry, and a camera that records recognized/unrecognized events and shows a preview in a Tkinter GUI.

## Architecture
- `main.py`: starts the GUI, camera, LCD, and keypad; handles PIN entry.
- `fingerprint/`: Adafruit Fingerprint sensor handling, user enrollment, and PIN storage.
- `camera/`: Picamera2 + OpenCV, recording recognized and unrecognized events, video quality assessment.
- `keypad/`: matrix keypad reading (gpiozero).
- `lcd/`: I2C LCD control.
- `config_manager.py`: secure storage of registration PINs and user PINs (HMAC/PBKDF2 + pepper), ID tracking.
- `progressive_enroll.py`: tracks per-user video collection progress.
- `gui.py`: Tkinter admin interface for logs and camera preview.
- `data/`, `logs/`, `recordings/`: runtime artifacts (git-ignored).

## Hardware
- Raspberry Pi with camera (Picamera2 driver), I2C LCD (PCF8574), matrix keypad, Adafruit fingerprint sensor on `/dev/ttyAMA0`.

## Software / dependencies
- Python 3.11+ on Raspberry Pi OS.
- System packages: `sudo apt install python3-picamera2 python3-opencv libatlas-base-dev python3-tk` (tkinter, opencv, picamera2).
- Python packages: see `requirements.txt` (`pip install -r requirements.txt`).

## Installation (run from repo root)
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Run
```bash
python main.py
```
- GUI prompts for admin username/password (default admin/admin; change in `secure_config.json`).
- Camera and GUI start in the background; LCD shows PIN entry status.
- Recordings are saved in `recordings/recognized` and `recordings/unrecognized` (git-ignored).

## Security notes
- `secure_config.json` is generated automatically and contains the pepper and admin hash -> do not commit (already in .gitignore).
- `data/`, `logs/`, `recordings/` contain sensitive and/or large files -> ignored. Leave empty directories with `.gitkeep`.
- On a new device, run the app once to create config/files.

## Directory structure
security_system/
  main.py
  gui.py
  config_manager.py
  progressive_enroll.py
  camera/
    camera_module.py
    video_quality.py
  fingerprint/
    fingerprint_sensor.py
  keypad/
    keypad_reader.py
  lcd/
    lcd_controller.py
  data/              # runtime PIN map, registration tokens (ignored)
  logs/              # log_*.txt (ignored)
  recordings/        # recognized/unrecognized .avi (ignored)

## Known setup tips
- If Picamera2 cannot be imported from the virtual environment, install it system-wide (`apt`) and run the script with the system Python.
- OpenCV can be heavy on slower Pis; lower resolutions in `camera/camera_module.py` if needed.
- For keypad and LCD, verify BCM pins and the I2C address (PCF8574) before use.
