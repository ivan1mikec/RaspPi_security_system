import asyncio
import serial
from typing import Callable, Iterable, Optional

from adafruit_fingerprint import Adafruit_Fingerprint
from lcd.lcd_controller import update_lcd
import camera.camera_module as camera_module

# Centralizirana sigurnost i spremanje podataka u config_manageru
from config_manager import (
    is_user_pin_taken,   # provjera zauzetosti korisničkog PIN-a
    add_user_pin,        # spremanje korisničkog PIN-a (PBKDF2+salt+pepper)
    ids_list,            # čitanje liste ID-jeva
    ids_add,             # dodavanje ID-ja
    ids_delete,          # brisanje jednog ID-ja
    ids_clear,           # brisanje svih ID-jeva
)

# ---------------------------
# Logger (injicira se izvana)
# ---------------------------
log_event: Callable[[str, str], None] = lambda msg, tag='G': None


def set_logger(logger_func: Callable[[str, str], None]) -> None:
    """Postavi vanjsku log funkciju (npr. gui.log_event)."""
    global log_event
    log_event = logger_func


# ---------------------------
# Definicije statusnih kodova
# ---------------------------
FINGERPRINT_OK = 0
FINGERPRINT_NOFINGER = 2
FINGERPRINT_NOTFOUND = 9

# ---------------------------
# UART i instanca senzora
# ---------------------------
uart = serial.Serial("/dev/ttyAMA0", baudrate=57600, timeout=1)
finger = Adafruit_Fingerprint(uart)

# Ispravno postavljanje adrese i lozinke
finger.address = [0xFF, 0xFF, 0xFF, 0xFF]
finger.password = [0x00, 0x00, 0x00, 0x01]

# ---------------------------
# Rad s ID listom (preko config_managera)
# ---------------------------
def load_used_ids() -> Iterable[int]:
    return ids_list()


def save_used_id(new_id: int) -> None:
    ids_add(int(new_id))


def delete_used_id(finger_id: int) -> None:
    """Prvo briše s uređaja, zatim iz lokalne liste."""
    finger.delete_model(int(finger_id))
    ids_delete(int(finger_id))


def clear_used_ids() -> None:
    ids_clear()


def delete_all_fingerprints() -> None:
    if finger.verify_password():
        finger.empty_library()
        ids_clear()
        log_event("Svi otisci obrisani sa senzora i lokalna lista ID-jeva očišćena", "F")


# ---------------------------
# Stanja registracije
# ---------------------------
register_mode = False
_reset_to_home: Optional[Callable[[], None]] = None
_lock_input: Optional[Callable[[bool], None]] = None

# Koraci registracije PIN-a (lokalni state)
_pin_capture_active = False        # jesmo li u fazi unosa PIN-a
_pin_capture_buffer = ""           # trenutni unos
_pin_capture_for_id: Optional[int] = None  # ID kojem pridružujemo PIN
_pin_confirm_stage = 1             # 1 = prvi unos, 2 = potvrda
_pin_first_entry = ""              # zapamćeni prvi unos


def set_reset_callback(func: Callable[[], None]) -> None:
    global _reset_to_home
    _reset_to_home = func


def set_input_lock(lock_func: Callable[[bool], None]) -> None:
    global _lock_input
    _lock_input = lock_func


def _pin_capture_reset() -> None:
    global _pin_capture_active, _pin_capture_buffer, _pin_capture_for_id
    global _pin_confirm_stage, _pin_first_entry
    _pin_capture_active = False
    _pin_capture_buffer = ""
    _pin_capture_for_id = None
    _pin_confirm_stage = 1
    _pin_first_entry = ""


def cancel_registration() -> None:
    global register_mode
    register_mode = False
    _pin_capture_reset()
    log_event("Registracija otkazana", "F")
    update_lcd("Registracija", "otkazana")
    if _reset_to_home:
        _reset_to_home()
    if _lock_input:
        _lock_input(False)


def enable_registration() -> None:
    global register_mode
    if not register_mode:
        register_mode = True
        if _lock_input:
            _lock_input(True)
        asyncio.create_task(registration_blocking_loop())


def is_registering() -> bool:
    return register_mode


