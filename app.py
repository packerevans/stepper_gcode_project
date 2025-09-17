from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import serial
import threading
import time

app = Flask(__name__)
app.secret_key = "supersecret"  # Needed for flash messages

# === SERIAL SETUP ===
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)  # Adjust for your device
time.sleep(2)  # let Arduino reset

serial_log = []
lock = threading.Lock()

def log_message(msg):
    """Thread-safe append to serial_log with size limit."""
    with lock:
        serial_log.append(msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# Background thread: read Arduino logs
def read_from_serial():
    while True:
        try:
            line = arduino.readline().decode(errors="ignore").strip()
            if line:
                log_message(line)
        except Exception as e:
            log_message(f"Error: {e}")

threading.Thread(target=read_from_serial, daemon=True).start()


# ---------------- ROUTES ----------------

@app.route("/")
def index():
    """Main slider + button UI"""
    return render_template("index.html")


@app.route("/terminal")
def terminal():
    """Live log terminal"""
    return render_template("terminal.html")


@app.route("/reboot", methods=["POST"])
def reboot():
    """Send reboot command"""
    arduino.write(b"REBOOT\n")
    log_message("Sent: REBOOT")
    flash("System reboot command sent", "info")
    return redirect(url_for("index"))


@app.route("/set_value")
def set_value():
    """Set ARM or BASE values via slider"""
    vtype = request.args.get("type")
    value = request.args.get("value")
    if vtype and value:
        cmd = f"{vtype}:{value}"
        arduino.write((cmd + "\n").encode())
        log_message(f"Sent: {cmd}")
        return f"{vtype} set to {value}"
    return "Invalid request", 400


@app.route("/command")
def command():
    """Handle pause/resume commands"""
    cmd = request.args.get("type")
    if cmd:
        cmd = cmd.upper()
        arduino.write((cmd + "\n").encode())
        log_message(f"Sent: {cmd}")
        return f"Command {cmd} sent"
    return "Invalid command", 400


@app.route("/send", methods=["POST"])
def send_command():
    """Send a raw command (used by terminal form/JS)"""
    data = request.json
    cmd = data.get("command")
    if cmd:
        arduino.write((cmd + "\n").encode())
        log_message(f"Sent: {cmd}")
        return jsonify(success=True)
    return jsonify(success=False), 400


@app.route("/terminal/logs")
def get_logs():
    """Return recent serial logs"""
    with lock:
        return jsonify(serial_log)


# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
