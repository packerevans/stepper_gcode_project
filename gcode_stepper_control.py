import serial
import time

# Initialize serial port
ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
time.sleep(2)  # wait for Arduino to reset

# Function to send command
def send_command(a, l, u):
    command = f"A{a} L{l} U{u}\n"
    print(f"Sending: {command.strip()}")
    ser.write(command.encode())
    time.sleep(0.1)

# Main sequence
for i in range(42):
    # First: CW lower arm
    send_command(1, 78, 360)
    time.sleep(5)  # Wait for move to finish (adjust if needed)

for i in range(42):
    # Second: CCW lower arm
    send_command(1, -78, 360)
    time.sleep(5)  # Wait for move to finish (adjust if needed)

print("Done.")
ser.close()
