from flask import Flask, render_template, request, jsonify, Response, url_for
import serial, threading, time, subprocess
import asyncio
import ble_controller # Import the BLE handler

app = Flask(__name__)
# Add a simple secret key for session management, needed if using Flask sessions later
app.secret_key = 'your_super_secret_key' 

# === NGROK HEADER FIX ===
@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === SERIAL SETUP ===
arduino = None
arduino_connected = False

try:
    # Use a shorter timeout to avoid blocking if the port is busy
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1) 
    time.sleep(2)
    arduino_connected = True
except Exception as e:
    # Print warning if Arduino is not found
    print(f"WARNING: Arduino not connected: {e}") 

serial_log = []
lock = threading.Lock()

def log_message(msg):
    """Adds a message to the serial log thread-safely."""
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S] ")
    with lock:
        serial_log.append(timestamp + msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# Background reader thread
def read_from_serial():
    """Reads lines from the serial port and logs them."""
    while arduino_connected:
        try:
            # Check for data available before reading to prevent blocking
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(line)
            else:
                # Sleep briefly to reduce CPU usage
                time.sleep(0.01) 
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            time.sleep(1) # Wait before retrying after an error

if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()
# === End of Serial setup code ===


# ---------------- UTILITY ROUTES ----------------

@app.route("/check_password", methods=["POST"])
def check_password_route():
    """Simple server-side password check (optional but better practice)."""
    # NOTE: Since the front-end handles persistence via localStorage, this 
    # check is mostly for verification during the login attempt.
    data = request.json
    password = data.get("password")
    
    # Simple hardcoded password for example.
    # Replace "2025" with a secure environment variable or configuration method in production!
    if password == "2025":
        return jsonify(success=True)
    return jsonify(success=False), 401

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Executes 'sudo shutdown now' on the host system (Raspberry Pi)."""
    log_message("System received SHUTDOWN command...")
    try:
        # IMPORTANT: This assumes the user running the Flask app has 'sudo' rights for shutdown without a password.
        subprocess.Popen(["sudo", "shutdown", "now"])
        return jsonify(success=True, message="Shutting down Raspberry Pi in 1 minute...")
    except Exception as e:
        log_message(f"Shutdown error: {e}")
        return jsonify(success=False, message=f"Shutdown command failed: {e}"), 500

@app.route("/pull", methods=["POST"])
def git_pull():
    """Executes 'git pull' in the current working directory."""
    log_message("Running 'git pull'...")
    try:
        # Assuming the app is run from the git repository root (cwd=".")
        # check=True raises an exception for non-zero exit codes (errors)
        result = subprocess.run(
            ["git", "pull"], 
            capture_output=True, 
            text=True, 
            cwd=".", 
            check=True,
            timeout=15 # Add a timeout for safety
        )
        
        output = result.stdout.strip() if result.stdout else "No output."
        error = result.stderr.strip() if result.stderr else ""
        
        if error and not output:
             # Handle case where git pull might output to stderr but still succeed (e.g. warnings)
             log_message(f"Git Pull finished with potential warnings/errors: {error}")
             return jsonify(success=True, message=f"Git pull finished with warnings: {error[:100]}...") # Limit error message length
        
        log_message(f"Git Pull successful:\n{output}")
        return jsonify(success=True, message=f"Git pull successful. Output: {output[:100]}...")
        
    except subprocess.CalledProcessError as e:
        log_message(f"Git Pull FAILED: {e.stderr.strip()}")
        return jsonify(success=False, message=f"Git pull FAILED: {e.stderr.strip()[:100]}..."), 500
    except Exception as e:
        log_message(f"Execution Error during git pull: {e}")
        return jsonify(success=False, message=f"Execution error: {e}"), 500

@app.route("/reboot", methods=["POST"])
def reboot():
    """Executes 'sudo reboot' on the host system."""
    log_message("System received REBOOT command...")
    try:
        subprocess.Popen(["sudo", "reboot"])
        return jsonify(success=True, message="Rebooting Raspberry Pi...")
    except Exception as e:
        log_message(f"Reboot error: {e}")
        return jsonify(success=False, message=f"Reboot command failed: {e}"), 500
    
# ---------------- APP ROUTES ----------------
@app.route("/")
def index():
    if not arduino_connected:
        return render_template("connect.html")
    return render_template("index.html")

@app.route("/terminal")
def terminal():
    # Only check for Arduino connectivity if you expect the terminal to only work when connected.
    # Since this is a system terminal now, we can skip the check or include a status message.
    # For now, keeping the original logic:
    if not arduino_connected:
        return render_template("connect.html")
    return render_template("terminal.html")

@app.route("/led_controls")
def led_controls():
    return render_template("led_controls.html")

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    
    if cmd:
        try:
            # Check for BLE/Serial command routing
            if cmd.startswith("LED:") or cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
                # --- BLE COMMANDS (Async handling remains the same) ---
                if cmd.startswith("LED:"):
                    parts = cmd.split(":")[1].split(",")
                    r, g, b, br = map(int, parts)
                    future = asyncio.run_coroutine_threadsafe(
                        ble_controller.send_led_command(r, g, b, br),
                        ble_controller.loop
                    )
                    future.result(timeout=5)
                else: # CONNECT/DISCONNECT/POWER commands
                    future = asyncio.run_coroutine_threadsafe(
                        ble_controller.handle_command(cmd),
                        ble_controller.loop
                    )
                    future.result(timeout=5)
            elif arduino_connected:
                # --- SERIAL COMMANDS ---
                arduino.write((cmd + "\n").encode())
            else:
                # Command received but neither BLE nor Arduino is connected to handle it.
                log_message(f"Warning: Command '{cmd}' received but no device connected.")
                return jsonify(success=False, error="No device connected to send command to."), 500

            log_message(f"Sent: {cmd}")
            return jsonify(success=True)

        except Exception as e:
            log_message(f"Error sending {cmd}: {e}")
            return jsonify(success=False, error=str(e)), 500

    return jsonify(success=False), 400

@app.route("/status", methods=["GET"])
def get_status():
    """Return real BLE connection status."""
    return jsonify({"connected": ble_controller.is_connected()})

@app.route("/terminal/logs")
def get_logs():
    if not arduino_connected:
        return jsonify(["WARNING: Arduino not connected. Showing system messages only."])
    with lock:
        # Return a copy of the log to prevent modification while reading
        return jsonify(list(serial_log))

# ---------------- MAIN ----------------
if __name__ == "__main__":
    # The BLE loop starts automatically on import of ble_controller
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False) 
