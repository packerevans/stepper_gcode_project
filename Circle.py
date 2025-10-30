import serial
import time

# ---------- CONFIGURATION ----------
# *** FIXED: Changed this to the Linux port you specified. ***
SERIAL_PORT = '/dev/ttyACM0'  # This was 'COM3'
BAUD_RATE = 9600
TIMEOUT = 1  # seconds

# ---------- G1 COMMANDS ----------
# (Your G-code list is quite long, so I'll just show the first few and the last one)
gcode_commands = [
"G1 -127 0 1000",
"G1 0 4 1000",
"G1 0 5 1000",
"G1 -1 4 1000",
"G1 -1 4 1000",
"G1 -1 4 1000",
"G1 -1 5 1000",
"G1 -1 4 1000",
"G1 -2 4 1000",
"G1 -1 4 1000",
"G1 -2 3 1000",
"G1 -3 4 1000",
"G1 -2 4 1000",
"G1 -2 3 1000",
"G1 -3 4 1000",
"G1 -3 3 1000",
"G1 -3 3 1000",
"G1 -3 3 1000",
"G1 -3 3 1000",
"G1 -4 3 1000",
"G1 -3 2 1000",
"G1 -4 2 1000",
"G1 -4 3 1000",
"G1 -3 2 1000",
"G1 -4 1 1000",
"G1 -4 2 1000",
"G1 -4 1 1000",
"G1 -5 1 1000",
"G1 -4 1 1000",
"G1 -4 1 1000",
"G1 -4 1 1000",
"G1 -4 0 1000",
"G1 -9 0 1000",
"G1 -5 0 1000",
"G1 -4 -1 1000",
"G1 -4 -1 1000",
"G1 -4 -1 1000",
"G1 -5 -1 1000",
"G1 -4 -1 1000",
"G1 -4 -2 1000",
"G1 -4 -1 1000",
"G1 -3 -2 1000",
"G1 -4 -3 1000",
"G1 -4 -2 1000",
"G1 -3 -2 1000",
"G1 -4 -3 1000",
"G1 -3 -2 1000",
"G1 -3 -2 1000",
"G1 -3 -4 1000",
"G1 -3 -4 1000",
"G1 -2 -3 1000",
"G1 -1 -4 1000",
"G1 -3 -2 1000",
"G1 -2 -2 1000",
"G1 -2 -4 1000",
"G1 -2 -3 1000",
"G1 -1 -4 1000",
"G1 -2 -4 1000",
"G1 -1 -4 1000",
"G1 -2 -5 1000",
"G1 -1 -4 1000",
"G1 0 -4 1000",
"G1 -1 -4 1000",
"G1 0 -5 1000",
"G1 0 -4 1000",
"G1 0 -4 1000",
"G1 0 -5 1000",
"G1 1 -4 1000",
"G1 1 -8 1000",
"G1 2 -4 1000",
"G1 1 -5 1000",
"G1 2 -4 1000",
"G1 1 -4 1000",
"G1 2 -3 1000",
"G1 2 -4 1000",
"G1 3 -3 1000",
"G1 2 -4 1000",
"G1 2 -3 1000",
"G1 3 -4 1000",
"G1 4 -3 1000",
"G1 3 -2 1000",
"G1 3 -4 1000",
"G1 4 -3 1000",
"G1 3 -2 1000",
"G1 4 -2 1000",
"G1 3 -1 1000",
"G1 4 -2 1000",
"G1 4 -3 1000",
"G1 4 -2 1000",
"G1 4 -1 1000",
"G1 5 -1 1000",
"G1 4 -1 1000",
"G1 4 -1 1000",
"G1 4 -1 1000",
"G1 5 0 1000",
"G1 4 0 1000",
"G1 4 0 1000",
"G1 5 0 1000",
"G1 3 2 1000",
"G1 5 0 1000",
"G1 4 1 1000",
"G1 5 2 1000",
"G1 4 2 1000",
"G1 4 1 1000",
"G1 4 2 1000",
"G1 3 0 1000",
"G1 4 3 1000",
"G1 4 2 1000",
"G1 3 2 1000",
"G1 4 3 1000",
"G1 3 3 1000",
"G1 3 3 1000",
"G1 3 3 1000",
"G1 3 3 1000",
"G1 3 7 1000",
"G1 2 4 1000",
"G1 2 4 1000",
"G1 3 4 1000",
"G1 2 3 1000",
"G1 1 0 1000",
"G1 2 4 1000",
"G1 1 4 1000",
"G1 1 5 1000",
"G1 1 4 1000",
"G1 1 4 1000",
"G1 1 4 1000",
"G1 0 5 1000",
"G1 0 4 1000",
"G1 -88 0 1000"
]

# ---------- SERIAL CONNECTION ----------
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT)
    time.sleep(2)  # give Arduino time to reset
    print("Connected to Arduino on", SERIAL_PORT)
except Exception as e:
    print("Error opening serial port:", e)
    exit(1)

# ---------- SEND COMMANDS ONE BY ONE ----------
for cmd in gcode_commands:
    cmd = cmd.strip()
    if not cmd:
        continue

    # Send command
    ser.write((cmd + '\n').encode('utf-8'))
    print(f"Sent: {cmd}")

    # Wait for Arduino to respond with "Done"
    while True:
        line = ser.readline().decode('utf-8').strip()
        if line:
            print(f"Arduino: {line}")
            if line.lower() == "done":
                break  # move to next command

# ---------- FINISH ----------
print("All commands sent.")
ser.close()
