from flask import Flask, render_template, request, jsonify, Response, url_for
import serial, threading, time, subprocess
import os  # <-- NEWLY ADDED IMPORT
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

# --- NEW DESIGNS FUNCTIONALITY (ADDED OCTOBER 2025) ---

def send_gcode_lines_to_arduino(filepath):
    """
    (RUNS IN A THREAD)
    Sends G-code lines from a file to the Arduino, one by one.
    This function is designed to be run in a background thread
    to avoid locking up the web server.
    """
    if not arduino_connected:
        log_message(f"ERROR: Cannot run design. Arduino not connected.")
        return False, "Arduino not connected."

    try:
        log_message(f"Starting G-code file: {os.path.basename(filepath)}")
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f):
                clean_line = line.strip()
                # Ignore empty lines and G-code comments (often ';')
                if clean_line and not clean_line.startswith(';'): 
                    log_message(f"Sending line {line_num + 1}: {clean_line}")
                    
                    # Write command to Arduino
                    with lock: # Ensure write is thread-safe with serial logger
                        arduino.write((clean_line + "\n").encode())
                    
                    # IMPORTANT: Wait for Arduino to process the command.
                    # 0.05s is a starting guess. You may need to TUNE this
                    # delay based on how
                    # fast your Arduino processes each G1 command.
                    # For a more robust solution, implement an "ok"
                    # handshake from the Arduino.
                    time.sleep(0.05) 

        log_message(f"Finished sending G-code file: {os.path.basename(filepath)}")
        return True, "G-code file sent successfully."
    except FileNotFoundError:
        log_message(f"ERROR: G-code file not found at {filepath}")
        return False, "G-code file not found."
    except Exception as e:
        log_message(f"Error sending G-code from file {filepath}: {e}")
        return False, f"Error sending G-code: {e}"


@app.route("/designs")
def designs():
    """Renders the designs page by scanning folders."""
    
    # Path to G-code files (e.g., 'gcode/spiral/command.txt')
    gcode_base_path = os.path.join(app.root_path, 'gcode')
    # Path to image files (e.g., 'static/gcode/spiral/spiral.png')
    static_gcode_path = os.path.join(app.root_path, 'static', 'gcode')
    
    designs_data = []

    if not os.path.exists(gcode_base_path) or not os.path.isdir(gcode_base_path):
        log_message(f"WARNING: 'gcode' directory not found at {gcode_base_path}")
        return render_template("designs.html", designs=designs_data)
    
    if not os.path.exists(static_gcode_path):
            log_message(f"WARNING: 'static/gcode' directory for images not found.")
            # We can proceed, but will use default images

    try:
        # Loop through each folder in the 'gcode' directory
        for design_folder in sorted(os.listdir(gcode_base_path)):
            
            # Path for .txt files
            gcode_design_path = os.path.join(gcode_base_path, design_folder) 
            
            if os.path.isdir(gcode_design_path):
                image_file = None
                description_text = "No description available."
                
                # --- 1. Find Image ---
                # Images must be in 'static/gcode/<design_folder>/<design_folder>.[ext]'
                for ext in ['.png', '.jpg', '.jpeg', '.gif']:
                    img_name = f"{design_folder}{ext}"
                    # Path relative to 'static' folder (for url_for)
                    static_rel_path = os.path.join('gcode', design_folder, img_name)
                    # Full OS path to check if file actually exists
                    full_static_path = os.path.join(app.root_path, 'static', static_rel_path)

                    if os.path.exists(full_static_path):
                        image_file = url_for('static', filename=static_rel_path)
                        break
                
                if not image_file:
                    # Fallback to a default image
                    image_file = url_for('static', filename='default_design.png')

                # --- 2. Read Description ---
                description_file = os.path.join(gcode_design_path, 'description.txt')
                if os.path.exists(description_file):
                    try:
                        with open(description_file, 'r') as f:
                            description_text = f.readline().strip()
                    except Exception as e:
                        log_message(f"Error reading description for {design_folder}: {e}")
                        description_text = "Error reading description."

                # --- 3. Check for Command File ---
                command_file = os.path.join(gcode_design_path, 'command.txt')
                command_available = os.path.exists(command_file)

                designs_data.append({
                    'name': design_folder,
                    'image': image_file,
                    'description': description_text,
                    'command_available': command_available
                })
    except Exception as e:
        log_message(f"Error scanning 'gcode' directory: {e}")

    return render_template("designs.html", designs=designs_data)


@app.route("/run_design/<design_name>", methods=["POST"])
def run_design(design_name):
    """
    API endpoint to run a specific design.
    Starts the G-code sending in a background thread.
    """
    # Basic security check to prevent path traversal (e.g., '../')
    if '..' in design_name or '/' in design_name or '\\' in design_name:
        log_message(f"WARNING: Invalid design name attempted: {design_name}")
        return jsonify(success=False, error="Invalid design name."), 400
    
    gcode_filepath = os.path.join(app.root_path, 'gcode', design_name, 'command.txt')
    
    if not os.path.exists(gcode_filepath):
        log_message(f"ERROR: Cannot run design. File not found: {gcode_filepath}")
        return jsonify(success=False, error="Command file not found."), 404

    # --- CRITICAL ---
    # Run the G-code sender in a background thread.
    # This immediately frees up the web request, so the browser
    # doesn't time out while waiting for a long G-code file to send.
    def send_gcode_task():
        send_gcode_lines_to_arduino(gcode_filepath)

    # Start the background task
    # 'daemon=True' means the thread will automatically exit when the main app exits
    threading.Thread(target=send_gcode_task, daemon=True).start()

    # Return an *immediate* success response to the frontend
    log_message(f"Background task started for design: {design_name}")
    return jsonify(success=True, message=f"Started sending design '{design_name}' to Arduino.")


# --- END OF NEW DESIGNS FUNCTIONALITY ---


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
