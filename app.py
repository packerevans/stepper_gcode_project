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
# ... (Serial setup code remains unchanged) ...
arduino = None
arduino_connected = False

try:
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
    time.sleep(2)
    arduino_connected = True
except Exception as e:
    print(f"Arduino not connected: {e}")

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
# ... (End of Serial setup code) ...


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
    # If this route is meant to be callable even without Arduino, keep this line commented or modify logic
    # if not arduino_connected:
    #     return render_template("connect.html") 
    
    data = request.json
    cmd = data.get("command")
    
    if cmd:
        try:
            if cmd.startswith("LED:"):
                # Send RGB LED values over BLE: Expects R, G, B, Brightness
                parts = cmd.split(":")[1].split(",")
                r, g, b, br = map(int, parts)
                
                # SCHEDULE the async BLE call to the background thread's event loop
                future = asyncio.run_coroutine_threadsafe(
                    ble_controller.send_led_command(r, g, b, br),
                    ble_controller.loop
                )
                future.result(timeout=5) # Wait for the result/exception (timeout added for safety)

            elif cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
                # Handle BLE power/connect/disconnect commands
                future = asyncio.run_coroutine_threadsafe(
                    ble_controller.handle_command(cmd),
                    ble_controller.loop
                )
                future.result(timeout=5)

            elif arduino_connected:
                # Forward anything else to Arduino over serial
                arduino.write((cmd + "\n").encode())

            log_message(f"Sent: {cmd}")
            return jsonify(success=True)

        except Exception as e:
            log_message(f"Error sending {cmd}: {e}")
            return jsonify(success=False, error=str(e)), 500

    return jsonify(success=False), 400

@app.route("/status", methods=["GET"])
def get_status():
    """Return real BLE connection status."""
    # Run the synchronous check directly, as it only reads a global variable
    return jsonify({"connected": ble_controller.is_connected()})

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

@app.route("/shutdown", methods=["POST"])
def shutdown():
    log_message("System is shutting down…")
    subprocess.Popen(["sudo", "shutdown", "now"])
    return jsonify(success=True, message="Shutting down…")

@app.route("/pull", methods=["POST"])
def pull():
    try:
        result = subprocess.check_output(["git", "pull"], stderr=subprocess.STDOUT).decode()
        log_message("Git pull:\n" + result)
        return jsonify(success=True, message="Pulled successfully", output=result)
    except subprocess.CalledProcessError as e:
        log_message("Git pull error:\n" + e.output.decode())
        return jsonify(success=False, error=e.output.decode()), 500


# ---------------- MAIN ----------------
if __name__ == "__main__":
    # The BLE loop starts automatically on import of ble_controller
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False) # use_reloader=False is important with threading
