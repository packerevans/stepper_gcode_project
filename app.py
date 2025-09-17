from flask import Flask, render_template, request, jsonify
import serial
import threading
import time

app = Flask(__name__)

# === SERIAL SETUP ===
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)  # Adjust port for your system
time.sleep(2)  # give Arduino time to reset

serial_log = []
lock = threading.Lock()

def log_message(msg):
    with lock:
        serial_log.append(msg)
        if len(serial_log) > 200:  # keep log short
            serial_log.pop(0)

# Background thread to read Arduino logs
def read_from_serial():
    while True:
        try:
            line = arduino.readline().decode(errors="ignore").strip()
            if line:
                log_message(line)
        except Exception as e:
            log_message(f"Error: {e}")

threading.Thread(target=read_from_serial, daemon=True).start()

@app.route("/")
def index():
    return render_template("terminal.html")

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    if cmd:
        arduino.write((cmd + "\n").encode())
        log_message(f"Sent: {cmd}")
        return jsonify(success=True)
    return jsonify(success=False), 400

@app.route("/terminal/logs")
def get_logs():
    with lock:
        return jsonify(serial_log)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
