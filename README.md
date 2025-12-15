# Security System (fingerprint + PIN + camera)

Diplomski projekt: lokalni sigurnosni sustav na Raspberry Pi s identifikacijom otiskom prsta, PIN tipkovnicom i kamerom (snima recognized/unrecognized dogadjaje i prikazuje preview u Tkinter GUI-u).

## Arhitektura
- `main.py`: pokretanje GUI-a, kamere, LCD-a i tipkovnice, obrada PIN unosa.
- `fingerprint/`: rad sa senzorom otiska (Adafruit Fingerprint), registracija korisnika i PIN-a.
- `camera/`: Picamera2 + OpenCV, snimanje prepoznatih i neprepoznatih dogadjaja, procjena kvalitete videa.
- `keypad/`: citanje matricne tipkovnice (gpiozero).
- `lcd/`: upravljanje I2C LCD-om.
- `config_manager.py`: sigurno spremanje registracijskih PIN-ova i korisnickih PIN-ova (HMAC/PBKDF2 + pepper), ID lista.
- `progressive_enroll.py`: pracenje napretka prikupljanja videa po korisniku.
- `gui.py`: Tkinter sucelje za administraciju, logove i preview kamere.
- `data/`, `logs/`, `recordings/`: runtime artefakti (ignorirani u git-u).

## Hardver
- Raspberry Pi s kamerom (Picamera2 driver), I2C LCD (PCF8574), matricna tipkovnica, Adafruit fingerprint senzor na /dev/ttyAMA0.

## Softver / ovisnosti
- Python 3.11+ na Raspberry Pi OS-u.
- Sistemski paketi: `sudo apt install python3-picamera2 python3-opencv libatlas-base-dev python3-tk` (tkinter, opencv, picamera2).
- Python paketi: vidi `requirements.txt` (`pip install -r requirements.txt`).

## Instalacija (preporuceno u repo rootu)
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Pokretanje
```bash
python main.py
```
- GUI ce traziti admin korisnicko ime/lozinku (zadano admin/admin, mijenja se u `secure_config.json`).
- Kamera i GUI se pokrecu u pozadini; LCD prikazuje status PIN unosa.
- Snimke se spremaju u `recordings/recognized` i `recordings/unrecognized` (ignorirano u git-u).

## Sigurnosne napomene
- `secure_config.json` se generira automatski i sadrzi pepper i admin hash -> ne committati (vec u .gitignore).
- `data/`, `logs/`, `recordings/` sadrze osjetljive i/ili velike datoteke -> ignorirano. Ostavi prazne direktorije pomocu `.gitkeep`.
- Ako radis na novom uredjaju, pokreni aplikaciju jednom da se kreiraju config/datoteke.

## Struktura direktorija
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
  data/              # runtime PIN map, registracijski tokeni (ignored)
  logs/              # log_*.txt (ignored)
  recordings/        # recognized/unrecognized .avi (ignored)

## Known setup tips
- Ako se Picamera2 ne moze importati iz virtualnog okruzenja, instaliraj ga sistemski (`apt`) i pokreni skriptu sa sistemskim Pythonom.
- OpenCV moze biti tezak na slabijem Pi-u; smanji rezolucije u `camera/camera_module.py` ako je potrebno.
- Za keypad i LCD provjeri BCM pinove i I2C adresu (PCF8574) prije koristenja.