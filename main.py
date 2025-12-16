import asyncio
import threading

from gui import log_event, start_gui
from lcd.lcd_controller import update_lcd
from keypad.keypad_reader import scan_keys

import camera.camera_module as camera_module
from camera.camera_module import start_camera_recording

# fingerprint module and API
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

# Initialize security settings
security_init()
camera_module.set_camera_logger(log_event)
fingerprint_sensor.set_logger(log_event)


# Global PIN entry state
pin_buffer = ""
pin_mode = False
input_locked = False


def reset_to_home():
    global pin_buffer, pin_mode
    pin_buffer = ""
    pin_mode = False
    update_lcd("Enter PIN", "or scan fingerprint")


set_reset_callback(reset_to_home)


def lock_input(state: bool):
    global input_locked
    input_locked = state


set_input_lock(lock_input)


def update_pin_display():
    stars = "*" * len(pin_buffer)
    update_lcd("PIN entry:", stars)


def handle_pin_input(key):
    # During registration, keys are routed to user PIN entry
    if input_locked:
        if is_registering():
            registration_pin_key_input(key)
        return

    global pin_buffer, pin_mode

    if not pin_mode:
        pin_mode = True
        pin_buffer = ""

    if key in "0123456789ABCD":
        # User PINs are 1-8 digits 0-9
        if len(pin_buffer) < 8:
            pin_buffer += key
            update_pin_display()

    elif key == "*":
        pin_buffer = pin_buffer[:-1]
        update_pin_display()

    elif key == "#":
        raw_entered = pin_buffer  # e.g., "0427"

        # 1) REGISTRATION: try to consume a one-time registration PIN
        if raw_entered.isdigit() and len(raw_entered) == 4:
            if consume_registration_pin(raw_entered):
                log_event("Registration PIN accepted", "F")
                update_lcd("Reg PIN accepted", "Start enrollment")
                enable_registration()
                return

        # 2) ENTRY: user PIN, look up ID
        user_id = get_id_for_entered_pin(raw_entered)
        if user_id is not None:
            update_lcd("Access granted", f"ID {user_id}")
            log_event(f"Access granted by user PIN (ID {user_id})", "P")
            try:
                # Intentionally left (if function missing, it will be logged)
                camera_module.notify_recoadgnized_event(user_id)
            except Exception as e:
                log_event(f"Camera notify_recognized_event error: {e}", "C")
            asyncio.create_task(reset_after_delay())
        else:
            update_lcd("Access denied", "")
            log_event("Access denied (wrong user PIN)", "P")
            asyncio.create_task(reset_after_delay())


async def reset_after_delay():
    await asyncio.sleep(3)
    reset_to_home()


async def main():
    reset_to_home()
    asyncio.create_task(fingerprint_loop())
    await scan_keys(handle_pin_input)


if __name__ == "__main__":
    # Start GUI and camera in the background
    threading.Thread(target=start_gui, daemon=True).start()
    threading.Thread(target=start_camera_recording, daemon=True).start()

    # Start the security system
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
