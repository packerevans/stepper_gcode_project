from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory
import serial, threading, time, subprocess
import os
import re
import asyncio
import ble_controller # Import the BLE handler

app = Flask(__name__)
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
    global current_gcode_runner
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(line)
                    # Handshake check
                    if current_gcode_runner and line.strip().upper() == "DONE":
                        current_gcode_runner.handshake_event.set()       
            else:
                time.sleep(0.01) 
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            time.sleep(1)

# Global reference to the current G-code thread runner instance
current_gcode_runner = None

# Class to encapsulate the G-code running process
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
        
        # Buffer priming
        PRIME_COUNT = 8 
        log_message(f"Received G-code block with {num_commands} commands. Starting {PRIME_COUNT}-command priming...")

        # 1. INITIAL
        for _ in range(PRIME_COUNT):
            if self.is_running and self.line_index < num_commands:
                self.send_line(self.lines[self.line_index])
                time.sleep(0.05) 
            else:
                break 
        
        if self.is_running and self.line_index > 0:
            time.sleep(0.1)

        # 2. DRIP FEED
        while self.is_running and self.line_index < num_commands:
            if self.handshake_event.wait(timeout=None): # Wait forever for response (allows pausing)
                self.handshake_event.clear()
                if not self.send_line(self.lines[self.line_index]):
                    break 
            else:
                log_message("TIMEOUT: Arduino failed to respond. Aborting.")
                self.is_running = False

        # 3. CLEANUP
        current_gcode_runner = None
        log_message("Finished sending G-code block.")


if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()


# ---------------- UTILITY ROUTES ----------------

@app.route("/check_password", methods=["POST"])
def check_password_route():
    data = request.json
    password = data.get("password")
    if password == "2025":
        return jsonify(success=True)
    return jsonify(success=False), 401

@app.route("/shutdown", methods=["POST"])
def shutdown():
    log_message("System received SHUTDOWN command...")
    try:
        subprocess.Popen(["sudo", "shutdown", "now"])
        return jsonify(success=True, message="Shutting down Raspberry Pi...")
    except Exception as e:
        return jsonify(success=False, message=f"Shutdown command failed: {e}"), 500

@app.route("/pull", methods=["POST"])
def git_pull():
    log_message("Running 'git pull'...")
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=".", check=True, timeout=15)
        output = result.stdout.strip() if result.stdout else "No output."
        log_message(f"Git Pull successful:\n{output}")
        return jsonify(success=True, message=f"Git pull successful. Output: {output[:100]}...")
    except Exception as e:
        log_message(f"Git Pull FAILED: {e}")
        return jsonify(success=False, message=f"Git pull FAILED: {e}"), 500

@app.route("/reboot", methods=["POST"])
def reboot():
    log_message("System received REBOOT command...")
    try:
        subprocess.Popen(["sudo", "reboot"])
        return jsonify(success=True, message="Rebooting Raspberry Pi...")
    except Exception as e:
        return jsonify(success=False, message=f"Reboot command failed: {e}"), 500

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    global arduino, arduino_connected
    log_message("FIRMWARE UPDATE STARTED...")
    
    if arduino and arduino.is_open:
        arduino.close()
    arduino_connected = False
    log_message("Serial port disconnected for upload.")

    try:
        log_message("Compiling firmware...")
        subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)

        log_message("Uploading firmware...")
        subprocess.run(["arduino-cli", "upload", "-p", "/dev/ttyACM0", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)
        
        log_message("Firmware updated successfully!")
        success_msg = "Firmware updated."
    except Exception as e:
        log_message(f"UPDATE ERROR: {str(e)}")
        success_msg = f"Error: {str(e)}"

    try:
        log_message("Reconnecting serial...")
        time.sleep(2) 
        arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1)
        arduino_connected = True
        threading.Thread(target=read_from_serial, daemon=True).start()
        log_message("Serial reconnected.")
    except Exception as e:
        log_message(f"FAILED TO RECONNECT SERIAL: {e}")
    
    return jsonify(success=True, message=success_msg)

# ---------------- APP ROUTES ----------------
    
@app.route("/")
def index():
    return render_template("designs.html")

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
    return render_template("designs.html")

