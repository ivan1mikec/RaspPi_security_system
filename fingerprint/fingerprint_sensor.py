import asyncio
import serial
from typing import Callable, Iterable, Optional

from adafruit_fingerprint import Adafruit_Fingerprint
from lcd.lcd_controller import update_lcd
import camera.camera_module as camera_module

# Centralized security and data storage lives in config_manager
from config_manager import (
    is_user_pin_taken,   # check whether a user PIN is already taken
    add_user_pin,        # store a user PIN (PBKDF2+salt+pepper)
    ids_list,            # read the list of IDs
    ids_add,             # add an ID
    ids_delete,          # delete a single ID
    ids_clear,           # delete all IDs
)

# Logger (injected from outside)
log_event: Callable[[str, str], None] = lambda msg, tag='G': None


def set_logger(logger_func: Callable[[str, str], None]) -> None:
    global log_event
    log_event = logger_func


# Status code definitions
FINGERPRINT_OK = 0
FINGERPRINT_NOFINGER = 2
FINGERPRINT_NOTFOUND = 9

# UART and sensor instance
uart = serial.Serial("/dev/ttyAMA0", baudrate=57600, timeout=1)
finger = Adafruit_Fingerprint(uart)

# Correctly set address and password
finger.address = [0xFF, 0xFF, 0xFF, 0xFF]
finger.password = [0x00, 0x00, 0x00, 0x01]

# Working with the ID list (via config_manager)
def load_used_ids() -> Iterable[int]:
    return ids_list()


def save_used_id(new_id: int) -> None:
    ids_add(int(new_id))


def delete_used_id(finger_id: int) -> None:
    finger.delete_model(int(finger_id))
    ids_delete(int(finger_id))


def clear_used_ids() -> None:
    ids_clear()


def delete_all_fingerprints() -> None:
    if finger.verify_password():
        finger.empty_library()
        ids_clear()
        log_event("All fingerprints removed from the sensor and the local ID list cleared", "F")


# Registration state
register_mode = False
_reset_to_home: Optional[Callable[[], None]] = None
_lock_input: Optional[Callable[[bool], None]] = None

# PIN entry steps (local state)
_pin_capture_active = False        # currently entering a PIN?
_pin_capture_buffer = ""           # current entry
_pin_capture_for_id: Optional[int] = None  # ID we are assigning the PIN to
_pin_confirm_stage = 1             # 1 = first entry, 2 = confirmation
_pin_first_entry = ""              # remembered first entry


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
    log_event("Registration cancelled", "F")
    update_lcd("Registration", "cancelled")
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


# Main recognition loop
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
            log_event(f"Denied ID {user_id}; not in local list", "F")
            update_lcd("Fingerprint not", "approved")
            await asyncio.sleep(2)
            if _reset_to_home:
                _reset_to_home()
            continue

        update_lcd("Access granted", f"ID {user_id}")
        log_event(f"Access granted ID {user_id} (confidence={confidence})", "F")
        try:
            camera_module.notify_recognized_event(user_id)
        except Exception as e:
            log_event(f"Camera notify_recognized_event error: {e}", "C")

        if _lock_input:
            _lock_input(True)
        await asyncio.sleep(2)

        while finger.get_image() != FINGERPRINT_NOFINGER:
            await asyncio.sleep(0.1)

        if _reset_to_home:
            _reset_to_home()
        if _lock_input:
            _lock_input(False)


