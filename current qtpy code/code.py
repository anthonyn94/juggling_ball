import time
import board
import math
import neopixel
import busio
import digitalio

import adafruit_mpu6050

from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# =====================================================
# CONFIG
# =====================================================

DEVICE_ID        = "ball_D"

NUM_PIXELS       = 3
PIXEL_PIN        = board.A0
PIXEL_BRIGHTNESS = 1

BUTTON_PIN       = board.D0

IMU_INTERVAL     = 0.01
TAP_THRESHOLD    = 18
TAP_COOLDOWN     = 0.3
WHITE_HOLD       = 0.5
LONG_PRESS       = 1.0
DEBOUNCE         = 0.02

SOLID_BLUE       = (0, 0, 255)

# =====================================================
# I2C + IMU (MPU6050 — no power-enable pin needed)
# =====================================================

i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c)
print("IMU ready")

# =====================================================
# NEOPIXEL
# =====================================================

pixel = neopixel.NeoPixel(
    PIXEL_PIN,
    NUM_PIXELS,
    brightness=PIXEL_BRIGHTNESS,
    auto_write=True,
    pixel_order=neopixel.GRB  # MPU6050 boards typically use GRB; change to RGB if colors look wrong
)

# =====================================================
# BUTTON
# =====================================================

button = digitalio.DigitalInOut(BUTTON_PIN)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP

# =====================================================
# BLE
# =====================================================

ble = BLERadio()
uart = UARTService()
advertisement = ProvideServicesAdvertisement(uart)
advertisement.complete_name = DEVICE_ID

try:
    ble.stop_advertising()
except Exception:
    pass

ble.start_advertising(advertisement)

# =====================================================
# STATE
# =====================================================

mode           = "rainbow"
awake          = True

last_send      = 0
last_tap       = 0
rainbow_offset = 0
tapped         = False

button_last    = False
press_start    = None
long_handled   = False

cmd_buffer     = ""
override_color = None

# =====================================================
# HELPERS
# =====================================================

def set_pixels(color):
    for i in range(NUM_PIXELS):
        pixel[i] = color


def wheel(pos):
    pos = pos % 255
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    else:
        pos -= 170
        return (pos * 3, 0, 255 - pos * 3)


def update_rainbow():
    global rainbow_offset
    # Spread pixels across the wheel so multi-pixel strips look nice
    for i in range(NUM_PIXELS):
        pixel[i] = wheel(rainbow_offset + (i * 40))
    rainbow_offset = (rainbow_offset + 2) % 255


def go_to_sleep():
    global awake
    awake = False
    set_pixels((0, 0, 0))
    try:
        ble.stop_advertising()
    except Exception:
        pass
    print("Sleeping")


def wake_up():
    global awake, rainbow_offset, mpu, i2c

    awake = True

    # Re-initialise I2C + IMU after sleep
    try:
        i2c.deinit()
    except Exception:
        pass

    time.sleep(0.05)
    i2c = board.I2C()
    mpu = adafruit_mpu6050.MPU6050(i2c)

    rainbow_offset = 0
    try:
        ble.start_advertising(advertisement)
    except Exception:
        pass
    print("Awake")


def handle_tap(now, magnitude=0.0):
    global tapped, last_tap
    msg = f"TAP|{DEVICE_ID}|{now:.4f}|{magnitude:.2f}\n"
    if ble.connected:
        uart.write(msg.encode())
    print("TAP:", msg.strip())
    set_pixels((255, 255, 255))
    tapped = True
    last_tap = now

# =====================================================
# COMMAND HANDLER
# =====================================================

def handle_command(cmd):
    global mode, override_color, tapped

    parts = cmd.strip().split("|")
    if not parts:
        return

    verb = parts[0].upper()

    if verb == "COLOR":
        if len(parts) == 2 and parts[1].upper() == "RESET":
            override_color = None
            tapped = False
            print("CMD: color reset")
        elif len(parts) == 4:
            try:
                r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                override_color = (r, g, b)
                tapped = False
                print(f"CMD: color -> {override_color}")
            except ValueError:
                print(f"CMD: bad COLOR args: {cmd}")

    elif verb == "MODE":
        if len(parts) == 2:
            new_mode = parts[1].lower()
            if new_mode in ("rainbow", "blue"):
                mode = new_mode
                override_color = None
                tapped = False
                print(f"CMD: mode -> {mode}")

    else:
        print(f"CMD: unknown verb: {verb}")


