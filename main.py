#*************************************
#  Lighthouse alarm clock project
#  (C) Petri Suominen 2026
#*************************************

from qwiic_i2c.micropython_i2c import MicroPythonI2C
from gurgleapps_webserver import GurgleAppsWebserver
import uasyncio as asyncio
import icons
import qwiic_rv8803
import sys
import time
import ntptime
from machine import RTC,Pin,PWM,ADC
import sh1106
from writer import Writer
import clock36
import urandom
import ujson as json
import os
from ntp import get_ntp_time
import socket #debug reason
from timezone import get_timezone
from rotary_irq_rp2 import RotaryIRQ
from audio_player import AudioPlayer

# ============================================================
# GPIO PIN ASSIGNMENT
# Raspberry Pi Pico 2 W / RP2350
# ============================================================
#
# This is the central GPIO map for the project.
# Change pin assignments here rather than using GPIO numbers
# directly elsewhere in the code.
#
# ------------------------------------------------------------
# I2C BUS
# ------------------------------------------------------------
# OLED display and RV8803 RTC share the same I2C bus.
#
GPIO_I2C_SDA      = 4
GPIO_I2C_SCL      = 5
#
# ------------------------------------------------------------
# ROTARY ENCODER
# ------------------------------------------------------------
#
GPIO_ROTARY_CLK   = 13
GPIO_ROTARY_DT    = 14
GPIO_KNOB         = 15 # Changed to 15 on the PCB, 16 on breadboard
#
# ------------------------------------------------------------
# USER BUTTONS
# ------------------------------------------------------------
#
GPIO_BUTTON1      = 17
GPIO_BUTTON2      = 18
GPIO_BUTTON3      = 19
#
# ------------------------------------------------------------
# AMPLIFIER
# ------------------------------------------------------------
#
GPIO_AMP_PWM      = 0
GPIO_AMP_SD       = 1 #Changed to 1 on the PCB, 21 on breadboard
#d
# ------------------------------------------------------------
# LEDS
# ------------------------------------------------------------
#
GPIO_LED1         = 6 #Top, change to 6 on PCB, 1 on breadboard
GPIO_LED2_PWM     = 2 #White
GPIO_LED3_PWM     = 3 #Yellow
#
# ------------------------------------------------------------
# POWER SUPPLY STATUS
# ------------------------------------------------------------
# Two digital inputs used to determine the power supply state.
#
GPIO_POWER_1      = 21 #Change to 21 on PCB, 6 on breadboard
GPIO_POWER_2      = 20 #Change to 20 on PCB, 7 on breadboard
#
# ------------------------------------------------------------
# BATTERY VOLTAGE
# ------------------------------------------------------------
# External battery voltage measurement through ADC.
#
GPIO_BATTERY_ADC  = 26       # ADC0
#
#-------------------------------------------------------------
# ALARM SET SWITCH
#-------------------------------------------------------------
# Switch for turning the alarm on or off
#
GPIO_ALARM_SW=22
#
# ------------------------------------------------------------
# RESERVED / UNUSED
# ------------------------------------------------------------
#
# GPIO 7-12    Currently unused / available
#
# GPIO 23-25   RESERVED - Pico 2 W wireless subsystem
#
# GPIO 27-28   Currently unused ADC capable GPIOs
#
# GPIO 29      RESERVED - Pico 2 W VSYS / wireless SPI
#
# ============================================================


# Global variables and objects
FW_VERSION="1.0.3"
i2c = MicroPythonI2C(sda=GPIO_I2C_SDA, scl=GPIO_I2C_SCL)
wifi_timeout_ms=180000
wifi_timeout=time.ticks_add(time.ticks_ms(),wifi_timeout_ms)
setup_complete=False
WifiNetworks=None
knob_pressed=False
button1_pressed = False
button2_pressed = False
button3_pressed = False
last_knob_press=0
last_button1_press=0
last_button2_press=0
last_button3_press=0
last_alarm_switch_change=0
alarm_switch_changed=False
oled_setup_requested=False
web_setup_requested=False
setup_timezone=0
last_ntp_sync=0
ui_state="clock" # Default state
# TIME_PRESET1 = 1
# TIME_PRESET2 = 2
# TIME_PRESET3 = 3
TIME_ALARM   = 0 # This is the time when the alarm is set to go off
ALARM_STATE=0 # Possible values: 0=waiting, 1=Alarm raised, 2=Snooze in progress
SUNRISE_STATE=0 #Possible values 0-5 to indikate the brightness of the lights
SUNRISE_CANCELLED=False # Switched to true if user cancels sunrise effect during sunrise
SNOOZE_UNTIL=0 # Time to snooze until if user so chooses
SUNRISE_FORCE=False # User can toggle this with button 2 when on the clock screen
AUDIO_VOLUME = 5  # Volume 0–100 %
WIFI_OFF=False
default_config = {
    "timezone": 2,
    "timezone_info":"",
    "snooze": 9,
    "sunrise": 30,
    "preset1": 420,
    "preset2": 450,
    "preset3": 540,
    "tzsync":0,
    "alarmtime":420,
    "alarmsound":0 # 0=beep, 1=raw soundfile
}



config=default_config
#Create external RTC
myRTC = qwiic_rv8803.QwiicRV8803(i2c_driver=i2c)
#Create Pico RTC
pico_RTC=RTC()

#Scheduler run times
ntp_sync_time=241 # 04:01 local time

# Create OLED display
oled = sh1106.SH1106_I2C(
        128, 64,
        i2c._i2cbus,
        None,
        0x3c,
        rotate=180
    )
#Create Rotary Encoder
rotary = RotaryIRQ(
    pin_num_clk=GPIO_ROTARY_CLK,
    pin_num_dt=GPIO_ROTARY_DT,
    min_val=0,
    max_val=23,
    incr=1,
    reverse=False,
    range_mode=RotaryIRQ.RANGE_BOUNDED,
    pull_up=True,
    half_step=False
)

def knob_handler(pin):
    global knob_pressed, last_knob_press,rotary

    now = time.ticks_ms()

    if time.ticks_diff(now, last_knob_press) < 300:
        return

    last_knob_press = now
    print("Knob pressed ",rotary.value())
    knob_pressed = True
    
knob = Pin(GPIO_KNOB, Pin.IN, Pin.PULL_UP)
knob.irq(
    trigger=Pin.IRQ_FALLING,
    handler=knob_handler
)
# End rotary encoder creation

#Create Buttons 1-3
def button1_handler(pin):
    global button1_pressed, last_button1_press

    now = time.ticks_ms()

    if time.ticks_diff(now, last_button1_press) < 300:
        return

    last_button1_press = now
    print("button1 pressed "+str(time.ticks_ms()))
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
    print("button2 pressed"+str(time.ticks_ms()))
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
    print("button3 pressed"+str(time.ticks_ms()))
    button3_pressed = True
    
button3 = Pin(GPIO_BUTTON3, Pin.IN, Pin.PULL_UP)
button3.irq(
    trigger=Pin.IRQ_FALLING,
    handler=button3_handler
)

# End buttons 1-3 creation

# Create Alarm switch
def alarm_switch_handler(pin):
    global alarm_switch_changed,last_alarm_switch_change
    now=time.ticks_ms()
    if time.ticks_diff(now,last_alarm_switch_change)<300:
        return
    last_alarm_switch_change=now
    alarm_switch_changed = True
    print("Alarm Switch Changed")

alarm_switch = Pin(GPIO_ALARM_SW, Pin.IN, Pin.PULL_UP)

alarm_switch.irq(
trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING,
    handler=alarm_switch_handler
)

# End Alarm Switch creation

# Create Sunrise LEDs
led2_pwm = PWM(Pin(GPIO_LED2_PWM))
led2_pwm.freq(1000)
led2_pwm.duty_u16(0)
led3_pwm=PWM(Pin(GPIO_LED3_PWM))
led3_pwm.freq(1000)
led3_pwm.duty_u16(0)

#Create TOP Led
led1_pwm = PWM(Pin(GPIO_LED1))
led1_pwm.freq(1000)
led1_pwm.duty_u16(0)


# Create Audio Player
audio_player = AudioPlayer(
    audio_pin=GPIO_AMP_PWM,
    amp_enable_pin=GPIO_AMP_SD,
    default_file="lighthouse_sound.raw",
    sample_rate=22050,
    pwm_carrier_hz=20000,
    volume=0.55,
    block_samples=8192,
    amp_start_delay_ms=20
)



#Create Power monitoring pins
chrg_pin=Pin(GPIO_POWER_1,Pin.IN,Pin.PULL_UP)
stdby_pin=Pin(GPIO_POWER_2,Pin.IN,Pin.PULL_UP)
battery=ADC(GPIO_BATTERY_ADC)
#BEGIN PROGRAM CODE


async def audio_task():
    while True:
        if ALARM_STATE == 1:
            # Start alarm sound if not already playing
            if not audio_player.is_playing():
                try:
                    if int(config.get("alarmsound", 0)) == 0:

                        # Traditional beep
                        audio_player.play_beep(
                            frequency=1000,
                            on_ms=300,
                            off_ms=300,
                            loop=True
                        )
                    else:
                        # Lighthouse ambient sound
                        audio_player.play(
                            "lighthouse_sound.raw",
                            loop=True
                        )
                except Exception as e:
                    print("Audio start error:", e)
        else:
            # Stop audio when alarm is no longer active
            if audio_player.is_playing():
                audio_player.stop()
        await asyncio.sleep_ms(100)