@app.route('/designs/<path:filename>')
def serve_design_file(filename):
    designs_dir = os.path.join(app.root_path, 'templates', 'designs')
    return send_from_directory(designs_dir, filename)

@app.route('/api/designs')
def list_designs():
    designs_dir = os.path.join(app.root_path, 'templates', 'designs')
    try:
        files = [f for f in os.listdir(designs_dir) if f.endswith('.txt')]
        return jsonify(files)
    except Exception as e:
        return jsonify(error=str(e)), 500


# ---------------- COMMAND HANDLING ----------------

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode_block = data.get("gcode")
    speed_override = data.get("speed_override")

    if not gcode_block:
        return jsonify(success=False, error="No G-code received."), 400
    
    if not arduino_connected:
         return jsonify(success=False, error="Arduino not connected."), 500

    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive():
        return jsonify(success=False, error="Another design is currently running."), 503

    # --- SPEED OVERRIDE LOGIC ---
    if speed_override:
        new_block = []
        for line in gcode_block.split('\n'):
            line = line.strip()
            if line.startswith('G1'):
                parts = line.split()
                if len(parts) >= 3:
                    # G1 <steps1> <steps2> <NEW_SPEED>
                    new_line = f"{parts[0]} {parts[1]} {parts[2]} {speed_override}"
                    new_block.append(new_line)
                else:
                    new_block.append(line)
            else:
                new_block.append(line)
        gcode_block = "\n".join(new_block)
        log_message(f"Speed override applied: {speed_override}µs")

    process_and_send_gcode(gcode_block)
    return jsonify(success=True, message="G-code block received and started.")

def process_and_send_gcode(gcode_block):
    if not arduino_connected:
        log_message("ERROR: Cannot run G-code. Arduino not connected.")
        return
    runner = GCodeRunner(gcode_block)
    runner.start()

@app.route("/send_single_gcode_line", methods=["POST"])
def send_single_gcode_line_route():
    data = request.json
    gcode_line = data.get("gcode_line")

    if not gcode_line: return jsonify(success=False, error="No G-code line received."), 400
    if not arduino_connected: return jsonify(success=False, error="Arduino not connected."), 500

    clean_line = gcode_line.strip()
    if not clean_line.upper().startswith('G1'):
         return jsonify(success=False, error="Invalid line. Only G1 commands allowed."), 400

    try:
        with lock:
            arduino.write((clean_line + "\n").encode())
        log_message(f"Sent (Manual): {clean_line}")
        return jsonify(success=True, message=f"Sent: {clean_line}")
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route("/send", methods=["POST"])
def send_command():
    """Handles commands for both Arduino (Serial) and LED Strip (BLE)."""
    data = request.json
    cmd = data.get("command")
    if not cmd: return jsonify(success=False, error="No command received")

    try:
        # 1. BLE / LED COMMANDS
        if cmd.startswith("LED:"):
            try:
                parts = cmd.split(":")[1].split(",")
                r, g, b, br = map(int, parts)
                asyncio.run_coroutine_threadsafe(
                    ble_controller.send_led_command(r, g, b, br),
                    ble_controller.loop
                )
                return jsonify(success=True, message="Color update queued")
            except Exception as e:
                return jsonify(success=False, error=f"LED Parse Error: {str(e)}")

        if cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
            asyncio.run_coroutine_threadsafe(
                ble_controller.handle_command(cmd),
                ble_controller.loop
            )
            return jsonify(success=True, message=f"{cmd} queued")

        # 2. ARDUINO COMMANDS
        if arduino_connected:
            with lock:
                arduino.write((cmd + "\n").encode())
            log_message(f"Sent: {cmd}")
            return jsonify(success=True)
        else:
            log_message(f"Warning: Command '{cmd}' received but no device connected.")
            return jsonify(success=False, error="Arduino not connected and not a BLE command."), 500

    except Exception as e:
        log_message(f"Error in send_command: {e}")
        return jsonify(success=False, error=str(e)), 500

@app.route("/status", methods=["GET"])
def get_status():
    return jsonify({"connected": ble_controller.is_connected()})

@app.route("/terminal/logs")
def get_logs():
    if not arduino_connected:
        return jsonify(["WARNING: Arduino not connected. Showing system messages only."])
    with lock:
        return jsonify(list(serial_log))

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