# Registration + user PIN entry (exactly 4 digits)
async def registration_blocking_loop() -> None:
    global register_mode, _pin_capture_active, _pin_capture_for_id
    global _pin_confirm_stage, _pin_first_entry, _pin_capture_buffer

    # 1) finger capture
    update_lcd("Place finger", "to enroll")
    log_event("Waiting for finger to enroll", "F")

    while register_mode:
        if finger.get_image() == FINGERPRINT_OK:
            break
        await asyncio.sleep(0.1)
    if not register_mode:
        return

    if finger.image_2_tz(1) != FINGERPRINT_OK:
        update_lcd("Error", "first image")
        await asyncio.sleep(2)
        cancel_registration()
        return

    update_lcd("Remove finger", "")
    while finger.get_image() != FINGERPRINT_NOFINGER:
        await asyncio.sleep(0.1)

    update_lcd("Place again", "")
    log_event("Waiting for second print", "F")

    while register_mode:
        if finger.get_image() == FINGERPRINT_OK:
            break
        await asyncio.sleep(0.1)
    if not register_mode:
        return

    if finger.image_2_tz(2) != FINGERPRINT_OK:
        update_lcd("Unsuccessful", "try again")
        await asyncio.sleep(2)
        cancel_registration()
        return

    if finger.create_model() != FINGERPRINT_OK:
        update_lcd("Error", "modeling")
        await asyncio.sleep(2)
        cancel_registration()
        return

    used_ids = load_used_ids()
    location = next((i for i in range(1, 127) if i not in used_ids), None)
    if location is None:
        update_lcd("Error", "no slots")
        log_event("No free IDs according to local list", "F")
        cancel_registration()
        return

    if finger.store_model(location) != FINGERPRINT_OK:
        update_lcd("Error", "saving")
        await asyncio.sleep(2)
        cancel_registration()
        return

    save_used_id(location)
    update_lcd("Finger enrolled", f"ID {location}")
    log_event(f"New ID {location} enrolled", "F")
    await asyncio.sleep(1)

    # 2) PERSONAL PIN ENTRY + CONFIRMATION (EXACTLY 4) 
    _pin_capture_for_id = location
    _pin_capture_active = True
    _pin_confirm_stage = 1
    _pin_first_entry = ""
    _pin_capture_buffer = ""
    _show_pin_prompt()

    # Wait for user to finish entry (registration_pin_key_input clears _pin_capture_active)
    while register_mode and _pin_capture_active:
        await asyncio.sleep(0.1)

    if not register_mode:
        # cancelled during entry
        _pin_capture_reset()
        return

    # PIN saved - finish
    update_lcd("Registration OK", f"ID {location}")
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
        stars = stars[-16:]  # LCD second line max 16 chars
    header = "Enter PIN (4)" if _pin_confirm_stage == 1 else "Confirm PIN"
    update_lcd(header, f"digits: {stars}")


def registration_pin_key_input(key: str) -> None:
    global _pin_capture_buffer, _pin_capture_active, _pin_confirm_stage, _pin_first_entry

    if not (register_mode and _pin_capture_active):
        return  # ignore if not in PIN entry

    if key in "0123456789":
        if len(_pin_capture_buffer) < 4:
            _pin_capture_buffer += key
        _show_pin_prompt()

    elif key == "*":
        _pin_capture_buffer = _pin_capture_buffer[:-1]
        _show_pin_prompt()

    elif key == "#":
        # Confirm step - must be exactly 4 digits
        if len(_pin_capture_buffer) != 4:
            update_lcd("PIN must be", "exactly 4 digits")
            asyncio.create_task(_flash_and_prompt_again())
            return

        if _pin_confirm_stage == 1:
            # Store first entry and ask for confirmation
            _pin_first_entry = _pin_capture_buffer
            _pin_capture_buffer = ""
            _pin_confirm_stage = 2
            update_lcd("Confirm PIN", "enter again")
            asyncio.create_task(_flash_and_prompt_again())
            return

        # _pin_confirm_stage == 2
        if _pin_capture_buffer != _pin_first_entry:
            # Mismatch - restart
            update_lcd("Does not match", "Try again")
            log_event("PIN confirmation failed (mismatch)", "F")
            _pin_capture_buffer = ""
            _pin_first_entry = ""
            _pin_confirm_stage = 1
            asyncio.create_task(_flash_and_prompt_again())
            return

        # Matches - check availability then store
        try:
            if is_user_pin_taken(_pin_capture_buffer):
                update_lcd("PIN taken", "Choose another")
                log_event("Entered personal PIN is already in use", "F")
                # reset PIN entry process (back to step 1)
                _pin_capture_buffer = ""
                _pin_first_entry = ""
                _pin_confirm_stage = 1
                asyncio.create_task(_flash_and_prompt_again())
                return

            # securely store PIN for ID (PBKDF2 + salt + pepper)
            add_user_pin(_pin_capture_for_id, _pin_capture_buffer)
            log_event(f"Personal PIN stored for ID {_pin_capture_for_id}", "F")
            _pin_capture_active = False  # signal to the registration loop that we are done

        except ValueError as e:
            update_lcd("Invalid PIN", "exactly 4 digits (0-9)")
            log_event(f"Error saving PIN: {e}", "F")
            # back to step 1
            _pin_capture_buffer = ""
            _pin_first_entry = ""
            _pin_confirm_stage = 1
            asyncio.create_task(_flash_and_prompt_again())


async def _flash_and_prompt_again() -> None:
    await asyncio.sleep(1.2)
    _show_pin_prompt()


# Helper operations on the sensor
def get_registered_ids():
    if not finger.verify_password() or not finger.read_templates():
        log_event("Failure: get_registered_ids() -> auth/read_templates", "F")
        return []
    return finger.templates


def delete_fingerprint(finger_id: int) -> bool:
    if not finger.verify_password():
        log_event("Failure: delete_fingerprint() -> password", "F")
        return False
    if finger.delete_model(int(finger_id)) == FINGERPRINT_OK:
        ids_delete(int(finger_id))
        log_event(f"Fingerprint ID {finger_id} deleted", "F")
        return True
    else:
        log_event(f"Failed to delete fingerprint ID {finger_id}", "F")
        return False