async def set_led1(state):
    if state == 1:
        target = 65535
    else:
        target = 0

    start = led1_pwm.duty_u16()

    steps = 20
    step = (target - start) / steps
    current = start

    for _ in range(steps):
        current += step
        led1_pwm.duty_u16(int(current))
        await asyncio.sleep_ms(50)

    led1_pwm.duty_u16(target)

async def set_led2_brightness(level):
    brightness = [0, 5, 10, 20, 33, 75]

    if level < 4:
        level = 0
    elif level > 5:
        level = 5

    target = int(brightness[level] * 65535 / 100)
    current = led2_pwm.duty_u16()

    # Fade over 1.8 seconds
    steps = 18
    step = (target - current) / steps

    for i in range(steps):
        current += step
        led2_pwm.duty_u16(int(current))
        await asyncio.sleep_ms(100)

    led2_pwm.duty_u16(target)

async def set_led3_brightness(level):
    brightness = [0, 10, 33, 80, 100, 100]

    if level < 0:
        level = 0
    elif level > 5:
        level = 5

    target = int(brightness[level] * 65535 / 100)
    current = led3_pwm.duty_u16()

    # Fade over 1.8 seconds
    steps = 18
    step = (target - current) / steps

    for i in range(steps):
        current += step
        led3_pwm.duty_u16(int(current))
        await asyncio.sleep_ms(100)

    led3_pwm.duty_u16(target)

async def check_ntp(force=False):

    global last_ntp_sync,WIFI_OFF

    now = time.time()

    if not force and now - last_ntp_sync < 60:
        return

    current = time.localtime()
    current_minutes = current[3] * 60 + current[4]

    if not force and current_minutes != ntp_sync_time:
        return

    last_ntp_sync = now

    try:
        await enable_wifi() #In case we are in powersave mode
        sync_ntp()
        if config["tzsync"] == 1:
            sync_timezone()
        await RTC2Local()
        if WIFI_OFF:
            # Wifi was off deliberatly, we'll turn it off again
            await disable_wifi()

    except Exception as e:
        print("NTP sync failed:", e)

def sync_timezone():
    global config,myRTC
    if not server.is_wifi_connected():
        #We only try this if Wifi is in STA mode
        return
    try:
        from timezone import get_timezone

        info = get_timezone()

        timezone = info["timezone"]
        utc_offset = info["utc_offset"]
        dst = info["dst"]

        # Convert "+03:00" / "-05:30" to RV8803 offset
        sign = 1

        if utc_offset[0] == "-":
            sign = -1

        hours = int(utc_offset[1:3])
        minutes = int(utc_offset[4:6])

        # RV8803 offset = hours * 4
        # Only hours are taken into account
        offset = sign * (hours * 4 )

        print("Timezone:", timezone)
        print("UTC offset:", utc_offset)
        print("RV8803 offset:", offset)
        print("DST:", dst)

        # Set RV8803 timezone offset
        myRTC.set_time_zone_quarter_hours(offset)

        # Save timezone name
        config["timezone"] = offset
        config["timezone_info"]=timezone

        save_config(config)

        return True

    except Exception as e:
        print("Timezone sync failed:", e)
        return False

def sync_ntp():
    #We only attempt this if we are in STA mode
    if not server.is_wifi_connected():
        return
    try:
        UTC_time = get_ntp_time()
#        UTC_time = time.gmtime(utc_time)
        RTCSave(UTC_time)
        print("NTP sync:", UTC_time)
        return True
    except Exception as e:
        print("NTP sync failed:", e)
        return False

async def write_network_config(wificonfig, filename="wifi_config.json"):
    global wifi_config
    wificonfig["init"]=0
    try:
        with open(filename, "w") as f:
            json.dump(wificonfig, f)
        print("Wificonfig=",wificonfig)    
        print("Config saved to", filename)
        wifi_config=wificonfig
    except Exception as e:
        print("Error saving config:", e)

async def getclockconfig(request, response):
    global config
    print("GetClockConfig started")

    local_config = config.copy()
    local_config["voltage"] = await readBatVoltage()
    local_config["powerstate"] = await check_powerstate()

    print("Local Config:",local_config)
    await response.send_json(json.dumps(local_config), 200)
    await extend_wifi_timeout()

async def check_powerstate():
    global chrg_pin,stdby_pin
    chrg=chrg_pin.value()
    stdby=stdby_pin.value()
    
    if chrg==0 and stdby==1:
        return 0 #Charging, on mains power
    if chrg==1 and stdby==0:
        return 1 #Battery Full, on mains power
    if chrg==1 and stdby==1:
        return 2 #On battery power, discharging
    else:
        return -1 #Error state, should not happen

async def readBatVoltage():
    global battery
    
    #Check the battery voltage
    raw_voltage=battery.read_u16()
    conversion_factor=3.3*2/65535 # Voltage divider splits the volts in half.
    voltage=raw_voltage*conversion_factor*1.018 #To account for losses in measurement
    if voltage>4.3: #This situation may happen, if the power switch is off and the Pico is powered from its own USB
        voltage=3.2 #We indicate the need to recharge, this is for debugging.
 
#     print("Voltage:" , voltage)        
    return voltage

async def factoryreset(request,response):
    global ui_state
    ui_state="factory_reset"
    await response.send_json(json.dumps({"result":"OK"}))

async def getnetconfig(request,response):
    global WifiNetworks
    #Function will return JSON containing the Wifi mode, the network that we are connected to, list of prefererred networks and
    #the list of available networks. Also info on wether the page should be displayed in wizard mode
    print("GetNetConfig started")
    if server.is_access_point_active():
        mode="AP"
        con_ssid=server.wlan_ap.config('essid')
    elif server.is_wifi_connected():
        mode="STA"
        con_ssid=server.wlan_sta.config('essid')
    else:
        mode="NONE"
        ssid=None
        

    config=read_network_config()
    known_ssids = [net.get("ssid") for net in config.get("sta", []) if net.get("ssid")]

    # Scanned networks
    available_ssids = []
    for network_info in WifiNetworks:
        w = network_info[0].decode("utf-8")
        if w:
            available_ssids.append(w)
            print("Available Wifi", w)

    available_networks = []
  
    # 1. Loop known networks
    for ssid in known_ssids:
        if ssid in available_ssids:
            flag = 1   # known & available
        else:
            flag = 2   # known but available
        available_networks.append({"ssid": ssid, "known": flag})
  
    # 2. Loop scanned networks and add those that are unknown
    for ssid in available_ssids:
        if ssid not in known_ssids:
            available_networks.append({"ssid": ssid, "known": 0})
            
    response_string={
        "init":config["init"],
        "mode":mode,
        "connected_ssid":con_ssid,
        "available_networks":available_networks,
        "hostname":config["hostname"]}
    
    print("GetConfig :",response_string)
    await response.send_json(json.dumps(response_string),200)
    await extend_wifi_timeout()

async def deletewifi(request,response,wifissid):
    #Delete a Wifi from the known networks
    global ui_state
    wificonfig=read_network_config()
    if "sta" not in wificonfig:
        response_json={"result": -1,"msg" : "No STA in Config"}
        await response.send_json(json.dumps(response_json),200)
        return
    
    wificonfig["sta"] = [net for net in wificonfig["sta"] if net.get("ssid") != wifissid]
    await write_network_config(wificonfig)
    
    response_json={"result":0,"msg":f"WLAN {wifissid} removed"}
    await response.send_json(json.dumps(response_json),200)
    
    server.scan_networks(server) #this also disconnects all connections
    await setup_network_from_config(server)
    ui_state="display_network"
    return  

async def switchtoap(request,response):
    #Edit the wifi_config to change mode and restart wifi
    global ui_state,WifiNetworks
    wificonfig = read_network_config()
    if "ap" not in wificonfig:
        response_json={"result":-1,"msg":"No AP in Config"}
        await response.send_json(json.dumps(response.json),200)
        return
    
    wificonfig["mode"]="ap"
    
    apconfig=wificonfig["ap"]
    apwlan=apconfig["ssid"]
    await write_network_config(wificonfig)
    
    response_json={"result":0,"msg":f"Winder changed to AP mode, you need to joint the internal {apwlan} WLAN"}
    await response.send_json(json.dumps(response_json),200)
    
    WifiNetworks=server.scan_networks(server)
    await setup_network_from_config(server)
    ui_state="display_network"
    return

async def choose_wifi(wifissid):
    global ui_state
    wificonfig=read_network_config()
    if "sta" not in wificonfig:
        response_json={"result": -1,"msg" : "No STA in Config"}
        await response.send_json(json.dumps(response_json),200)
        return -1
    
    sta_list=wificonfig["sta"]
    
    target=None
    for net in sta_list:
        if net.get("ssid") == wifissid:
            target=net
            break
    if not target:
        return -1
        
    sta_list.remove(target)
    sta_list.insert(0,target)
    wificonfig["sta"]=sta_list
    wificonfig["mode"]="sta"
    
    await write_network_config(wificonfig)    
    server.scan_networks(server) #this also disconnects all connections
    await setup_network_from_config(server)
    return 0