# ---------------------------
# Glavna petlja prepoznavanja
# ---------------------------
async def fingerprint_loop() -> None:
    while True:
        await asyncio.sleep(0.1)

        if register_mode:
            continue

        if finger.get_image() != FINGERPRINT_OK:
            continue

        if finger.image_2_tz(1) != FINGERPRINT_OK:
            continue

        if finger.finger_search() != FINGERPRINT_OK:
            continue

        user_id = finger.finger_id
        confidence = finger.confidence

        if user_id not in load_used_ids():
            log_event(f"Odbijen ID {user_id} nije u lokalnoj listi", "F")
            update_lcd("Otisak nije", "odobren")
            await asyncio.sleep(2)
            if _reset_to_home:
                _reset_to_home()
            continue

        update_lcd("Pristup odobren", f"ID {user_id}")
        log_event(f"Pristup odobren ID {user_id} (confidence={confidence})", "F")
        try:
            camera_module.notify_recognized_event(user_id)
        except Exception as e:
            log_event(f"Kamera notify_recognized_event error: {e}", "C")

        if _lock_input:
            _lock_input(True)
        await asyncio.sleep(2)

        while finger.get_image() != FINGERPRINT_NOFINGER:
            await asyncio.sleep(0.1)

        if _reset_to_home:
            _reset_to_home()
        if _lock_input:
            _lock_input(False)


# ---------------------------
# Registracija + unos korisničkog PIN-a (točno 4 znamenke)
# ---------------------------
async def registration_blocking_loop() -> None:
    """
    1) Snimi prst (2 uzorka, model + store)
    2) Zatraži unos osobnog PIN-a (TOČNO 4 znamenke, 0–9) i POTVRDU:
       - '*' brisanje znaka
       - '#' potvrda koraka
       PIN se sprema kroz config_manager (PBKDF2+salt+pepper).
    """
    global register_mode, _pin_capture_active, _pin_capture_for_id
    global _pin_confirm_stage, _pin_first_entry, _pin_capture_buffer

    # --- 1) SNIMANJE PRSTA ---
    update_lcd("Prislonite prst", "za registraciju")
    log_event("Čekanje prsta za registraciju", "F")

    while register_mode:
        if finger.get_image() == FINGERPRINT_OK:
            break
        await asyncio.sleep(0.1)
    if not register_mode:
        return

    if finger.image_2_tz(1) != FINGERPRINT_OK:
        update_lcd("Greska", "prva slika")
        await asyncio.sleep(2)
        cancel_registration()
        return

    update_lcd("Maknite prst", "")
    while finger.get_image() != FINGERPRINT_NOFINGER:
        await asyncio.sleep(0.1)

    update_lcd("Ponovno prst", "")
    log_event("Čekanje drugog otiska", "F")

    while register_mode:
        if finger.get_image() == FINGERPRINT_OK:
            break
        await asyncio.sleep(0.1)
    if not register_mode:
        return

    if finger.image_2_tz(2) != FINGERPRINT_OK:
        update_lcd("Neuspjesno", "ponovite")
        await asyncio.sleep(2)
        cancel_registration()
        return

    if finger.create_model() != FINGERPRINT_OK:
        update_lcd("Greska", "modeliranje")
        await asyncio.sleep(2)
        cancel_registration()
        return

    used_ids = load_used_ids()
    location = next((i for i in range(1, 127) if i not in used_ids), None)
    if location is None:
        update_lcd("Greska", "nema mjesta")
        log_event("Nema slobodnih ID-jeva prema lokalnoj listi", "F")
        cancel_registration()
        return

    if finger.store_model(location) != FINGERPRINT_OK:
        update_lcd("Greska", "spremanje")
        await asyncio.sleep(2)
        cancel_registration()
        return

    save_used_id(location)
    update_lcd("Prst registriran", f"ID {location}")
    log_event(f"Registriran novi ID {location}", "F")
    await asyncio.sleep(1)

    # --- 2) UNOS OSOBNOG PIN-a + POTVRDA (TOČNO 4) ---
    _pin_capture_for_id = location
    _pin_capture_active = True
    _pin_confirm_stage = 1
    _pin_first_entry = ""
    _pin_capture_buffer = ""
    _show_pin_prompt()

    # Čekaj da korisnik završi unos (registration_pin_key_input gasi _pin_capture_active)
    while register_mode and _pin_capture_active:
        await asyncio.sleep(0.1)

    if not register_mode:
        # otkazano usred unosa
        _pin_capture_reset()
        return

    # PIN sačuvan — završetak
    update_lcd("Registracija OK", f"ID {location}")
    await asyncio.sleep(2)

    register_mode = False
    _pin_capture_reset()
    if _reset_to_home:
        _reset_to_home()
    if _lock_input:
        _lock_input(False)


