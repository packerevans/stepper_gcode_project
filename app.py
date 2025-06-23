from flask import Flask, render_template, request, redirect, url_for
import serial
import time
import os

# Initialize serial connection to Arduino
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
time.sleep(2)  # Give Arduino time to reset

app = Flask(__name__)

def send_command(command):
    """Send command and wait for Arduino to confirm it's done."""
    print(f"Sending: {command}")
    arduino.write((command + '\n').encode())
    arduino.flush()

    # Wait for Arduino to respond with "Movement complete." or "Paused"
    while True:
        response = arduino.readline().decode().strip()
        if response:
            print(f"Arduino: {response}")
        if "Movement complete." in response or "Paused" in response:
            break

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        command = request.form.get('command')
        repeat = int(request.form.get('repeat', 1))
        if command:
            for _ in range(repeat):
                send_command(command)
        return redirect(url_for('index'))
    return render_template('index.html')

@app.route('/designs', methods=['GET', 'POST'])
def designs():
    if request.method == 'POST':
        design = request.form.get('design')
        if design == "Snowflake":
            filepath = os.path.join("designs", "snowflake.gcode")
            try:
                with open(filepath, "r") as file:
                    for line in file:
                        line = line.strip()
                        if not line or line.startswith(";"):
                            continue
                        send_command(line)
            except Exception as e:
                print(f"Error reading G-code file: {e}")
        return redirect(url_for('designs'))
    return render_template('designs.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
