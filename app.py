from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory
import serial, threading, time, subprocess
import os  # <-- Make sure this import is here
import asyncio
import ble_controller # Import the BLE handler

app = Flask(__name__)
# Add a simple secret key for session management, needed if using Flask sessions later
app.secret_key = 'your_super_secret_key' 

# === Configuration Variable (REMOVED: Fixed delay is replaced by handshaking) ===
# Old DRIP_DELAY_SECONDS is removed.
# ==============================

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
    # IMPORTANT: Increasing baud rate can help, but 9600 is often safe.
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

# Background reader thread (MODIFIED to support handshaking)
def read_from_serial():
    """Reads lines from the serial port and logs them."""
    global current_gcode_runner
    while arduino_connected:
        try:
            # Check for data available before reading to prevent blocking
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(line)
                    
                    # *** CRITICAL FIX: Reverting to a strict check. ***
                    # The loose check was being triggered by "Queued...", "Ignored...", etc.
                    # We will check for the exact string "Done", case-insensitive.
                    if current_gcode_runner and line.strip().upper() == "DONE":
                        current_gcode_runner.handshake_event.set()
                        
            else:
                # Sleep briefly to reduce CPU usage
                time.sleep(0.01) 
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            time.sleep(1) # Wait before retrying after an error

# Global reference to the current G-code thread runner instance
current_gcode_runner = None

# Class to encapsulate the G-code running process and its state
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block):
        super().__init__(daemon=True)
        self.gcode_block = gcode_block
        self.handshake_event = threading.Event()
        self.lines = [
            line.split(';')[0].strip() for line in gcode_block.split('\n') 
            if line.split(';')[0].strip().upper().startswith('G1')
        ]
        self.line_index = 0
        self.is_running = True

    def send_line(self, line):
        """Sends a single G-code line to the Arduino."""
        try:
            with lock:
                arduino.write((line + "\n").encode())
            log_message(f"Sent line {self.line_index + 1}: {line}")
            self.line_index += 1
            return True
        except Exception as e:
            log_message(f"SERIAL ERROR while sending line: {e}")
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner
        current_gcode_runner = self
        num_commands = len(self.lines)
        # *** CHANGED: Increased priming count to 8 (queue size is 10) ***
        PRIME_COUNT = 18 
        log_message(f"Received G-code block with {num_commands} commands. Starting {PRIME_COUNT}-command priming...")

        # 1. INITIAL: Send the first four commands immediately, with a small delay between each.
        for _ in range(PRIME_COUNT):
            if self.is_running and self.line_index < num_commands:
                self.send_line(self.lines[self.line_index])
                # Small delay to allow the Arduino queueing process to keep up
                time.sleep(0.05) 
            else:
                break 
        
        # *** NEW: Critical pause after priming to allow the Arduino to start executing the first command and send "Done" ***
        if self.is_running and self.line_index > 0:
            time.sleep(0.1) # Wait 100ms before entering handshake loop

        # 2. DRIP: Wait for 'Done' signal before sending the next command
        while self.is_running and self.line_index < num_commands:
            
            # Wait up to 10 seconds for the 'Done' signal
            if self.handshake_event.wait(timeout=3600):
                self.handshake_event.clear() # Clear the signal for the next command
                
                # Send the next command
                if not self.send_line(self.lines[self.line_index]):
                    break # Stop if sending failed
            else:
                # Timeout occurred, usually meaning the Arduino stopped responding
                log_message("TIMEOUT: Arduino failed to respond with 'Done'. Aborting G-code block.")
                self.is_running = False

        # 3. CLEANUP
        current_gcode_runner = None
        log_message("Finished sending G-code block.")


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

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    """
    Disconnects Serial, Compiles & Uploads Arduino Code, Reconnects Serial.
    """
    global arduino, arduino_connected
    
    log_message("FIRMWARE UPDATE STARTED...")
    
    # 1. Disconnect Serial to free the port
    if arduino and arduino.is_open:
        arduino.close()
    arduino_connected = False
    log_message("Serial port disconnected for upload.")

    try:
        # 2. Run Compile Command
        log_message("Compiling firmware...")
        # Note: using --clean to ensure a fresh build
        compile_cmd = [
            "/usr/local/bin/arduino-cli", "compile", # Path might need adjustment if not in /usr/local/bin
            "--fqbn", "arduino:avr:uno",
            "/home/pacpi/Desktop/stepper_gcode_project/Sand"
        ]
        # Check if arduino-cli is just in 'bin' or global path.
        # If 'arduino-cli' works in terminal, just use "arduino-cli" below:
        subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)

        # 3. Run Upload Command
        log_message("Uploading firmware...")
        subprocess.run(["arduino-cli", "upload", "-p", "/dev/ttyACM0", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)
        
        log_message("Firmware updated successfully!")
        success_msg = "Firmware updated."

    except subprocess.CalledProcessError as e:
        log_message(f"UPDATE FAILED: {e.stderr}")
        success_msg = f"Update Failed: {e.stderr[:50]}..."
        # Don't return yet, we need to reconnect!
    except Exception as e:
        log_message(f"UPDATE ERROR: {str(e)}")
        success_msg = f"Error: {str(e)}"

    # 4. Reconnect Serial
    try:
        log_message("Reconnecting serial...")
        time.sleep(2) # Wait for Arduino to reboot
        arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1)
        arduino_connected = True
        # Restart reader thread
        threading.Thread(target=read_from_serial, daemon=True).start()
        log_message("Serial reconnected.")
    except Exception as e:
        log_message(f"FAILED TO RECONNECT SERIAL: {e}")
    
    return jsonify(success=True, message=success_msg)
