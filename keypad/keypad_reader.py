from gpiozero import OutputDevice, Button
from signal import pause
import asyncio

# Raspored tipki
KEYPAD = [
    ["D", "C", "B", "A"],
    ["#", "9", "6", "3"],
    ["0", "8", "5", "2"],
    ["*", "7", "4", "1"]
]


# GPIO pinovi (BCM)
ROW_PINS = [4, 17, 27, 22]    # R1-R4 kao output
COL_PINS = [5, 6, 13, 19]     # C1-C4 kao input s pull-down

# Inicijalizacija redova (output, HIGH/LOW)
rows = [OutputDevice(pin, active_high=True, initial_value=False) for pin in ROW_PINS]

# Inicijalizacija stupaca (input s pull-down)
cols = [Button(pin, pull_up=False, bounce_time=0.05) for pin in COL_PINS]

# Glavna funkcija za skeniranje tipkovnice
async def scan_keys(callback):
    while True:
        for row_index, row in enumerate(rows):
            row.on()  # postavi red u HIGH
            for col_index, col in enumerate(cols):
                if col.is_pressed:
                    key = KEYPAD[row_index][col_index]
                    callback(key)
                    await asyncio.sleep(0.3)  # debounce
            row.off()  # postavi red natrag u LOW
        await asyncio.sleep(0.01)  # kratka pauza za CPU
