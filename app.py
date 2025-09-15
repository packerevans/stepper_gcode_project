from flask import Flask, render_template, request, jsonify, send_from_directory
import serial
import time
import threading
import os

# Open serial connection to Arduino
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
time.sleep(2)

app = Flask(__name__)
serial_log = []
lock = threading.Lock()

def send_command(command):
    """Send command to Arduino and log it."""
    with lock:
        serial_log.append(f"> {command}")
    arduino.write((command + '\n').encode())
    arduino.flush()

@app.route('/')
def index():
    return render_template('index.html')  # main slider control page

@app.route('/set_value')
def set_value():
    vtype = request.args.get('type')
    value = request.args.get('value')
    if vtype and value:
        send_command(f"{vtype}:{value}")
        return f"{vtype} set to {value}"
    return "Invalid request", 400

@app.route('/get_values')
def get_values():
    send_command("GET")
    time.sleep(0.1)  # allow Arduino to respond
    response = arduino.readline().decode().strip()
    # Expect format: ARM=#### BASE=####
    values = {"ARM": 0, "BASE": 0}
    if response.startswith("ARM="):
        try:
            parts = response.split()
            values["ARM"] = int(parts[0].split("=")[1])
            values["BASE"] = int(parts[1].split("=")[1])
        except:
            pass
    return jsonify(values)

@app.route('/command')
def command():
    """Handle pause/resume commands from buttons."""
    cmd = request.args.get('type')
    if cmd == "pause":
        send_command("PAUSE")
        return "Paused"
    elif cmd == "resume":
        send_command("RESUME")
        return "Resumed"
    return "Invalid command", 400

# ---------- NEW: Designs route ----------
@app.route('/designs')
def designs():
    """List available .gcode design files."""
    designs_path = os.path.join(os.getcwd(), "designs")
    files = []
    if os.path.exists(designs_path):
        files = [f for f in os.listdir(designs_path) if f.endswith(".gcode")]
    return render_template("designs.html", files=files)

@app.route('/designs/<filename>')
def download_design(filename):
    """Serve .gcode file for download or use."""
    return send_from_directory("designs", filename)

# ---------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