async def connectwifi(request,response,wifissid):
    #Refactored version to isolate web interface from logic
    global ui_state

    result = await choose_wifi(wifissid)

    if result == -1:
        response_json = {
            "result": -1,
            "msg": "WLAN not previously known"
        }
        await response.send_json(json.dumps(response_json), 200)
        return

    response_json = {
        "result": 0,
        "msg": f"Network configuration changed, you need to join WLAN {wifissid}"
    }
    await response.send_json(json.dumps(response_json), 200)

    ui_state = "display_network"    
    

async def joinwifi(request,response,wifissid,wifipw):
    #New Wifi, let's add that to the list as the new preferred Wifi
    global ui_state
    wificonfig= read_network_config()
    if "sta" not in wificonfig:
        response_json={"result": -1,"msg" : "No STA in Config"}
        await response.send_json(json.dumps(response_json),200)
        return
    
    sta_list=wificonfig["sta"]
    newssid={"ssid":wifissid,"password":wifipw}
    sta_list.insert(0,newssid)
    wificonfig["sta"]=sta_list
    wificonfig["mode"]="sta"
    
    await write_network_config(wificonfig)
    response_json={"result":0,"msg":f"New WLAN {wifissid} added, you need to join it. In case of incorrect passkey, the winder will reconnect back to this WLAN"}
    await response.send_json(json.dumps(response_json),200)
       
    server.scan_networks(server) #this also disconnects all connections
    await setup_network_from_config(server)
    ui_state="display_network"
    return
    

async def change_ap_pw(request,response,newpw):
     #Change the AP passowrd in wifi_config.json and restart wifi
    global ui_state
    wificonfig=read_network_config()
    if "ap" not in wificonfig:
        response_json={"result":-1,"msg":"No AP in Config"}
        await response.send_json(json.dumps(response.json),200)
        return
    ap_config=wificonfig["ap"]
    ap_config["password"]=newpw
    wificonfig["ap"]=ap_config
    await write_network_config(wificonfig)
    
    response_json={"result":0,"msg":"AP Mode password changed. You need to rejoin the WLAN"}
    await response.send_json(json.dumps(response_json),200)
    
    server.scan_networks(server)
    await setup_network_from_config(server)
    ui_state="display_network"
    return

async def changehostname(request,response,newhostname):
    #Change the device network hostname
    global ui_state
    wificonfig=read_network_config()
    wificonfig["hostname"]=newhostname
    await write_network_config(wificonfig)
    response_json={"result":0,"msg":"Hostname changed to {host_name}"}
    await response.send_json(json.dumps(response_json),200)
    print("Restarting")
    await asyncio.sleep(2)
    machine.reset()
    return

async def rename_ap(request,response,apname):
    #Change the AP name in wifi_config.json and restart wifi
    global ui_state
    wificonfig=read_network_config()
    if "ap" not in wificonfig:
        response_json={"result":-1,"msg":"No AP in Config"}
        await response.send_json(json.dumps(response_json),200)
        return
    ap_config=wificonfig["ap"]
    ap_config["ssid"]=apname
    wificonfig["ap"]=ap_config
    await write_network_config(wificonfig)
    
    response_json={"result":0,"msg":f"AP Mode renamed to {apname}. You need to join the new WLAN"}
    await response.send_json(json.dumps(response_json),200)
    print("AP Change response sent")
    server.scan_networks(server)
    await setup_network_from_config(server)
    ui_state="display_network"
    return


async def load_config(config_file="config.json"):
    #Config is stored in a global variable, this function should only be called in the beginning of the main function
    global config,myRTC,TIME_ALARM
    try:
        with open("config.json", "r") as fd:
            config=json.load(fd)    
    except OSError:
        return False
    fd.close()
    # Set timezone to RTC
    print(config)
    TIME_ALARM=config["alarmtime"]
    myRTC.set_time_zone_quarter_hours(int(config["timezone"]))
    await RTC2Local()
    
    
def save_config(config, config_file="config.json"):
    try:
        with open(config_file, "w") as f:
            json.dump(config, f)

        print("Configuration saved.")
        return True

    except OSError as e:
        print("Error saving config:", e)
        return False

    except Exception as e:
        print("Unexpected error saving config:", e)
        return False

async def RTC2Local():
    global myRTC,pico_RTC
    UTC_time,Local_time,tz=myRTC.datetime()
    pico_RTC.datetime(Local_time)
    print(UTC_time,Local_time,tz)
    
def RTCSave(UTC_time):
    global myRTC
    # Convert MicroPython weekday (0=Monday ... 6=Sunday)
    # to RV-8803 weekday bitmap
    weekday=UTC_time[6]
    weekday = 1 << ((weekday + 1) % 7)
    
    myRTC.set_time(
    UTC_time[5],  # second
    UTC_time[4],  # minute
    UTC_time[3],  # hour
    weekday,  # weekday
    UTC_time[2],  # day
    UTC_time[1],   # month
    UTC_time[0]   # year
    )

def get_alarm_indicator():
    global ALARM_STATE
    if ALARM_STATE==2:
        return "S"
    if alarm_switch.value() == 0:
        return "A"
    return " "

async def get_battery_indicator():
    if await check_powerstate()==1:
        return "       "
    if await readBatVoltage() <3.3:
        return "BAT LOW"
    return "       "
        

def Time2Minutes(hour=None, minute=None):
    if hour is None and minute is None:
        _, _, _, _, hour, minute, _, _ = pico_RTC.datetime()
    elif hour is None or minute is None:
        return -1

    return hour * 60 + minute

async def saveclockconfig(request,response,TimeZone,snooze,sunrise,preset1,preset2,preset3,tzsync,utc_time,alarmsound):
    global config,myRTC
    
    config["timezone"]=int(TimeZone)*4
    config["snooze"]=snooze
    config["sunrise"]=sunrise
    config["preset1"]=preset1
    config["preset2"]=preset2
    config["preset3"]=preset3
    config["tzsync"]=tzsync
    config["alarmsound"]=alarmsound
    UTC_Time=time.gmtime(int(utc_time))
    print("saveclock utc time:")
    print(UTC_Time)
    print("Unix:", utc_time)
    print("Python:", time.localtime(int(utc_time)))
    print("MP weekday:", UTC_Time[6])
    RTCSave(UTC_Time)
    print("RTC raw:", myRTC._time)
    print("RTC weekday raw:", myRTC._time[myRTC.kIdxWeekday])
    if int(tzsync)==0:
        myRTC.set_time_zone_quarter_hours(int(TimeZone)*4)
        config["timezone_info"]=""
    else:
        #To implement: ipapi timezone sync
        print("Syncing timezone")
        sync_ntp()
        sync_timezone()
    await RTC2Local()
    save_config(config)
    response_string={"Result":0}
    await extend_wifi_timeout()
    await response.send_json(json.dumps(response_string),200)
    
async def savewizard(request,response,TimeZone,snooze,sunrise,utc_time):
    global setup_complete,myRTC,default_config,config
    UTC_Time=time.gmtime(int(utc_time))
    rtc_tz=int(TimeZone)*4
    #Set RTC time
    RTCSave(UTC_Time)
    myRTC.set_time_zone_quarter_hours(rtc_tz)
    # Sync RTC to local
    await RTC2Local()
    config=default_config.copy()
    config["timezone"]=rtc_tz
    config["snooze"]=snooze
    config["sunrise"]=sunrise
    if save_config(config):
        setup_complete=True
        response_string={"Result":0}
    else:
        response_string={"Result":1}
    await response.send_json(json.dumps(response_string),200)
    
    
    
    
    

async def getsetupstate(request,response):
    global setup_complete
    if config_exists():
        setup_complete=True
    response_string={"Setup_complete":setup_complete}
    await response.send_json(json.dumps(response_string),200)
    await extend_wifi_timeout()
    
async def startsetup(request,response):
    global setup_complete,web_setup_requested
    print("Start Setup requested")
    response_string={"Setup_complete":setup_complete}
    await response.send_json(json.dumps(response_string),200)
    web_setup_requested=True
    await extend_wifi_timeout()

async def extend_wifi_timeout():
    global wifi_timeout
    wifi_timeout=time.ticks_add(time.ticks_ms(),wifi_timeout_ms)
    print("Wifi timeout extended to ", wifi_timeout/60000)

def generate_password(length=8):
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(
        chars[urandom.getrandbits(32) % len(chars)]
        for _ in range(length)
    )

def read_network_config(config_file="wifi_config.json"):
    default_config = {
        "init":1,
        "hostname": "MyLighthouse",
        "mode": "ap",
        "ap": {
            "ssid": "Lighthouse",
            "password": generate_password()
        },
        "sta": []
    }

    try:
        with open(config_file) as f:
            config = json.load(f)
    except OSError:
        # Tiedostoa ei löytynyt → luodaan oletus
        print("Config file not found, creating default config...")
        with open(config_file, "w") as f:
            json.dump(default_config, f)
        config = default_config
    return config

def config_exists():
    try:
        with open("config.json", "r"):
            return True
    except OSError:
        return False

async def disable_wifi():
    global wifi_timeout
 #   print ("Wifi timeout at " , wifi_timeout)
    if server.wifi_enabled:
        print("Turning Wifi off")
        await server.stop_server()
        server.wifi_off()

async def enable_wifi():
    global wifi_timeout
