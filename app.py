from flask import Flask, render_template, request, redirect, url_for
import serial
import time

# Initialize serial connection to Arduino
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
time.sleep(2)  # Give Arduino time to reset

app = Flask(__name__)

def send_command(command):
    """Send command and wait for Arduino to confirm it's done."""
    print(f"Sending: {command}")
    arduino.write((command + '\n').encode())
    arduino.flush()

    # Wait for "Movement complete." confirmation
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
        if design == "Spiral":
            send_command("A1 L100 U3200")
        elif design == "Zigzag":
            send_command("A1 L300 U300")
        elif design == "Circle":
            send_command("A1 L50 U3200")
        elif design == "Wave":
            send_command("A1 L200 U200")
        return redirect(url_for('designs'))
    return render_template('designs.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
