from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import serial, threading, time, subprocess

app = Flask(__name__)

# === SERIAL SETUP ===
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
time.sleep(2)

serial_log = []
lock = threading.Lock()

def log_message(msg):
    with lock:
        serial_log.append(msg)
        if len(serial_log) > 200:  # limit length
            serial_log.pop(0)

# Background reader thread
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
    return render_template("index.html")

@app.route("/terminal")
def terminal():
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

@app.route("/reboot", methods=["POST"])
def reboot():
    log_message("System is rebooting…")
    subprocess.Popen(["sudo", "reboot"])  # real Pi reboot
    return jsonify(success=True, message="Rebooting Raspberry Pi…")