#     voltage=await readBatVoltage()
#     if voltage<3.45 and (await check_powerstate()==2):
#         #We will not start wifi to conserver power
#         return
    if server.wifi_enabled==False:
        print ("Turning Wifi on")
        server.wifi_on()
        #WifiNetworks=await server.scan_networks(server)
        await extend_wifi_timeout()
        await setup_network_from_config(server)
        await server.start_server()

async def setup_network_from_config(server):

    config=read_network_config()
    mode=config.get("mode","ap")

    if mode == "ap":
        ssid = config["ap"]["ssid"]
        pwd = config["ap"]["password"]
        print(f"Starting Access Point: {ssid}")
        server.start_access_point(ssid, pwd)
        await extend_wifi_timeout()
        return True

    elif mode == "sta":
        networks = config.get("sta", [])
        for net in networks:
            ssid = net["ssid"]
            pwd = net["password"]
            print(f"Trying Wi-Fi: {ssid}")
            ok = await server.connect_wifi(ssid, pwd)
            if ok:
                print(f"Connected to {ssid}")
                await extend_wifi_timeout()
                return True

        # Jos yksikään STA-verkko ei onnistunut → fallback AP:ksi
        print("No known Wi-Fi could be connected. Starting fallback Access Point...")
        ssid = config["ap"]["ssid"]
        pwd = config["ap"]["password"]
        server.start_access_point(ssid, pwd)
        await extend_wifi_timeout()
        return True

    else:
        print(f"Unknown mode: {mode}, starting fallback Access Point...")
        ssid = config["ap"]["ssid"]
        pwd = config["ap"]["password"]
        server.start_access_point(ssid, pwd)
        await extend_wifi_timeout()
        return True

async def oled_wizard1():
    global oled,wifi_config,web_setup_requested,knob_pressed,oled_setup_requested
    oled.fill(0)
    oled.text("Lighthouse setup" ,0,0)
    oled.text("Connect to Wifi",0,10)
    oled.text("SSID:" + wifi_config["ap"]["ssid"], 0, 20)
    oled.text("PW:" + wifi_config["ap"]["password"], 0, 30)
    oled.text("IP:"+server.get_ap_ip_address() ,0,40)
    oled.text("Or press [OK] ",0,50)
    oled.show()
    knob_pressed = False
    web_setup_requested = False
    Proceed=False
    while not Proceed:
        if web_setup_requested:
            await asyncio.sleep_ms(50)
            Proceed=True
        if knob_pressed:
            oled_setup_requested=True
            knob_pressed=False
            Proceed=True
            return True
        await asyncio.sleep_ms(20)
    return True

def setup_rotary(value, min_val, max_val, wrap=False):
    global rotary
    if wrap:
        range_mode = RotaryIRQ.RANGE_WRAP
    else:
        range_mode = RotaryIRQ.RANGE_BOUNDED
    rotary.set(
        value=value,
        min_val=min_val,
        max_val=max_val,
        incr=1,
        range_mode=range_mode,
        reverse=True
    )

async def oled_setup_date(wizardmode=False):
    # This page sets the date
    global oled, knob_pressed, rotary, setup_timezone,button1_pressed,button3_pressed,ui_state

    knob_pressed = False
    button1_pressed=False
    button3_pressed=False
    last_action=time.ticks_ms()
    now = machine.RTC().datetime()

    setup_day = now[2]
    setup_month = now[1]
    setup_year = now[0]

    # -------------------------
    # SET DAY
    # -------------------------

    setup_rotary(setup_day, 1, 31, True)

    last_value = None
    last_blink = None

    while not knob_pressed:

        value = rotary.value()
        blink = (time.ticks_ms() // 500) % 2 == 0
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ut_state="menu_clock"
            return
        if value != last_value:
            last_value = value
            last_action=time.ticks_ms()
            setup_day = value
            oled.fill(0)
            oled.text("Setup date", 0, 0)

            # Draw complete date normally
            daterow = f"{setup_day:02d}.{setup_month:02d}.{setup_year:04d}"
            oled.text(daterow, 12, 25)

            # Invert day
            oled.fill_rect(12, 25, 16, 8, 1)
            oled.text(f"{setup_day:02d}", 12, 25, 0)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)                
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False

    # -------------------------
    # SET MONTH
    # -------------------------

    setup_rotary(setup_month, 1, 12, True)

    last_value = None
    last_blink = None

    while not knob_pressed:

        value = rotary.value()
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ut_state="menu_clock"
            return
        if value != last_value:
            last_value = value
            last_action=time.ticks_ms()
            setup_month = value

            oled.fill(0)
            oled.text("Setup date", 0, 0)

            # Draw complete date normally
            daterow = f"{setup_day:02d}.{setup_month:02d}.{setup_year:04d}"
            oled.text(daterow, 12, 25)

            # Invert month
            oled.fill_rect(36, 25, 16, 8, 1)
            oled.text(f"{setup_month:02d}", 36, 25, 0)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)                
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if time.ticks_diff(time.ticks_ms,last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False

    # -------------------------
    # SET YEAR
    # -------------------------

    setup_rotary(setup_year, 2020, 2099, False)

    last_value = None
    last_blink = None

    while not knob_pressed:

        value = rotary.value()
        blink = (time.ticks_ms() // 500) % 2 == 0
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ut_state="menu_clock"
            return
        if value != last_value:
            last_value = value
            setup_year = value
            last_action=time.ticks_ms()

            oled.fill(0)
            oled.text("Setup date", 0, 0)

            # Draw complete date normally
            daterow = f"{setup_day:02d}.{setup_month:02d}.{setup_year:04d}"
            oled.text(daterow, 12, 25)

            # Invert year
            oled.fill_rect(60, 25, 32, 8, 1)
            oled.text(f"{setup_year:04d}", 60, 25, 0)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)                
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False

    # -------------------------
    # SAVE DATE TO PICO RTC
    # -------------------------

    now = machine.RTC().datetime()

    rtc_datetime = (
        setup_year,
        setup_month,
        setup_day,
        now[3],          # weekday
        now[4],          # hour
        now[5],          # minute
        now[6],          # second
        now[7]           # subsecond
    )

    machine.RTC().datetime(rtc_datetime)

    # -------------------------
    # SAVE TO RV8803 + CONFIG
    # -------------------------

    now = machine.RTC().datetime()

    # Convert Pico RTC datetime format to time tuple format
    local_time = (
        now[0],          # year
        now[1],          # month
        now[2],          # day
        now[4],          # hour
        now[5],          # minute
        now[6],          # second
        now[3],          # weekday
        now[7]           # subsecond
    )

    # Convert local time to UTC
    utc_time = time.mktime(local_time) - setup_timezone * 900

    UTC_Time = time.gmtime(int(utc_time))

    # Save UTC time to RV8803
    RTCSave(UTC_Time)

    # Save timezone to RV8803
    myRTC.set_time_zone_quarter_hours(setup_timezone)

    # Save configuration
    if wizardmode:
        config = default_config.copy()
        config["timezone"] = setup_timezone

        if save_config(config):
            return True

    return False

async def oled_set_tz(wizardmode=False):
    # This page sets the timezone
    global oled, knob_pressed, rotary, setup_timezone,myRTC,ui_state,button1_pressed,button3_pressed
    
    button1_pressed=False
    button3_pressed=False
    knob_pressed = False
    last_action=time.ticks_ms()
    if wizardmode:
        setup_timezone = 0
    else:
        setup_timezone = int(config["timezone"]) // 4  # Stored in quarter hours

    setup_rotary(setup_timezone, -12, 12, True)

    last_value = None
    last_blink = None

    while not knob_pressed:

        value = rotary.value()
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ut_state="menu_clock"
            return
        # Update display only when value or blink state changes
        if value != last_value:

            last_value = value
            last_action=time.ticks_ms()
            setup_timezone = value

            oled.fill(0)
            oled.text("Setup Timezone", 0, 0)
            utcrow = f"UTC {setup_timezone:+d}"
            oled.text("UTC", 0, 30)
            oled.fill_rect(40, 30, 16, 8, 1)
            oled.text(f"{setup_timezone:+d}",40,30,0)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)                
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if not wizardmode and time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False

    # Convert hours to RV8803 quarter-hour format
    setup_timezone = setup_timezone * 4
    if not wizardmode:
        #Save the timezoneinfo now to both the config.json and RV8803
        config["timezone"]=setup_timezone
        myRTC.set_time_zone_quarter_hours(setup_timezone)
        save_config(config)
        await RTC2Local()
        
    
    

async def oled_set_time(wizardmode=False):
    #This page gets the time
    global oled,knob_pressed,rotary,button1_pressed,button3_pressed,ui_state
    knob_pressed=False
    button1_pressed=False
    button3_pressed=False
    setup_hour=0
    setup_minute=0
    last_action=time.ticks_ms()
    if not wizardmode:
        now = machine.RTC().datetime()
        setup_hour=now[4]
        setup_minute=now[5]
    setup_rotary(setup_hour,0,23,True)
    wri = Writer(oled, clock36,False)
    last_value=None
    while not knob_pressed:
        value = rotary.value()
        if not wizardmode:
            if button1_pressed:
                button1_pressed=False
                ui_state="clock"
                return
            if button3_pressed:
                button3_pressed=False
                ut_state="menu_clock"
                return

        # Update display only when something changed
        if value != last_value :
            last_value = value
            last_action=time.ticks_ms()
            setup_hour = value
            oled.fill(0)
            oled.text("Setup local time", 0, 0)
            clockrow = f"{setup_hour:02d}:{setup_minute:02d}"
            x = (128 - wri.stringlen(clockrow)) // 2
            # hours inverted
            wri.setpos(10, x)
            wri.printstring(f"{setup_hour:02d}", invert=True)
            # Colon and minutes uninverted
            wri.setpos(10, x + wri.stringlen(f"{setup_hour:02d}"))
            wri.printstring(f":{setup_minute:02d}")            
