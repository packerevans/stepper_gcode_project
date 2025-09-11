from flask import Flask, render_template, request, jsonify
import serial
import time
import threading

arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
time.sleep(2)

app = Flask(__name__)
serial_log = []
lock = threading.Lock()

def send_command(command):
    """Send command to Arduino and log response."""
    with lock:
        serial_log.append(f"> {command}")
    arduino.write((command + '\n').encode())
    arduino.flush()

@app.route('/')
def index():
    return render_template('index.html')

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
