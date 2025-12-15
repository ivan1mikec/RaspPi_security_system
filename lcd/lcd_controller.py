from RPLCD.i2c import CharLCD
from time import sleep

lcd = CharLCD('PCF8574', address=0x27, port=1, cols=16, rows=2)



def update_lcd(line1='', line2=''):
    lcd.clear()
    lcd.write_string(line1[:16])
    lcd.crlf()
    lcd.write_string(line2[:16])