# ---------------- APP ROUTES ----------------
    
@app.route("/")
def index():
    # UPDATED: This is now the default page
    return render_template("designs.html")

# NEW: This is the new route for your old controls page
@app.route("/controls")
def controls():
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

@app.route("/script")
def script():
    return render_template("script.html")

@app.route("/designs")
def designs():
    # This route just points to the new default page
    return render_template("designs.html")

# --- ROUTE TO SERVE THE .txt AND .png FILES ---
@app.route('/designs/<path:filename>')
def serve_design_file(filename):
    """Serves files (txt, png) from the 'templates/designs' directory."""
    # This path now correctly points inside the 'templates' folder
    designs_dir = os.path.join(app.root_path, 'templates', 'designs')
    
    # Log the request
    log_message(f"Serving file from /templates/designs/: {filename}")
    
    # Securely send the file from that directory
    return send_from_directory(designs_dir, filename)

# --- NEW: API ROUTE TO LIST ALL DESIGN FILES ---
@app.route('/api/designs')
def list_designs():
    """
    Lists all .txt files in the templates/designs directory
    so the webpage can fetch them.
    """
    designs_dir = os.path.join(app.root_path, 'templates', 'designs')
    try:
        # Find all files ending in .txt
        files = [f for f in os.listdir(designs_dir) if f.endswith('.txt')]
        # Log it and return the list as JSON
        log_message(f"Found {len(files)} designs in /templates/designs/.")
        return jsonify(files)
    except Exception as e:
        log_message(f"ERROR: Could not list designs in /templates/designs/: {e}")
        return jsonify(error=str(e)), 500
# --- END OF NEW ROUTE ---


# --- G-CODE BLOCK FUNCTIONALITY ---

# process_and_send_gcode is replaced by the GCodeRunner Class and its run method
def process_and_send_gcode(gcode_block):
    """
    Starts the new GCodeRunner thread for handshaking.
    """
    if not arduino_connected:
        log_message("ERROR: Cannot run G-code. Arduino not connected.")
        return
    
    # Check if a job is already running
    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive():
        log_message("ERROR: Another G-code job is already running.")
        return

    runner = GCodeRunner(gcode_block)
    runner.start()


@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    """
    API endpoint for AUTO-DRIP mode, now using handshaking.
    """
    data = request.json
    gcode_block = data.get("gcode")

    if not gcode_block:
        return jsonify(success=False, error="No G-code received."), 400
    
    if not arduino_connected:
         return jsonify(success=False, error="Arduino not connected."), 500

    # Check if a job is already running (prevent multiple simultaneous runs)
    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive():
        return jsonify(success=False, error="Another design is currently running. Wait for it to finish."), 503

    # Start the handshaking runner
    process_and_send_gcode(gcode_block)

    # Return an immediate success message to the browser
    return jsonify(success=True, message="G-code block received and is being sent (handshaking).")

# --- NEW: ENDPOINT FOR MANUAL STEP-BY-STEP ---
@app.route("/send_single_gcode_line", methods=["POST"])
def send_single_gcode_line_route():
    """
    API endpoint for MANUAL mode.
    Sends one G-code line and returns.
    """
    data = request.json
    gcode_line = data.get("gcode_line")

    if not gcode_line:
        return jsonify(success=False, error="No G-code line received."), 400
    
    if not arduino_connected:
        return jsonify(success=False, error="Arduino not connected."), 500

    # Validate it's a G1 command
    clean_line = gcode_line.strip()
    if not clean_line.upper().startswith('G1'):
         return jsonify(success=False, error="Invalid line. Only G1 commands allowed."), 400

    try:
        with lock: # Use the lock for thread-safe writing
            arduino.write((clean_line + "\n").encode())
        log_message(f"Sent (Manual): {clean_line}")
        return jsonify(success=True, message=f"Sent: {clean_line}")
    except Exception as e:
        log_message(f"SERIAL ERROR (Manual) while sending line: {e}")
        return jsonify(success=False, error=str(e)), 500
# --- END OF NEW ENDPOINT ---


# In app.py

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    
    if not cmd:
        return jsonify(success=False, error="No command")

    # ... (Your existing serial logic) ...

    # BLE Handling
    if cmd.startswith("LED:"):
        # Parse RGB
        try:
            parts = cmd.split(":")[1].split(",")
            r, g, b, br = map(int, parts)
            
            # Fire and Forget: Schedule the task, but don't make Flask wait for the physical Bluetooth ack
            asyncio.run_coroutine_threadsafe(
                ble_controller.send_led_command(r, g, b, br),
                ble_controller.loop
            )
            return jsonify(success=True, message="Color update queued")
            
        except Exception as e:
            return jsonify(success=False, error=str(e))

    # Power/Connect Handling
    if cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
        asyncio.run_coroutine_threadsafe(
            ble_controller.handle_command(cmd),
            ble_controller.loop
        )
        return jsonify(success=True, message=f"{cmd} queued")

    # ... (Rest of function)

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
