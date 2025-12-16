from __future__ import annotations

import os
import json
import hashlib
import hmac
import secrets
import fcntl
import time
from pathlib import Path
from typing import Optional, Set, Dict, Callable

# Paths 
BASE_DIR = Path(".")
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "secure_config.json"

VALID_PINS_FILE = DATA_DIR / "valid_registration_pins.txt"  # one-time registration PINs (HMAC)
PIN_TO_ID_FILE = DATA_DIR / "pin_to_id_map.txt"             # user PIN -> ID (PBKDF2)
ID_TRACK_FILE  = DATA_DIR / "used_ids.txt"                  # list of occupied IDs (plain ints)

# File-system security helpers 
def _umask_secure() -> None:
    os.umask(0o077)

def _ensure_dir_secure(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    try: os.chmod(p, 0o700)
    except PermissionError: pass

def _ensure_file_secure(p: Path) -> None:
    if not p.exists():
        p.touch()
    try: os.chmod(p, 0o600)
    except PermissionError: pass

def _atomic_append(p: Path, line: str) -> None:
    _ensure_dir_secure(p.parent)
    fd = os.open(str(p), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    f = os.fdopen(fd, "a", buffering=1)
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
    _ensure_file_secure(p)

def _locked_read_modify_write(p: Path, modifier: Callable[[list[str]], list[str]]) -> None:
    _ensure_dir_secure(p.parent)
    with open(p, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            lines = f.readlines()
            new_lines = modifier(lines)
            f.seek(0); f.truncate(0); f.writelines(new_lines)
            f.flush(); os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    _ensure_file_secure(p)

# Config (admin + secrets) 
DEFAULT_CONFIG: Dict[str, object] = {
    "username": "admin",
    "password_hash": hashlib.sha256("admin".encode()).hexdigest(),
    "pepper": None,         # hex string
    "kdf_iters": 200_000,   # PBKDF2 iterations
}

def _write_cfg(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)
    try: os.chmod(CONFIG_FILE, 0o600)
    except PermissionError: pass

def ensure_config_exists() -> None:
    _ensure_dir_secure(BASE_DIR)
    if not CONFIG_FILE.exists():
        cfg = DEFAULT_CONFIG.copy()
        cfg["pepper"] = secrets.token_hex(32)  # 256-bit
        _write_cfg(cfg)
    else:
        # ensure pepper/kdf_iters fields exist
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        changed = False
        if "pepper" not in cfg or not cfg["pepper"]:
            cfg["pepper"] = secrets.token_hex(32); changed = True
        if "kdf_iters" not in cfg:
            cfg["kdf_iters"] = DEFAULT_CONFIG["kdf_iters"]; changed = True
        if "username" not in cfg:
            cfg["username"] = DEFAULT_CONFIG["username"]; changed = True
        if "password_hash" not in cfg:
            cfg["password_hash"] = DEFAULT_CONFIG["password_hash"]; changed = True
        if changed:
            _write_cfg(cfg)

def load_config() -> dict:
    ensure_config_exists()
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(data: dict) -> None:
    ensure_config_exists()
    _write_cfg(data)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# Crypto helpers 
def _pepper_bytes() -> bytes:
    cfg = load_config()
    return bytes.fromhex(cfg["pepper"])  

def hmac_pin(pin: str) -> str:
    return hmac.new(_pepper_bytes(), pin.encode(), hashlib.sha256).hexdigest()

def _pbkdf2_pin(pin: str, salt_hex: str) -> str:
    cfg = load_config()
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), _pepper_bytes() + salt, int(cfg["kdf_iters"]), dklen=32
    )
    return dk.hex()

# Initialize secure environment 
def security_init() -> None:
    _umask_secure()
    _ensure_dir_secure(DATA_DIR)
    for f in (VALID_PINS_FILE, PIN_TO_ID_FILE, ID_TRACK_FILE):
        _ensure_file_secure(f)

def get_paths() -> Dict[str, str]:
    return {
        "data_dir": str(DATA_DIR),
        "valid_pins_file": str(VALID_PINS_FILE),
        "pin_to_id_file": str(PIN_TO_ID_FILE),
        "id_track_file": str(ID_TRACK_FILE),
    }

# Registration PINs (one-time, HMAC) 
def generate_registration_pin() -> str:
    existing = _read_hmac_tokens()
    for _ in range(10_000):
        pin = f"{secrets.randbelow(10_000):04d}"
        token = hmac_pin(pin)
        if token not in existing:
            _atomic_append(VALID_PINS_FILE, f"{token}|{_now()}\n")
            return pin
    raise RuntimeError("Unable to generate a PIN.")

def consume_registration_pin(entered_pin: str) -> bool:
    token_hmac = hmac_pin(entered_pin).lower()

    def _mod(lines: list[str]) -> list[str]:
        kept: list[str] = []
        removed = False
        for ln in lines:
            tok = (ln.strip().split("|", 1)[0] or "").lower()
            if tok == token_hmac:
                removed = True
                continue
            kept.append(ln)
        _mod.removed = removed  
        return kept

    _mod.removed = False  
    _locked_read_modify_write(VALID_PINS_FILE, _mod)
    return bool(_mod.removed)  

def _read_hmac_tokens() -> Set[str]:
    tokens: Set[str] = set()
    if VALID_PINS_FILE.exists():
        with open(VALID_PINS_FILE, "r") as f:
            for line in f:
                tok = line.strip().split("|", 1)[0]
                if tok and len(tok) == 64 and all(c in "0123456789abcdefABCDEF" for c in tok):
                    tokens.add(tok.lower())
    return tokens

# User PINs (PBKDF2) 
def is_user_pin_taken(raw_pin: str) -> bool:
    if not PIN_TO_ID_FILE.exists():
        return False
    with open(PIN_TO_ID_FILE, "r") as f:
        for line in f:
            token = line.strip().split("|", 1)[0]
            if token.startswith("p2:"):
                try:
                    _, salt_hex, hash_hex = token.split(":")
                    if _pbkdf2_pin(raw_pin, salt_hex) == hash_hex:
                        return True
                except Exception:
                    continue
    return False

def add_user_pin(user_id: int, raw_pin: str) -> None:
    if not (raw_pin.isdigit() and len(raw_pin) == 4):
        raise ValueError("PIN must be exactly 4 digits.")
    if is_user_pin_taken(raw_pin):
        raise ValueError("That PIN is already taken. Choose a different one.")
    salt_hex = secrets.token_hex(16)
    hash_hex = _pbkdf2_pin(raw_pin, salt_hex)
    token = f"p2:{salt_hex}:{hash_hex}"
    _atomic_append(PIN_TO_ID_FILE, f"{token}|{int(user_id)}|{_now()}\n")

def get_id_for_entered_pin(raw_pin: str) -> Optional[int]:
    if not PIN_TO_ID_FILE.exists():
        return None
    with open(PIN_TO_ID_FILE, "r") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) < 2:
                continue
            token, sid = parts[0], parts[1]
            if not sid.isdigit():
                continue
            if token.startswith("p2:"):
                try:
                    _, salt_hex, hash_hex = token.split(":")
                    if _pbkdf2_pin(raw_pin, salt_hex) == hash_hex:
                        return int(sid)
                except Exception:
                    continue
    return None