def _show_pin_prompt(mask: bool = True) -> None:
    stars = "*" * len(_pin_capture_buffer) if mask else _pin_capture_buffer
    if len(stars) > 16:
        stars = stars[-16:]  # LCD druga linija max 16 znakova
    header = "Unesite PIN (4)" if _pin_confirm_stage == 1 else "Potvrdite PIN"
    update_lcd(header, f"znamenke: {stars}")


def registration_pin_key_input(key: str) -> None:
    """
    Poziva se iz main.py za svaku tipku tijekom registracije.
    Dozvoljene tipke: '0'-'9', '*', '#'
    """
    global _pin_capture_buffer, _pin_capture_active, _pin_confirm_stage, _pin_first_entry

    if not (register_mode and _pin_capture_active):
        return  # ignoriraj ako nismo u traženju PIN-a

    if key in "0123456789":
        if len(_pin_capture_buffer) < 4:
            _pin_capture_buffer += key
        _show_pin_prompt()

    elif key == "*":
        _pin_capture_buffer = _pin_capture_buffer[:-1]
        _show_pin_prompt()

    elif key == "#":
        # Potvrda koraka — mora biti točno 4 znamenke
        if len(_pin_capture_buffer) != 4:
            update_lcd("PIN mora biti", "tocno 4 znamenke")
            asyncio.create_task(_flash_and_prompt_again())
            return

        if _pin_confirm_stage == 1:
            # Spremi prvi unos i traži potvrdu
            _pin_first_entry = _pin_capture_buffer
            _pin_capture_buffer = ""
            _pin_confirm_stage = 2
            update_lcd("Potvrdite PIN", "ponovno unesite")
            asyncio.create_task(_flash_and_prompt_again())
            return

        # _pin_confirm_stage == 2
        if _pin_capture_buffer != _pin_first_entry:
            # Ne podudara se — ispočetka
            update_lcd("Ne podudara se", "Pokusajte ponovno")
            log_event("PIN potvrda neuspješna (neslaganje)", "F")
            _pin_capture_buffer = ""
            _pin_first_entry = ""
            _pin_confirm_stage = 1
            asyncio.create_task(_flash_and_prompt_again())
            return

        # Podudara se — provjeri zauzetost pa spremi
        try:
            if is_user_pin_taken(_pin_capture_buffer):
                update_lcd("PIN zauzet", "Odaberite drugi")
                log_event("Uneseni osobni PIN je zauzet", "F")
                # resetiraj cijeli proces unosa PIN-a (natrag na korak 1)
                _pin_capture_buffer = ""
                _pin_first_entry = ""
                _pin_confirm_stage = 1
                asyncio.create_task(_flash_and_prompt_again())
                return

            # sigurno spremanje PIN-a za ID (PBKDF2 + salt + pepper)
            add_user_pin(_pin_capture_for_id, _pin_capture_buffer)
            log_event(f"Spremljen osobni PIN za ID {_pin_capture_for_id}", "F")
            _pin_capture_active = False  # signal registracijskom loopu da je gotovo

        except ValueError as e:
            update_lcd("Neispravan PIN", "točno 4 znamenke (0–9)")
            log_event(f"Greška pri spremanju PIN-a: {e}", "F")
            # natrag na korak 1
            _pin_capture_buffer = ""
            _pin_first_entry = ""
            _pin_confirm_stage = 1
            asyncio.create_task(_flash_and_prompt_again())


async def _flash_and_prompt_again() -> None:
    await asyncio.sleep(1.2)
    _show_pin_prompt()


# ---------------------------
# Pomoćne operacije nad senzorom
# ---------------------------
def get_registered_ids():
    if not finger.verify_password() or not finger.read_templates():
        log_event("Neuspjeh: get_registered_ids() – auth/read_templates", "F")
        return []
    return finger.templates


def delete_fingerprint(finger_id: int) -> bool:
    if not finger.verify_password():
        log_event("Neuspjeh: delete_fingerprint() – lozinka", "F")
        return False
    if finger.delete_model(int(finger_id)) == FINGERPRINT_OK:
        ids_delete(int(finger_id))
        log_event(f"Otisak ID {finger_id} obrisan", "F")
        return True
    else:
        log_event(f"Neuspješno brisanje otiska ID {finger_id}", "F")
        return False