#             wri.printstring(clockrow)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if not wizardmode and time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False
    setup_rotary(setup_minute,0,59,True)
    last_value = None


    while not knob_pressed:

        value = rotary.value()
        if not wizardmode:
            if button1_pressed:
                button1_pressed=False
                ui_state="clock"
                return
            if button3_pressed:
                button3_pressed=False
                ut_state="menu_clock"
                return

        if value != last_value:
            last_action=time.ticks_ms()
            last_value = value
            setup_minute = value

            oled.fill(0)
            oled.text("Setup local time", 0, 0)
            clockrow = f"{setup_hour:02d}:{setup_minute:02d}"


            x = (128 - wri.stringlen(clockrow)) // 2
            wri.setpos(10, x)
            # hours normal
            wri.setpos(10, x)
            wri.printstring(f"{setup_hour:02d}:")
            # minutes inverted
            wri.setpos(10, x + wri.stringlen(f":{setup_minute:02d}"))
            wri.printstring(f"{setup_minute:02d}",invert=True)
            if not wizardmode:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)
            else:
                oled.text("Knob to proceed", 0, 50)
            oled.show()
        if not wizardmode and time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(20)

    knob_pressed = False
    now = machine.RTC().datetime()

    rtc_datetime = (
        now[0],          # year
        now[1],          # month
        now[2],          # day
        now[3],          # weekday
        setup_hour,
        setup_minute,
        0,               # seconds
        0
    )

    machine.RTC().datetime(rtc_datetime)
    if not wizardmode:
       #Convert Pico RTC datetime format to time tuple format
        now=machine.RTC().datetime()
        local_time = (
            now[0],          # year
            now[1],          # month
            now[2],          # day
            now[4],          # hour
            now[5],          # minute
            now[6],          # second
            now[3],          # weekday
            now[7]           # subsecond
        )
        # Convert local time to UTC
        utc_time = time.mktime(local_time) - int(config["timezone"]) * 900

        UTC_Time = time.gmtime(int(utc_time))

        # Save UTC time to RV8803
        RTCSave(UTC_Time)
        
    
        
    
async def oled_websetup():
    global oled,setup_complete,ui_state
    oled.fill(0)
    oled.text("Lighthouse setup",0,0)
    oled.text("Please complete",0,20)
    oled.text("setup on wifi",0,30)
    oled.show()
    while setup_complete==False:
        await asyncio.sleep_ms(50)
    ui_state="clock"

async def oled_display_network(LocalRequest=False):
    global oled,ui_state,wifi_config,button1_pressed,button3_pressed
    x=0
    print("Show network")
    if wifi_config["mode"]=="ap":
        ssid=wifi_config["ap"]["ssid"]
        wifipw=wifi_config["ap"]["password"]
        pwrow="PW  :"+wifipw
        hostrow="IP :" + server.wlan_ap.ifconfig()[0]
    else:
        ssid = server.wlan_sta.config("essid")
        pwrow="http://"
        hostrow=wifi_config["hostname"]
    if not server.wifi_enabled:
        ssid="[None]"
        pwrow="Wifi is"
        hostrow="turned off"
    while x<10:
        oled.fill(0)
        if LocalRequest:
            oled.text("Net Info",0,0)
        else:
            oled.text("Wifi changed",0,0)
        oled.text("SSID:"+ssid,0,10)
        oled.text(pwrow,0,20)
        oled.text(hostrow,0,30)   
        if LocalRequest:
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
        else:
            oled.text("This info will ",0,40)
            oled.text("clear in "+ str(10-x) +" sec",0,50)
        oled.show()
        if LocalRequest:
            x=0 #We exit only via the buttons
            if button1_pressed:
                ui_state="clock"
                button1_pressed=False
                return 
            if button3_pressed:
                ui_state="menu_net"
                button3_pressed=False
                return 
        x=x+1
        await asyncio.sleep(1)
    ui_state="clock"
        

async def oled_display_clock():
    global oled,pico_RTC,ui_state,button1_pressed,button2_pressed,knob_pressed,SUNRISE_STATE,SUNRISE_CANCELLED,ALARM_STATE,SNOOZE_UNTIL,SUNRISE_FORCE
    
    wri = Writer(oled, clock36,False)
    while ui_state=="clock":
        if button1_pressed:
            button1_pressed = False
            ui_state = "menu"
            return
        if SUNRISE_STATE!=0:
            if button2_pressed:
                button2_pressed=False
                SUNRISE_CANCELLED=True
                SUNRISE_FORCE=False
        if button2_pressed: # Check if user wants to force sunrise on
            button2_pressed=False
            SUNRISE_FORCE=True
        if ALARM_STATE==1: #Check for snooze
            if knob_pressed:
                knob_pressed=False
                SNOOZE_UNTIL=Time2Minutes() + int(config["snooze"])
                if SNOOZE_UNTIL >= 1440:
                    SNOOZE_UNTIL -= 1440 #Handle next day scenario
                ALARM_STATE=2
        year, month, day, weekday, hour, minute, second, _ = pico_RTC.datetime()
        battery_indicator=await get_battery_indicator()
        oled.fill(0)
        alarm_indicator = get_alarm_indicator()
        oled.text(alarm_indicator, 120, 0)
        oled.text(battery_indicator,0,0)
        daterow=str(day)+"."+str(month)+"."+str(year)
        separator = ":" if second % 2 == 0 else " "
        clockrow = f"{hour:02d}{separator}{minute:02d}"
        x = (128 - wri.stringlen(clockrow)) // 2
        wri.setpos(10, x)
        wri.printstring(clockrow)
        oled.text(daterow,20,55)
        oled.invert(False)
        oled.show()
        await asyncio.sleep(1)

async def oled_display_get_on_off(mode):
    global oled,ui_state,button1_pressed,button3_pressed,rotary,knob_pressed
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    selections = [0,1]
    
    if mode==1:
        Title="Timezone Sync"
        in_value=int(config["tzsync"])
        on_value="ON "
        off_value="OFF"
    elif mode==2:
        Title="Set Wifi mode"
        wifi_config=read_network_config()
        off_value="AP "
        on_value="STA"
        if wifi_config["mode"]=="ap":
            in_value=0
        else:
            in_value=1
    elif mode==3:
        Title="Turn Wifi ON/OFF"
        on_value="ON "
        off_value="OFF"
        if server.wifi_enabled:
            in_value=1
        else:
            in_value=0
    elif mode==4:
        Title="Alarm sound"
        on_value="Waves & Seagulls"
        off_value="Beep"
        in_value=int(config["alarmsound"])
    setup_rotary(in_value,0,1,False)
    wri = Writer(oled, clock36,False)
    last_index=None
    while not knob_pressed:
        index=rotary.value()
        if index != last_index:
            last_index=index
            oled.fill(0)
            oled.text(Title, 0, 0)
            if mode==4:
                value = off_value if index == 0 else on_value
                x = (128 - len(value) * 8) // 2
                w = len(value) * 8
                oled.fill_rect(x - 2, 20, w + 4, 12, 1)
                oled.text(value, x, 22, 0)    
            else:
                x = (128 - wri.stringlen(on_value)) // 2
                wri.setpos(10, x)            
                if index==0:
                    wri.printstring(off_value, invert=True)
                else:
                    wri.printstring(on_value, invert=True)
            
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            ui_state="clock"
            button1_pressed=False
            return -1
        if button3_pressed:
            ui_state="menu_net"
            button3_pressed=False
            return -1
    knob_pressed=False
    return index
    
async def oled_display_get_minutes(mode):
    global oled,ui_state,button1_pressed,button3_pressed,rotary,knob_pressed
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    snooze_selections = [0, 5, 9, 15]
    sunrise_selections = [0, 10, 20, 30]
    tz_sync_selections = [0,1]
    if mode==1:
        Title="Set snooze"
        selections=snooze_selections
        in_value=int(config["snooze"])
    elif mode==2:
        Title="Set Sunrise"
        selections=sunrise_selections
        in_value=int(config["sunrise"])
    elif mode==3:
        Title="Timezone sync"
        selections=tz_sync_selections
        in_value=int(config["tzsync"])
    print("mode=", mode)
    print("selections=", selections)
    print("in_value=", in_value)
    print("type=", type(in_value))   
    index = selections.index(in_value)
    setup_rotary(index,0,len(selections)-1,False)
    wri = Writer(oled, clock36,False)
    last_index=None
    while not knob_pressed:
        index=rotary.value()
        if index != last_index:
            last_index=index
            oled.fill(0)
            oled.text(Title, 0, 0)
            if mode !=3:
                timerow=f"{selections[index]:02d}"
            else:
                if index==0:
                    timerow="OFF"
                else:
                    timerow="ON "
            x = (128 - wri.stringlen(timerow)) // 2
            wri.setpos(10, x)
            wri.printstring(timerow, invert=True)
            if mode!=3:
                wri.setpos(10, x + wri.stringlen(" mins"))
                wri.printstring(" mins")            
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            ui_state="clock"
            button1_pressed=False
            return -1
        if button3_pressed:
            ui_state="menu_clock2"
            button3_pressed=False
            return -1
        await asyncio.sleep_ms(20)
    knob_pressed=False
    return selections[index]