def poll_commands():
    global cmd_buffer
    if not uart.in_waiting:
        return
    try:
        chunk = uart.read(uart.in_waiting).decode("utf-8")
        cmd_buffer += chunk
        while "\n" in cmd_buffer:
            line, cmd_buffer = cmd_buffer.split("\n", 1)
            line = line.strip()
            if line:
                handle_command(line)
    except Exception as e:
        print("CMD read error:", e)

# =====================================================
# BUTTON HELPERS
# =====================================================

def wait_for_release():
    while not button.value:
        time.sleep(0.01)
    time.sleep(DEBOUNCE)


def read_button_duration():
    start = time.monotonic()
    time.sleep(DEBOUNCE)
    if button.value:
        return 0.0
    wait_for_release()
    return time.monotonic() - start


def sleep_loop():
    print("Entering sleep loop — long press to wake")
    while True:
        if not button.value:
            duration = read_button_duration()
            print(f"Sleep press: {duration:.2f}s")
            if duration >= LONG_PRESS:
                wake_up()
                return
        time.sleep(0.02)

# =====================================================
# LOOP
# =====================================================

print("Starting")

while True:
    now = time.monotonic()

    # ------------------------------------------------
    # SLEEP GATE
    # ------------------------------------------------

    if not awake:
        sleep_loop()
        now = time.monotonic()
        button_last = False

    # ------------------------------------------------
    # INCOMING COMMANDS
    # ------------------------------------------------

    if ble.connected:
        poll_commands()

    # ------------------------------------------------
    # BUTTON
    # ------------------------------------------------

    btn = not button.value

    if btn and not button_last:
        press_start = now
        long_handled = False

    elif btn and button_last:
        if not long_handled and press_start and (now - press_start >= LONG_PRESS):
            long_handled = True
            go_to_sleep()
            continue

    elif not btn and button_last:
        if not long_handled and press_start is not None:
            mode = "blue" if mode == "rainbow" else "rainbow"
            override_color = None
            tapped = False
            print("Mode:", mode)
        press_start = None

    button_last = btn

    # ------------------------------------------------
    # BLE KEEPALIVE
    # ------------------------------------------------

    if not ble.connected and not ble.advertising:
        try:
            ble.start_advertising(advertisement)
        except Exception:
            pass

    # ------------------------------------------------
    # SENSOR READ
    # ------------------------------------------------

    if not awake:
        continue

    try:
        ax, ay, az = mpu.acceleration
        magnitude = math.sqrt(ax * ax + ay * ay + az * az)
    except OSError:
        print("IMU read error — skipping")
        continue

    # ------------------------------------------------
    # TAP DETECTION
    # ------------------------------------------------

    if magnitude > TAP_THRESHOLD and (now - last_tap > TAP_COOLDOWN):
        handle_tap(now, magnitude)

    # ------------------------------------------------
    # TAP WHITE TIMEOUT
    # ------------------------------------------------

    if tapped and (now - last_tap > WHITE_HOLD):
        tapped = False

    # ------------------------------------------------
    # IMU STREAM
    # format: I|<ball_ts>|<ax>|<ay>|<az>|<gx>|<gy>|<gz>
    # ball_ts is time.monotonic() on the ball — free-running from boot
    # ------------------------------------------------

    if ble.connected and (now - last_send > IMU_INTERVAL):
        gx, gy, gz = mpu.gyro
        msg = f"I|{now:.4f}|{ax:.1f}|{ay:.1f}|{az:.1f}|{gx:.1f}|{gy:.1f}|{gz:.1f}\n"
        uart.write(msg.encode())
        last_send = now

    # ------------------------------------------------
    # VISUAL UPDATE
    # ------------------------------------------------

    if tapped:
        pass  # hold white until WHITE_HOLD expires
    elif override_color is not None:
        set_pixels(override_color)
    elif mode == "rainbow":
        update_rainbow()
    else:
        set_pixels(SOLID_BLUE)