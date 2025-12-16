from gpiozero import OutputDevice, Button
from signal import pause
import asyncio

# Key layout
KEYPAD = [
    ["D", "C", "B", "A"],
    ["#", "9", "6", "3"],
    ["0", "8", "5", "2"],
    ["*", "7", "4", "1"]
]


# GPIO pins (BCM)
ROW_PINS = [4, 17, 27, 22]    # R1-R4 as outputs
COL_PINS = [5, 6, 13, 19]     # C1-C4 as inputs with pull-down

# Initialize rows (output, HIGH/LOW)
rows = [OutputDevice(pin, active_high=True, initial_value=False) for pin in ROW_PINS]

# Initialize columns (input with pull-down)
cols = [Button(pin, pull_up=False, bounce_time=0.05) for pin in COL_PINS]

# Main keypad scanning function
async def scan_keys(callback):
    while True:
        for row_index, row in enumerate(rows):
            row.on()  # drive row HIGH
            for col_index, col in enumerate(cols):
                if col.is_pressed:
                    key = KEYPAD[row_index][col_index]
                    callback(key)
                    await asyncio.sleep(0.3)  # debounce
            row.off()  # drive row back LOW
        await asyncio.sleep(0.01)  # short pause for CPU