def min_time_2_string(minutes):
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}{minute:02d}"

async def oled_display_get_time(mode):
    global oled, ui_state,button1_pressed,button2_pressed,button3_pressed,rotary,knob_pressed,alarm_switch
    button1_pressed=False
    button2_pressed=False
    button3_pressed=False
    knob_pressed=False
    Preset1=int(config["preset1"])
    Preset2=int(config["preset2"])
    Preset3=int(config["preset3"])
    Preset1_text=min_time_2_string(Preset1)
    Preset2_text=min_time_2_string(Preset2)
    Preset3_text=min_time_2_string(Preset3)
    print(">>> GET TIME START, mode=", mode)
    print("knob_pressed at start=", knob_pressed)
    if mode==1:
        #This is for preset1
        Title="Set Preset 1"
        time_hour=int(config["preset1"])//60
        time_minute=int(config["preset1"])-time_hour*60
    elif mode==2:
        #This is for preset2
        Title="Set Preset 2"
        time_hour=int(config["preset2"])//60
        time_minute=int(config["preset2"])-time_hour*60
    elif mode==3:
        #This is for preset3
        Title="Set Preset 3"
        time_hour=int(config["preset3"])//60
        time_minute=int(config["preset3"])-time_hour*60
    elif mode==4:
        #This is for Alarm time
        Title="Set Alarm"
        time_hour=int(config["alarmtime"])//60
        time_minute=int(config["alarmtime"])-time_hour*60
    elif mode==5:
        #This is for future use
        Title="not implemented"
        time_hour=int(config["alarm_time"])//60
        time_minute=int(config["alarm_time"])-time_hour*60
    setup_rotary(time_hour, 0, 23, False)
    print("Alarm rotary initialized:", rotary.value())
    wri = Writer(oled, clock36,False)
    last_value=None
    inactivity_time=time.ticks_ms()
    while not knob_pressed:
        time_hour = rotary.value()
        if time_hour != last_value:
            inactivity_time=time.ticks_ms()
            last_value = time_hour
            oled.fill(0)
            oled.text(Title, 0, 0)
            clockrow = f"{time_hour:02d}:{time_minute:02d}"
            x = (128 - wri.stringlen(clockrow)) // 2
            # hours inverted
            wri.setpos(10, x)
            wri.printstring(f"{time_hour:02d}", invert=True)
            # Colon and minutes uninverted
            wri.setpos(10, x + wri.stringlen(f"{time_hour:02d}"))
            wri.printstring(f":{time_minute:02d}")
            if mode==4:
                oled.fill_rect(0, 54, 36, 9, 1)
                oled.text(Preset1_text, 2, 55, 0)

                oled.fill_rect(43, 54, 36, 9, 1)
                oled.text(Preset2_text, 45, 55, 0)

                oled.fill_rect(86, 54, 36, 9, 1)
                oled.text(Preset3_text, 88, 55, 0)
            else:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            if mode!=4:
                ui_state="clock"
                button1_pressed=False
                return -1
            else:
                button1_pressed=False
                return Preset1
        if button2_pressed and mode==4:
            button2_pressed=False
            return Preset2
        if button3_pressed and mode!=4:
            if mode<4:
                ui_state="menu_presets"
            else:
                ui_state="menu_clock"
            button3_pressed=False
            return -1
        if button3_pressed and mode==4:
            button3_pressed=False
            return Preset3
            
        await asyncio.sleep_ms(10)
        if mode==4: #Check for inactivity or if the switch is back in off position
            if time.ticks_diff(time.ticks_ms(),inactivity_time)>10000:
                return config["alarmtime"]
            if alarm_switch.value()==1:
                return -1
        
    knob_pressed=False
    inactivity_time=time.ticks_ms()
    setup_rotary(time_minute,0,59,True)
    last_value = None

    while not knob_pressed:
        time_minute = rotary.value()

        if time_minute != last_value:
            inactivity_time=time.ticks_ms()
            last_value = time_minute

            oled.fill(0)
            oled.text(Title, 0, 0)
            clockrow = f"{time_hour:02d}:{time_minute:02d}"


            x = (128 - wri.stringlen(clockrow)) // 2
            wri.setpos(10, x)
            # hours normal
            wri.setpos(10, x)
            wri.printstring(f"{time_hour:02d}:")
            # minutes inverted
            wri.setpos(10, x + wri.stringlen(f":{time_minute:02d}"))
            wri.printstring(f"{time_minute:02d}",invert=True)
            if mode==4:
                oled.fill_rect(0, 54, 36, 9, 1)
                oled.text(Preset1_text, 2, 55, 0)

                oled.fill_rect(43, 54, 36, 9, 1)
                oled.text(Preset2_text, 45, 55, 0)

                oled.fill_rect(86, 54, 36, 9, 1)
                oled.text(Preset3_text, 88, 55, 0)
            else:
                oled.fill_rect(0, 54, 32, 9, 1)
                oled.text("EXIT", 0, 55, 0)
                oled.fill_rect(85, 54, 32, 9, 1)
                oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            ui_state="clock"
            button1_pressed=False
            return -1
        if button3_pressed:
            if mode<4:
                ui_state="menu_presets"
            else:
                ui_state="menu_clock"
            button3_pressed=False
            return -1      
        await asyncio.sleep_ms(20)
        if mode==4: #Check for inactivity or if the switch is back in off position
            if time.ticks_diff(time.ticks_ms(),inactivity_time)>10000:
                return config["alarmtime"]
            if alarm_switch.value()==1:
                return -1
    knob_pressed = False
    return time_hour*60+time_minute
    

async def oled_display_menu_clock2():
    global oled, ui_state,button1_pressed,button3_pressed,rotary,knob_pressed,config
    
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    setup_rotary(1,1,4,False)
    last_value=None
    last_action=time.ticks_ms()
    print ("Menu clock 2")
    while ui_state=="menu_clock2":
        value=rotary.value()
        if value!=last_value:
            last_value=value
            last_action=time.ticks_ms()
            cursor1="  "
            cursor2="  "
            cursor3="  "
            cursor4="  "
            if value==1:
                cursor1=" >"
            elif value==2:
                cursor2=" >"
            elif value==3:
                cursor3=" >"
            elif value==4:
                cursor4=" >"
            oled.fill(0)
            oled.text("CLOCK SETTINGS",0,0)
            oled.text(cursor1 + "Set Snooze", 0, 10)
            oled.text(cursor2 + "Set Sunrise", 0, 20)
            oled.text(cursor3 + "Timezone sync", 0, 30)
            oled.text(cursor4 + "Alarm Sound", 0, 40)
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()            
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ui_state="menu"
            return
        if knob_pressed:
            knob_pressed=False
            print("Knob pressed ",value)
            if value==1:
               selection=await oled_display_get_minutes(1)
               ui_state="menu_clock2"
               if selection !=-1:
                   config["snooze"]=int(selection)
                   save_config(config)
               return
            if value==2:
               selection=await oled_display_get_minutes(2)
               ui_state="menu_clock2"
               if selection!=-1:
                   config["sunrise"]=selection
                   save_config(config)
               return
            if value==3:
#               selection=await oled_display_get_minutes(3)
               selection=await oled_display_get_on_off(1)
               ui_state="menu_clock2"
               print("TZ selection=",selection)
               if selection !=-1:
                   config["tzsync"]=selection
                   save_config(config)
               return
            if value==4:
                selection = await oled_display_get_on_off(4)
                ui_state = "menu_clock2"
                if selection != -1:
                    config["alarmsound"] = selection
                    save_config(config) 
#                 ui_state="menu_clock"
                return        
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(10)            
        
        
        
        
async def oled_display_menu_clock():
    global oled, ui_state, button1_pressed,button3_pressed,rotary,knob_pressed
    
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    setup_rotary(1, 1, 4, False)
    last_value=None
    last_action=time.ticks_ms()
    while ui_state=="menu_clock":
        value=rotary.value()
        if value !=last_value:
            last_value=value
            last_action=time.ticks_ms()
            cursor1 = "  "
            cursor2 = "  "
            cursor3 = "  "
            cursor4 = "  "
            if value == 1:
                cursor1 = " >"
            elif value == 2:
                cursor2 = " >"
            elif value == 3:
                cursor3 = " >"
            elif value==4:
                cursor4=" >"
            oled.fill(0)
            oled.text("CLOCK SETTINGS",0,0)
            oled.text(cursor1 + "Set time", 0, 10)
            oled.text(cursor2 + "Set Timezone", 0, 20)
            oled.text(cursor3 + "Set Date", 0, 30)
            oled.text(cursor4 + "Next page", 0, 40)
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ui_state="menu"
            return
        if knob_pressed:
            knob_pressed=False
            print("Knob pressed ",value)
            if value==1:
               await oled_set_time(False)
               return
            if value==2:
               await oled_set_tz(False)
               return
            if value==3:
               await oled_setup_date(False)
               return
            if value==4:               
                ui_state="menu_clock2"
                return
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(10)    

