from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import serial
import time
import threading
import subprocess  # for reboot command

arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
time.sleep(2)

app = Flask(__name__)
app.secret_key = 'your-secret-key'  # Required for flashing messages

serial_log = []  # Global list to store logs
lock = threading.Lock()  # Thread-safe log writing

def send_command(command):
    """Send command to Arduino and log response."""
    with lock:
        serial_log.append(f"> {command}")
    arduino.write((command + '\n').encode())
    arduino.flush()

    while True:
        response = arduino.readline().decode().strip()
        if response:
            with lock:
                serial_log.append(f"< {response}")
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
        gcode_file = None
        if design == "Snowflake":
            gcode_file = "designs/snowflake.gcode"
        elif design == "Lines":
            gcode_file = "designs/lines.gcode"

        if gcode_file:
            try:
                with open(gcode_file, "r") as file:
                    for line in file:
                        line = line.strip()
                        if not line or line.startswith(";"):
                            continue
                        send_command(line)
            except Exception as e:
                with lock:
                    serial_log.append(f"[ERROR] {str(e)}")
        return redirect(url_for('designs'))
    return render_template('designs.html')

@app.route('/terminal')
def terminal():
    return render_template('terminal.html')

@app.route('/terminal/logs')
def terminal_logs():
    with lock:
        # Return the last 100 lines (to keep it light)
        return jsonify(serial_log[-100:])

@app.route('/reboot', methods=['POST'])
def reboot():
    try:
        # Log reboot request
        with lock:
            serial_log.append("[INFO] System reboot initiated by user.")
        # Flash a message to user
        flash("System reboot initiated.", "info")
        # Perform reboot (Linux)
        subprocess.Popen(['sudo', 'reboot'])
    except Exception as e:
        flash(f"Failed to reboot system: {e}", "error")
    return redirect(url_for('terminal'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
