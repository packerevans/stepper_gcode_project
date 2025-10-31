from flask import Flask, render_template, request, jsonify, Response, url_for
import serial, threading, time, subprocess
import os  # <-- Make sure this import is here
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
    data = request.json
    password = data.get("password")
    
    if password == "2025":
        return jsonify(success=True)
    return jsonify(success=False), 401

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Executes 'sudo shutdown now' on the host system (Raspberry Pi)."""
    log_message("System received SHUTDOWN command...")
    try:
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
        result = subprocess.run(
            ["git", "pull"], 
            capture_output=True, 
            text=True, 
            cwd=".", 
            check=True,
            timeout=15
        )
        
        output = result.stdout.strip() if result.stdout else "No output."
        error = result.stderr.strip() if result.stderr else ""
        
        if error and not output:
             log_message(f"Git Pull finished with potential warnings/errors: {error}")
             return jsonify(success=True, message=f"Git pull finished with warnings: {error[:100]}...")
        
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
# ---------------- APP ROUTES ----------------
@app.route("/script")
def script():
    if not arduino_connected:
        return render_template("connect.html")
    return render_template("script.html") # <-- This is the only line that needs to change
    
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

# --- NEW G-CODE BLOCK FUNCTIONALITY ---

def process_and_send_gcode(gcode_block):
    """
    (RUNS IN A THREAD)
    Parses a block of text and sends valid G1 commands to Arduino
    one by one, with a delay.
    """
    if not arduino_connected:
        log_message("ERROR: Cannot run G-code. Arduino not connected.")
        return
    
    lines = gcode_block.split('\n')
    log_message(f"Received G-code block with {len(lines)} lines. Starting to send...")
    
    for line_num, line in enumerate(lines):
        # Remove whitespace and comments
        clean_line = line.split(';')[0].strip() 
        
        # Process only non-empty G1 lines (case-insensitive)
        if clean_line and clean_line.upper().startswith('G1'):
            log_message(f"Sending line {line_num + 1}: {clean_line}")
            try:
                # Use the existing lock to prevent serial write conflicts
                with lock: 
                    arduino.write((clean_line + "\n").encode())
                
                # --- This is the "slow feed" ---
                # Wait for the Arduino to process.
                # You may need to TUNE this 0.05s value.
                # Increase it if Arduino misses commands.
                time.sleep(0.05) 
            except Exception as e:
                log_message(f"SERIAL ERROR while sending line {line_num + 1}: {e}")
                log_message("Aborting G-code block.")
                break # Stop sending if there's an error
        elif clean_line:
            # Log lines that are skipped (e.g., G28, M105, or empty)
            log_message(f"Skipping line {line_num + 1}: {clean_line}")
    
    log_message("Finished sending G-code block.")

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    """
    API endpoint to receive a block of G-code text from the browser.
    Starts the sending process in a background thread.
    """
    data = request.json
    gcode_block = data.get("gcode")

    if not gcode_block:
        return jsonify(success=False, error="No G-code received."), 400
    
    if not arduino_connected:
         return jsonify(success=False, error="Arduino not connected."), 500

    # Run the sending process in a background thread
    # This makes the web page responsive immediately.
    threading.Thread(target=process_and_send_gcode, args=(gcode_block,), daemon=True).start()

    # Return an immediate success message to the browser
    return jsonify(success=True, message="G-code block received and is being sent to the Arduino.")

# --- END OF NEW G-CODE BLOCK FUNCTIONALITY ---


@app.route("/send", methods=["POST"])
def send_command():
    """
    This route is still used for BLE commands and
    any other single-line commands you might add later.
    """
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
                # --- SERIAL COMMANDS (Single) ---
                arduino.write((cmd + "\n").encode())
            else:
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