async def oled_display_menu_presets():
    global oled, ui_state, button1_pressed,button3_pressed,rotary,knob_pressed
    
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    setup_rotary(1, 1, 3, False)
    last_value=None
    last_action=time.ticks_ms()
    while ui_state=="menu_presets":
        value=rotary.value()
        if value !=last_value:
            last_action=time.ticks_ms()
            last_value=value
            cursor1 = "  "
            cursor2 = "  "
            cursor3 = "  "
            cursor4 = "  "
            if value == 1:
                cursor1 = " >"
            elif value == 2:
                cursor2 = " >"
            elif value == 3:
                cursor3 = " >"
            elif value==4:
                cursor4=" >"
            oled.fill(0)
            oled.text("PRESET SETTINGS",0,0)
            oled.text(cursor1 + "Preset 1", 0, 10)
            oled.text(cursor2 + "Preset 2", 0, 20)
            oled.text(cursor3 + "Preset 3", 0, 30)
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ui_state="menu"
            return
        if knob_pressed:
            knob_pressed=False
            print("KNob pressed ",value)
            if value==1:
                # We want to get value in minutes for preset 1
                timevalue=await oled_display_get_time(1)
                if timevalue==-1:
                    return
                config["preset1"]=timevalue
                save_config(config)
                return
            if value==2:
                # We want to get value in minutes for preset 2
                timevalue=await oled_display_get_time(2)
                if timevalue==-1:
                    return
                config["preset2"]=timevalue
                save_config(config)
                return
            if value==3:
                # We want to get value in minutes for preset 3
                timevalue=await oled_display_get_time(3)
                if timevalue==-1:
                    return
                config["preset3"]=timevalue
                save_config(config)
                return
                
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return
        await asyncio.sleep_ms(10)

async def oled_choose_wifi():
    global oled, ui_state, button1_pressed, button3_pressed
    global rotary, knob_pressed, WifiNetworks

    button1_pressed = False
    button3_pressed = False
    knob_pressed = False
    last_action=time.ticks_ms()
    # Read known networks from config
    config = read_network_config()
    known_ssids = [
        net.get("ssid")
        for net in config.get("sta", [])
        if net.get("ssid")
    ]

    # Build list of known networks that are currently visible
    available_ssids = []

    for network_info in WifiNetworks:
        ssid = network_info[0].decode("utf-8")
        if ssid in known_ssids and ssid not in available_ssids:
            available_ssids.append(ssid)

    # Nothing to select
    if not available_ssids:
        oled.fill(0)
        oled.text("SELECT WIFI", 0, 0)
        oled.text("No known WiFi", 0, 20)
        oled.text("networks found", 0, 30)

        oled.fill_rect(0, 54, 32, 9, 1)
        oled.text("EXIT", 0, 55, 0)

        oled.fill_rect(85, 54, 32, 9, 1)
        oled.text("BACK", 85, 55, 0)

        oled.show()

        while not button1_pressed and not button3_pressed:
            if time.ticks_diff(time.ticks_ms(),last_action)>10000:
                button1_pressed=True
            await asyncio.sleep_ms(20)

        if button1_pressed:
            button1_pressed = False
            ui_state = "clock"
        elif button3_pressed:
            button3_pressed = False
            ui_state = "menu_net"

        return -1

    # Rotary selects index in the complete list
    setup_rotary(0, 0, len(available_ssids) - 1, False)

    last_index = None

    while not knob_pressed:
        index = rotary.value()

        if index != last_index:
            last_action=time.ticks_ms()
            last_index = index

            oled.fill(0)
            oled.text("SELECT WIFI", 0, 0)

            # Calculate first visible item
            if index < 4:
                first_item = 0
            elif index > len(available_ssids) - 4:
                first_item = len(available_ssids) - 4
            else:
                first_item = index - 1

            # Display maximum four networks
            for row in range(4):
                item_index = first_item + row

                if item_index >= len(available_ssids):
                    break

                if item_index == index:
                    prefix = "> "
                else:
                    prefix = "  "

                oled.text(
                    prefix + available_ssids[item_index],
                    0,
                    10 + row * 10
                )

            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)

            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)

            oled.show()

        if button1_pressed:
            button1_pressed = False
            ui_state = "clock"
            return -1

        if button3_pressed:
            button3_pressed = False
            ui_state = "menu_net"
            return -1

        if knob_pressed:
            knob_pressed = False

            
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            return -1
        await asyncio.sleep_ms(10)
    knob_pressed=False
    selected_ssid = available_ssids[index]
    oled_connecting_wifi()
    result = await choose_wifi(selected_ssid)
    if result == 0:
        ui_state = "display_network"
        return 0
    else:
        return -1
    return -1
    

async def oled_display_menu_net():
    global oled, ui_state,button1_pressed,button3_pressed,rotary,knob_pressed,WIFI_OFF
    
    button1_pressed=False
    button3_pressed=False
    knob_pressed=False
    last_value=None
    setup_rotary(1,1,4,False)
    while ui_state=="menu_net":
        value=rotary.value()
        if value!=last_value:
            last_value=value
            cursor1="  "
            cursor2="  "
            cursor3="  "
            cursor4="  "
            if value==1:
                cursor1=" >"
            elif value==2:
                cursor2=" >"
            elif value==3:
                cursor3=" >"
            elif value==4:
                cursor4=" >"
            oled.fill(0)
            oled.text("NET SETTINGS",0,0)
            oled.text(cursor1 +"Wifi mode",0,10)
            oled.text(cursor2 +"Select Wifi",0,20)
            oled.text(cursor3 +"Net info",0,30)
            oled.text(cursor4 +"Wifi ON/OFF",0,40)
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.fill_rect(85, 54, 32, 9, 1)
            oled.text("BACK", 85, 55, 0)
            oled.show()
        if button1_pressed:
            button1_pressed=False
            ui_state="clock"
            return
        if button3_pressed:
            button3_pressed=False
            ui_state="menu"
            return   
        if knob_pressed:
            knob_pressed=False
            if value==1:
                print("WifiMode")
                selection=await oled_display_get_on_off(2)
                print("Selection=",selection)
                if selection !=-1:
                    wifi_config=read_network_config()
                    if selection==0:
                        wifi_config["mode"]="ap"
                    else:
                        wifi_config["mode"]="sta"
                    oled_connecting_wifi()
                    await write_network_config(wifi_config)
                    await setup_network_from_config(server)
                    await oled_display_network(False)
                    last_value=None
                    setup_rotary(1,1,4,False)
            if value==2:
                print("Select Wifi")
                await oled_choose_wifi()
                setup_rotary(1,1,4,False)
            if value==3:
                print("Net Info")
                await oled_display_network(True)
            if value==4:
                print("Wifi on/off")
                selection=await oled_display_get_on_off(3)
                if selection!=-1:
                    if selection==0:
                        await disable_wifi()
                        WIFI_OFF=True
                        await oled_display_network(True)
                    else:
                        oled_connecting_wifi()
                        await enable_wifi()
                        WIFI_OFF=False
                        await oled_display_network(True)
                last_value=None
                setup_rotary(1,1,4,False)
        await asyncio.sleep_ms(10) 
    

async def oled_factory_reset():
    global oled,ui_state, button1_pressed,button2_pressed,button3_pressed
    
    button1_pressed=False
    button2_pressed=False
    button3_pressed=False
    
    oled.fill(0)
    oled.text("FACTORY RESET",0,0)
    oled.text("Press OK to do",0,20)
    oled.text("factory reset",0,30)
    oled.fill_rect(0, 54, 32, 9, 1)
    oled.text("EXIT", 0, 55, 0)
    oled.fill_rect(48,54,20,9,1)
    oled.text("OK",50,55,0)
    oled.fill_rect(85, 54, 32, 9, 1)
    oled.text("BACK", 85, 55, 0)
    oled.show()
    
    while ui_state=="factory_reset":
        if button1_pressed:
            ui_state="clock"
            button1_pressed=False
        if button2_pressed:
            # Delete config files and reboot
            for filename in ["wifi_config.json", "config.json"]:
                try:
                    os.remove(filename)
                except Exception as e:
                    print("Error deleting", filename, ":", e)
            await asyncio.sleep(2)
            machine.reset()
        if button3_pressed:
            ui_state="menu"
            button3_pressed=False
        await asyncio.sleep_ms(10)
            

async def oled_display_menu():
    global oled, ui_state, button1_pressed,button3_pressed, rotary, knob_pressed

    button1_pressed = False
    button2_pressed=False
    button3_pressed=False
    knob_pressed=False
    setup_rotary(1, 1, 4, False)
    print("Menu entered")
    last_value = None
    last_action=time.ticks_ms()
    while ui_state == "menu":

        value = rotary.value()

        if value != last_value:
            last_action=time.ticks_ms()
            last_value = value

            cursor1 = "  "
            cursor2 = "  "
            cursor3 = "  "
            cursor4 = "  "

            if value == 1:
                cursor1 = " >"
            elif value == 2:
                cursor2 = " >"
            elif value == 3:
                cursor3 = " >"
            elif value==4:
                cursor4=" >"

            oled.fill(0)
            oled.text("MAIN MENU", 0, 0)
            oled.text(cursor1 + "Clock settings", 0, 10)
            oled.text(cursor2 + "Presets", 0, 20)
            oled.text(cursor3 + "Net settings", 0, 30)
            oled.text(cursor4 + "Factory reset",0,40)
            oled.fill_rect(0, 54, 32, 9, 1)
            oled.text("EXIT", 0, 55, 0)
            oled.show()

        if button1_pressed:
            button1_pressed = False
            ui_state = "clock"
            return
        
        if knob_pressed:
            if value==1:
                knob_pressed=False
                ui_state="menu_clock"
                return
            if value==2:
                knob_pressed=False
                ui_state="menu_presets"
                return
            if value==3:
                knob_pressed=False
                ui_state="menu_net"
                return
            if value==4:
                knob_pressed=False
                ui_state="factory_reset"
                return
        if time.ticks_diff(time.ticks_ms(),last_action)>10000:
            ui_state="clock"
            print("Timeout")
            return
        await asyncio.sleep_ms(10)

