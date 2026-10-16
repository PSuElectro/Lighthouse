from machine import Pin
import time
import sh1106
import uasyncio as asyncio
from rotary_irq_rp2 import RotaryIRQ
from qwiic_i2c.micropython_i2c import MicroPythonI2C


# -------------------------------------------------
# Pins
# -------------------------------------------------
GPIO_I2C_SDA      = 4
GPIO_I2C_SCL      = 5
CLK_PIN = 13
DT_PIN = 14
SW_PIN = 16
GPIO_BUTTON1      = 17
GPIO_BUTTON2      = 18
GPIO_BUTTON3      = 19
button1_pressed = False
button2_pressed = False
button3_pressed = False
last_knob_press=0
last_button1_press=0
last_button2_press=0
last_button3_press=0

# Create OLED display
i2c = MicroPythonI2C(sda=GPIO_I2C_SDA, scl=GPIO_I2C_SCL)
oled = sh1106.SH1106_I2C(
        128, 64,
        i2c._i2cbus,
        None,
        0x3c,
        rotate=180
    )

# -------------------------------------------------
# Rotary
# -------------------------------------------------

rotary = RotaryIRQ(
    pin_num_clk=CLK_PIN,
    pin_num_dt=DT_PIN,
    min_val=0,
    max_val=23,
    incr=1,
    reverse=False,
    range_mode=RotaryIRQ.RANGE_BOUNDED,
    pull_up=True,
    half_step=False
)


# -------------------------------------------------
# Button
# -------------------------------------------------

button = Pin(SW_PIN, Pin.IN, Pin.PULL_UP)

last_button = 1


#Create Buttons 1-3
def button1_handler(pin):
    global button1_pressed, last_button1_press

    now = time.ticks_ms()

    if time.ticks_diff(now, last_button1_press) < 300:
        return

    last_button1_press = now
    print("button1 pressed")
    button1_pressed = True
    
button1 = Pin(GPIO_BUTTON1, Pin.IN, Pin.PULL_UP)
button1.irq(
    trigger=Pin.IRQ_FALLING,
    handler=button1_handler
)

def button2_handler(pin):
    global button2_pressed, last_button2_press

    now = time.ticks_ms()

    if time.ticks_diff(now, last_button2_press) < 300:
        return

    last_button2_press = now
    print("button2 pressed")
    button2_pressed = True
    
button2 = Pin(GPIO_BUTTON2, Pin.IN, Pin.PULL_UP)
button2.irq(
    trigger=Pin.IRQ_FALLING,
    handler=button2_handler
)

def button3_handler(pin):
    global button3_pressed, last_button3_press

    now = time.ticks_ms()

    if time.ticks_diff(now, last_button3_press) < 300:
        return

    last_button3_press = now
    print("button3 pressed")
    button3_pressed = True
    
button3 = Pin(GPIO_BUTTON3, Pin.IN, Pin.PULL_UP)
button3.irq(
    trigger=Pin.IRQ_FALLING,
    handler=button3_handler
)

# End buttons 1-3 creation


# -------------------------------------------------
# Get current time
# -------------------------------------------------

# now = time.localtime()
# 
# hour = now[3]
# minute = now[4]
# 
# print()
# print("==============================")
# print(" Rotary time setting test")
# print("==============================")
# print()
# print("Pico time:")
# print("Hour:", hour)
# print("Minute:", minute)
# print()


# Start by editing hours
# rotary.set(
#     value=hour,
#     min_val=0,
#     max_val=23,
#     incr=1,
#     range_mode=RotaryIRQ.RANGE_WRAP
# )

# print("=== SET HOURS ===")
# print("Rotate to change hour")
# print("Press button to accept")
# print("Current hour:", rotary.value())


# -------------------------------------------------
# Main loop
# -------------------------------------------------

# state = "hour"
# 
# while True:
# 
#     # Show rotary value if it has changed
#     value = rotary.value()
# 
#     if state == "hour":
#         if value != hour:
#             hour = value
#             print("Hour:", hour)
# 
#     elif state == "minute":
#         if value != minute:
#             minute = value
#             print("Minute:", minute)
# 
#     # ---------------------------------------------
#     # Button handling
#     # ---------------------------------------------
# 
#     button_value = button.value()
# 
#     # Falling edge = button pressed
#     if last_button == 1 and button_value == 0:
# 
#         if state == "hour":
# 
#             # Store selected hour
#             hour = rotary.value()
# 
#             print()
#             print("Hour accepted:", hour)
#             print()
# 
#             # Change rotary to minute mode
#             rotary.set(
#                 value=minute,
#                 min_val=0,
#                 max_val=59,
#                 incr=1,
#                 range_mode=RotaryIRQ.RANGE_WRAP
#             )
# 
#             state = "minute"
# 
#             print("=== SET MINUTES ===")
#             print("Rotate to change minute")
#             print("Press button to accept")
#             print("Current minute:", minute)
# 
#         elif state == "minute":
# 
#             # Store selected minute
#             minute = rotary.value()
# 
#             print()
#             print("Minute accepted:", minute)
#             print()
#             print("==============================")
#             print(" FINAL TIME")
#             print("==============================")
#             print("Hour:   ", hour)
#             print("Minute: ", minute)
#             print("==============================")
#             print()
# 
#             print("Test finished.")
#             break
# 
#         # Wait until button is released
#         while button.value() == 0:
#             time.sleep_ms(10)
# 
#     last_button = button_value
# 
#     time.sleep_ms(10)
    
def main():
    global rotary
    rotary.set(
    value=12,
    min_val=0,
    max_val=23,
    incr=1,
    range_mode=RotaryIRQ.RANGE_WRAP
    )

#     while True:
#         selected = rotary.value()
#         print("Rotary:", selected)
# 
#          oled.fill(0)
#          oled.text("Rotary test", 0, 0)
#          oled.text("Value: " + str(selected), 0, 20)
#          oled.show()
#         time.sleep_ms(100)
    last_value=None
    while True:
        value = rotary.value()
        if value != last_value:
            print("Rotary:", value)
            last_value = value
            oled.fill(0)
            oled.text("Rotary test", 0, 0)
            oled.text("Value: " + str(value), 0, 20)
            oled.show()

        time.sleep_ms(10)        
        
main()