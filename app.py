from flask import Flask, render_template, request, jsonify, Response
import serial, threading, time, subprocess
import asyncio
import ble_controller  # Import the BLE handler

app = Flask(__name__)

# === NGROK HEADER FIX ===
@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === SERIAL SETUP ===
arduino = None
arduino_connected = False

try:
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
    time.sleep(2)
    arduino_connected = True
    print("✅ Arduino connected on /dev/ttyACM0")
except Exception as e:
    print(f"⚠️ Arduino not connected: {e}")

serial_log = []
lock = threading.Lock()

def log_message(msg):
    with lock:
        serial_log.append(msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# Background reader thread
def read_from_serial():
    while arduino_connected:
        try:
            line = arduino.readline().decode(errors="ignore").strip()
            if line:
                log_message(line)
        except Exception as e:
            log_message(f"Error: {e}")

if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    if not arduino_connected:
        return render_template("connect.html")
    return render_template("index.html")

@app.route("/terminal")
def terminal():
    if not arduino_connected:
        return render_template("connect.html")
    return render_template("terminal.html")

@app.route("/led_controls")
def led_controls():
    return render_template("led_controls.html")

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command", "").strip()  # always sanitize
    cmd_upper = cmd.upper()

    if not cmd:
        return jsonify(success=False), 400

    print(f"[SEND] Received command: {cmd}")  # Debug print

    try:
        # --- BLE Commands ---
        if cmd_upper.startswith("LED:"):
            parts = cmd.split(":")[1].split(",")
            r, g, b = map(int, parts)
            asyncio.run(ble_controller.send_led_command(r, g, b))

        elif cmd_upper == "CONNECT":
            asyncio.run(ble_controller.connect())

        elif cmd_upper == "DISCONNECT":
            asyncio.run(ble_controller.disconnect())

        elif cmd_upper == "POWER:ON":
            asyncio.run(ble_controller.power_on())

        elif cmd_upper == "POWER:OFF":
            asyncio.run(ble_controller.power_off())

        # --- Arduino fallback (manual commands) ---
        else:
            if arduino_connected and arduino:
                arduino.write((cmd + "\n").encode())

        log_message(f"Sent: {cmd}")
        return jsonify(success=True)

    except Exception as e:
        log_message(f"Error sending {cmd}: {e}")
        print(f"⚠️ Error sending {cmd}: {e}")
        return jsonify(success=False, error=str(e)), 500

@app.route("/status", methods=["GET"])
def get_status():
    """Return real BLE connection status."""
    connected = ble_controller.is_connected()
    print(f"[STATUS] BLE connected = {connected}")
    return jsonify({"connected": connected})

@app.route("/terminal/logs")
def get_logs():
    if not arduino_connected:
        return render_template("connect.html")
    with lock:
        return jsonify(serial_log)

@app.route("/reboot", methods=["POST"])
def reboot():
    log_message("System is rebooting…")
    subprocess.Popen(["sudo", "reboot"])
    return jsonify(success=True, message="Rebooting Raspberry Pi…")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)