def oled_booting():
    global oled
    firmware="Firmware "+ FW_VERSION
    oled.fill(0)
    oled.text("Please wait !",0,0)
    oled.text("The clock is",0,20)
    oled.text("starting up",0,30)
    oled.text(firmware,0,50)
    oled.show()
    
def oled_connecting_wifi():
    global oled
    oled.fill(0)
    oled.text("Please wait !",0,0)
    oled.text("Network is being ",0,20)
    oled.text("reconfigured",0,30)
    oled.show()
    
async def setup_wizard():
    global web_setup_requested,oled_setup_requested
    # This wizard assumes, that the clock has not been setup, and it creates the initial config and wifi config
#    await setup_network_from_config(server)
    await oled_wizard1()
    if web_setup_requested:
        await oled_websetup()
    if oled_setup_requested:
        await oled_set_time(True)
        await oled_set_tz(True)
        await oled_setup_date(True)
        
       
    
    


async def deployment_check():
    # This function is called to perform first time deployment tasks when the device is taken into use.
    # This is executed if no settings files are found
    # We need to find two json files, if they are not present they will be created.
    if not config_exists():
        await setup_wizard()

    config = {}
    wifi = {}
    return config,wifi

async def handle_alarm_switch():
    global alarm_switch, alarm_switch_changed,ui_state,TIME_ALARM,ALARM_STATE,SUNRISE_CANCELLED,SNOOZE_UNTIL,oled
    #Check if actions need to be taken based on the alarm swich change
    ALARM_STATE=0 #Always reset
    SUNRISE_CANCELLED=False #Always reset
    SNOOZE_UNTIL=0 #Always reset
    
    #If the switch is off, we do nothing
    #NOTE value 1 means it is off, and 0 means on
    if alarm_switch.value()==1:
            print("Alarm switch is off")
            ui_state="clock"
            oled.contrast(128)
            return
    print("Alarm Switch is on,")
    # The switch was turned on, we need to ask for alarm time
    current_ui=ui_state # we save this so we can return to same state after alarm is set

    alarmtime= await oled_display_get_time(4)
    print("Alarm time=",alarmtime)
    if alarmtime!=-1:
        #Set the alarm
        config["alarmtime"]=alarmtime
        save_config(config)
        TIME_ALARM=alarmtime # Set the global variable
        oled.contrast(15) #Make the display a bit dimmer
    ui_state="clock"
    
    
async def alarm_switch_task():
    global alarm_switch_changed,ui_state

    while True:
        if alarm_switch_changed:
            alarm_switch_changed = False
            ui_state="set_alarm"
        await asyncio.sleep_ms(20)
        
async def check_alarm():
    global TIME_ALARM,ALARM_STATE,SNOOZE_UNTIL
    # Raise the alarm if the alarm is active and it is correct time
    if alarm_switch.value()==1: # Alarm is not active, exit
        ALARM_STATE=0
        await set_led1(0)
        return
    print("ALARM_STATE=",ALARM_STATE)
    now=Time2Minutes()
    if ALARM_STATE==0:
        print("TIME_ALARM=",TIME_ALARM)

        print("Time2Minutes=",now)
        if TIME_ALARM==now:
            ALARM_STATE=1
            await set_led1(1)
           # await alarm_audio()
            print("Raise alarm")
    if ALARM_STATE==2: #Snooze was activated
        print("Snooze until ",SNOOZE_UNTIL)
        if SNOOZE_UNTIL==now:
            ALARM_STATE=1
            print("Raise Snooze Alarm")
    
async def check_sunrise():
    global TIME_ALARM, SUNRISE_STATE, SUNRISE_CANCELLED,ALARM_STATE,SUNRISE_FORCE

    # If sunrise is forced, the set it and exit
    if SUNRISE_FORCE:
        SUNRISE_STATE=5
        await set_led2_brightness(SUNRISE_STATE)
        await set_led3_brightness(SUNRISE_STATE)
        return
    
    # Alarm is not active
    if alarm_switch.value() == 1:
        SUNRISE_STATE = 0
        await set_led2_brightness(SUNRISE_STATE)
        await set_led3_brightness(SUNRISE_STATE)
        return

    now = Time2Minutes()
    sunrise_time = int(config["sunrise"])

    if sunrise_time == 0:
        SUNRISE_STATE = 0
        await set_led2_brightness(SUNRISE_STATE)
        await set_led3_brightness(SUNRISE_STATE)
        return

    # How many minutes until the alarm?
    until_alarm = (TIME_ALARM - now) % 1440

    # If sunrise has not started yet
    if until_alarm > sunrise_time:
        SUNRISE_STATE = 0

    else:
        # Sunrise is in progress
        elapsed = sunrise_time - until_alarm

        level_time = sunrise_time / 5

        if elapsed < level_time:
            SUNRISE_STATE = 1
        elif elapsed < level_time * 2:
            SUNRISE_STATE = 2
        elif elapsed < level_time * 3:
            SUNRISE_STATE = 3
        elif elapsed < level_time * 4:
            SUNRISE_STATE = 4
        else:
            SUNRISE_STATE = 5

    if SUNRISE_CANCELLED:
        SUNRISE_STATE = 0
        print("SUNRISE CANCELLED")
    if ALARM_STATE==1:
        SUNRISE_STATE=5

    print("SUNRISE_STATE =", SUNRISE_STATE)
    await set_led2_brightness(SUNRISE_STATE)
    await set_led3_brightness(SUNRISE_STATE)

async def check_powersave():
    global wifi_timeout,WIFI_OFF
    
    # Implement powersaving functions if necessary
    
    # 1st check, is the Wifi off deliberatly
    if WIFI_OFF:
        return
    # 2nd check, if we are on mains power, this is not necessary
    
    if await check_powerstate()<2: # We are charging or we are connected mains with full battery
        # Let's make sure, that the wifi is on
        await enable_wifi()
        return
    
    # 2nd check, if the Wifi timeout expired
    if time.ticks_diff(time.ticks_ms(),wifi_timeout)<0:
        return
    
    # Both checks failed, let's shutdown Wifi
    await disable_wifi()
        

async def scheduler_task():

    await asyncio.sleep(30)

    await check_ntp(force=True)

    while True:

       
        await check_ntp()
        await check_sunrise()
        await check_alarm()
        await check_powersave()


        await asyncio.sleep(2)

async def main():
    global ui_state,WifiNetworks
    # Initialize audio hardware
    audio_player.init()
    oled_booting()
    WifiNetworks=await server.scan_networks(server)
    await setup_network_from_config(server)
    config,wifi=await deployment_check()
    
    await load_config()



    # Start background tasks
    asyncio.create_task(scheduler_task())
    asyncio.create_task(alarm_switch_task())
    asyncio.create_task(audio_task())
    
    while True:
        if ui_state=="clock":
            await oled_display_clock()
        if ui_state=="display_network":
            await oled_display_network()
        if ui_state=="menu":
            await oled_display_menu()
        if ui_state=="menu_clock":
            await oled_display_menu_clock()
        if ui_state=="menu_clock2":
            await oled_display_menu_clock2()
        if ui_state=="menu_presets":
            await oled_display_menu_presets()
        if ui_state=="menu_net":
            await oled_display_menu_net()
        if ui_state=="factory_reset":
            await oled_factory_reset()
        if ui_state=="set_alarm":
            await handle_alarm_switch()
            
    
 # Create webserver
wifi_config = read_network_config()
server = GurgleAppsWebserver(
    hostname=wifi_config["hostname"],
    port=80,
    timeout=20,
    doc_root="/www/",
    log_level=2
)

server.add_function_route("/deletewifi/<wifissid>",deletewifi)
server.add_function_route("/switchtoap",switchtoap)
server.add_function_route("/connectwifi/<wifissid>",connectwifi)
server.add_function_route("/joinwifi/<wifissid>/<wifipw>",joinwifi)
server.add_function_route("/rename_ap/<apname>",rename_ap)
server.add_function_route("/change_ap_pw/<newpw>",change_ap_pw)
server.add_function_route("/getnetconfig",getnetconfig)
server.add_function_route("/changehostname/<newhostname>",changehostname)
server.add_function_route("/getclockconfig",getclockconfig)
server.add_function_route("/saveclockconfig/<TimeZone>/<snooze>/<sunrise>/<preset1>/<preset2>/<preset3>/<tzsync>/<utc_time>/<alarmsound>",saveclockconfig)
server.add_function_route("/savewizard/<TimeZone>/<snooze>/<sunrise>/<utc_time>",savewizard)
server.add_function_route("/getsetupstate",getsetupstate)
server.add_function_route("/startsetup",startsetup)
server.add_function_route("/factoryreset",factoryreset)
asyncio.run(server.start_server_with_background_task(main))
