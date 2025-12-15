import asyncio
import threading

from gui import log_event, start_gui
from lcd.lcd_controller import update_lcd
from keypad.keypad_reader import scan_keys

import camera.camera_module as camera_module
from camera.camera_module import start_camera_recording

# fingerprint modul i API
import fingerprint.fingerprint_sensor as fingerprint_sensor
from fingerprint.fingerprint_sensor import (
    fingerprint_loop,
    enable_registration,
    is_registering,
    set_reset_callback,
    set_input_lock,
    registration_pin_key_input,
)

from config_manager import (
    security_init,
    consume_registration_pin,
    get_id_for_entered_pin,
)

# Inicijalizacija sigurnosnih postavki
security_init()
camera_module.set_camera_logger(log_event)
fingerprint_sensor.set_logger(log_event)


# Globalna stanja unosa PIN-a
pin_buffer = ""
pin_mode = False
input_locked = False


def reset_to_home():
    global pin_buffer, pin_mode
    pin_buffer = ""
    pin_mode = False
    update_lcd("Unesite PIN", "ili skenirajte")


set_reset_callback(reset_to_home)


def lock_input(state: bool):
    global input_locked
    input_locked = state


set_input_lock(lock_input)


def update_pin_display():
    stars = "*" * len(pin_buffer)
    update_lcd("Unos PIN-a:", stars)


def handle_pin_input(key):
    # Tijekom registracije tipke idu u unos korisničkog PIN-a
    if input_locked:
        if is_registering():
            registration_pin_key_input(key)
        return

    global pin_buffer, pin_mode

    if not pin_mode:
        pin_mode = True
        pin_buffer = ""

    if key in "0123456789ABCD":
        # Korisnički PIN-ovi su 1–8 znamenki 0–9
        if len(pin_buffer) < 8:
            pin_buffer += key
            update_pin_display()

    elif key == "*":
        pin_buffer = pin_buffer[:-1]
        update_pin_display()

    elif key == "#":
        raw_entered = pin_buffer  # npr. "0427"

        # 1) REGISTRACIJA: pokušaj “pojesti” jednokratni registracijski PIN
        if raw_entered.isdigit() and len(raw_entered) == 4:
            if consume_registration_pin(raw_entered):
                log_event("Registracijski PIN prihvaćen", "F")
                update_lcd("Reg. PIN prihvaćen", "Započni registraciju")
                enable_registration()
                return

        # 2) ULAZAK: korisnički PIN, tražimo ID
        user_id = get_id_for_entered_pin(raw_entered)
        if user_id is not None:
            update_lcd("Pristup odobren", f"ID {user_id}")
            log_event(f"Pristup odobren korisničkim PIN-om (ID {user_id})", "P")
            try:
                # Namjerno ostavljeno (ako funkcija ne postoji, bit će zalogirano)
                camera_module.notify_recoadgnized_event(user_id)
            except Exception as e:
                log_event(f"Kamera notify_recognized_event error: {e}", "C")
            asyncio.create_task(reset_after_delay())
        else:
            update_lcd("Pristup odbijen", "")
            log_event("Pristup odbijen (pogrešan korisnički PIN)", "P")
            asyncio.create_task(reset_after_delay())


async def reset_after_delay():
    await asyncio.sleep(3)
    reset_to_home()


async def main():
    reset_to_home()
    asyncio.create_task(fingerprint_loop())
    await scan_keys(handle_pin_input)


if __name__ == "__main__":
    # Pokreni GUI i kameru u pozadini
    threading.Thread(target=start_gui, daemon=True).start()
    threading.Thread(target=start_camera_recording, daemon=True).start()

    # Pokreni sigurnosni sustav
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Prekinuto.")