def remove_pins_for_id(user_id: int) -> int:
    uid = int(user_id)
    def _mod(lines: list[str]) -> list[str]:
        kept: list[str] = []
        removed = 0
        for ln in lines:
            parts = ln.strip().split("|")
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == uid:
                removed += 1
                continue
            kept.append(ln)
        _mod.removed = removed  
        return kept
    _mod.removed = 0  
    _locked_read_modify_write(PIN_TO_ID_FILE, _mod)
    return int(_mod.removed)  

def wipe_all_user_pins() -> None:
    _locked_read_modify_write(PIN_TO_ID_FILE, lambda _lines: [])

# ID list 
def ids_list() -> Set[int]:
    if not ID_TRACK_FILE.exists():
        return set()
    with open(ID_TRACK_FILE, "r") as f:
        return {int(x.strip()) for x in f if x.strip().isdigit()}

def ids_add(new_id: int) -> None:
    _atomic_append(ID_TRACK_FILE, f"{int(new_id)}\n")

def ids_delete(finger_id: int) -> None:
    fid = int(finger_id)
    def _mod(lines: list[str]) -> list[str]:
        return [ln for ln in lines if ln.strip() != str(fid)]
    _locked_read_modify_write(ID_TRACK_FILE, _mod)

def ids_clear() -> None:
    _locked_read_modify_write(ID_TRACK_FILE, lambda _lines: [])

# Utility 
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
