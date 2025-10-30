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
   "G1 -151 0 1000, " 
"G1 33 63 1000, " 
"G1 -70 -12 1000, " 
"G1 -49 51 1000, " 
"G1 -10 -70 1000, " 
"G1 -64 -31 1000, " 
"G1 64 -31 1000, " 
"G1 10 -70 1000, " 
"G1 49 51 1000, " 
"G1 70 -12 1000, " 
"G1 -33 63 1000, " 
"G1 -64 0 1000, " 
]

# ---------- SERIAL CONNECTION ----------
try:
    # Open the serial port
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT)
    print(f"Opening serial port {SERIAL_PORT} at {BAUD_RATE} baud...")
    
    # Wait for the connection to be established (e.g., for Arduino to reset)
    time.sleep(2)  
    
    # Read any initial messages from the device
    initial_message = ser.read_all().decode('utf-8')
    if initial_message:
        print(f"Device connected. Initial message:\n{initial_message.strip()}")
    else:
        print(f"Connected to device on {SERIAL_PORT}")

except Exception as e:
    print(f"Error opening serial port {SERIAL_PORT}: {e}")
    print("Please check the port name, baud rate, and device connection.")
    print("If on Linux, make sure you have permissions (e.g., 'sudo chmod a+rw /dev/ttyACM0')")
    exit(1)

# ---------- SEND COMMANDS ONE BY ONE ----------
print("\nStarting to send G-code commands...")

for cmd in gcode_commands:
    cmd = cmd.strip()  # Clean up whitespace
    if not cmd or cmd.startswith(';'):
        continue  # Skip empty lines or comments

    # Send the command
    try:
        ser.write((cmd + '\n').encode('utf-8'))
        print(f"Sent: {cmd}")

        # Wait for the device to respond with "ok" or "Done"
        # This is a simple handshake protocol.
        response_received = False
        while not response_received:
            line = ser.readline().decode('utf-8').strip()
            
            if not line:
                # Timeout occurred, no response
                print("Warning: No response from device, continuing to next command...")
                break # Break inner loop and move to next command

            print(f"Device: {line}")
            
            # *** IMPROVED: Check for 'ok' (common for GRBL/Marlin) or 'done' ***
            if line.lower() == "ok" or line.lower() == "done":
                response_received = True
                break # Exit the while loop, move to next command

    except Exception as e:
        print(f"Error during serial communication: {e}")
        break # Exit the for loop on error

# ---------- FINISH ----------
print("\nAll commands sent.")
ser.close()
print("Serial port closed.")
