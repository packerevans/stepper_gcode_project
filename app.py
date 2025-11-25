from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess
import os
import re
import asyncio
import socket
# Assuming these modules exist in your project folder
import wifi_tools 
import ble_controller 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === SERIAL LOGGING ===
serial_log = []
lock = threading.Lock()

def log_message(msg):
    """Adds a message to the serial log thread-safely."""
    timestamp = time.strftime("[%H:%M:%S] ")
    with lock:
        serial_log.append(timestamp + msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# Connect BLE logs to Flask logs
ble_controller.set_logger(log_message)

# === SERIAL CONNECTION SETUP ===
arduino = None
arduino_connected = False
current_gcode_runner = None

try:
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1) 
    time.sleep(2) # Wait for Arduino reset
    arduino_connected = True
except Exception as e:
    print(f"WARNING: Arduino not connected: {e}") 
    log_message(f"Arduino Init Failed: {e}")

# === SMART G-CODE RUNNER (FLOW CONTROL) ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block):
        super().__init__(daemon=True)
        # Filter and store only G1 commands
        self.lines = [
            line.split(';')[0].strip() for line in gcode_block.split('\n') 
            if line.split(';')[0].strip().upper().startswith('G1')
        ]
        self.total_lines = len(self.lines)
        self.is_running = True
        
        # FLOW CONTROL CONFIGURATION
        # Arduino has a buffer of 50. We fill to 40 to be safe.
        self.ARDUINO_BUFFER_SIZE = 40  
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        
        # Thread Event: Used to pause sending when credits are 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
        """Called by the serial reader thread when Arduino speaks."""
        line = line.strip().upper()
        # "Done" means Arduino finished a move and has space for 1 more
        if line == "DONE":
            with lock:
                self.credits += 1
                self.slot_available_event.set() # Wake up the sender!

    def send_line(self, line):
        try:
            with lock:
                arduino.write((line + "\n").encode())
            
            # Optional: Log progress (can be noisy for large designs)
            # log_message(f"Sent ({self.lines_sent+1}/{self.total_lines})")
            
            self.lines_sent += 1
            self.credits -= 1 # Used one buffer slot
            return True
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner
        current_gcode_runner = self
        
        log_message(f"Job Started: {self.total_lines} lines.")

        while self.is_running and self.lines_sent < self.total_lines:
            
            # 1. CHECK CREDITS (Is Arduino full?)
            if self.credits <= 0:
                self.slot_available_event.clear()
                # Wait for Arduino to say "Done" (timeout 10s to prevent hanging)
                got_slot = self.slot_available_event.wait(timeout=10.0) 
                if not got_slot:
                    log_message("TIMEOUT: Arduino stopped responding.")
                    break
            
            # 2. SEND COMMAND
            if self.is_running:
                next_line = self.lines[self.lines_sent]
                if not self.send_line(next_line):
                    break
                
                # Tiny sleep to let the serial bus breathe, but fast enough to keep buffer full
                time.sleep(0.002) 

        current_gcode_runner = None
        log_message("Design Completed.")

# === SERIAL READER THREAD ===
def read_from_serial():
    global current_gcode_runner
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    # Log everything from Arduino
                    log_message(f"Ard: {line}") 
                    
                    # If we are running a job, pass the message to the runner
                    # so it can count "Done" signals
                    if current_gcode_runner:
                        current_gcode_runner.process_incoming_serial(line)
            else:
                time.sleep(0.01) 
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            time.sleep(1)

if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()


# === UTILITY FUNCTIONS ===

def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('10.254.254.254', 1)) 
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = '127.0.0.1'
    return ip

# === FLASK ROUTES ===

@app.route("/")
def index():
    current_ip = get_current_ip()
    # Force setup if on Hotspot
    if current_ip in ["10.42.0.1", "192.168.4.1"]:
        return redirect(url_for('wifi_setup_page'))
    return render_template("designs.html")

@app.route("/wifi_setup")
def wifi_setup_page():
    networks = wifi_tools.get_wifi_networks()
    saved_networks = wifi_tools.get_saved_networks()
    current_ip = get_current_ip()
    hostname = socket.gethostname()
    return render_template("wifi_setup.html", networks=networks, saved_networks=saved_networks, ip_address=current_ip, hostname=hostname)

@app.route("/api/forget_wifi", methods=["POST"])
def forget_wifi_route():
    data = request.json
    ssid = data.get("ssid")
    if not ssid: return jsonify(success=False, message="No SSID")
    log_message(f"Forgetting: {ssid}")
    success, msg = wifi_tools.forget_network(ssid)
    return jsonify(success=success, message=msg)

@app.route("/check_password", methods=["POST"])
def check_password_route():
    data = request.json
    if data.get("password") == "2025":
        return jsonify(success=True)
    return jsonify(success=False), 401

@app.route("/shutdown", methods=["POST"])
def shutdown():
    log_message("System shutting down...")
    subprocess.Popen(["sudo", "shutdown", "now"])
    return jsonify(success=True)

@app.route("/reboot", methods=["POST"])
def reboot():
    log_message("System rebooting...")
    subprocess.Popen(["sudo", "reboot"])
    return jsonify(success=True)

@app.route("/pull", methods=["POST"])
def git_pull():
    log_message("Running git pull...")
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=".", check=True, timeout=15)
        output = result.stdout.strip() if result.stdout else "No output."
        log_message(f"Git Output: {output[:50]}...")
        return jsonify(success=True, message=output[:100])
    except Exception as e:
        log_message(f"Git Failed: {e}")
        return jsonify(success=False, message=str(e)), 500

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    global arduino, arduino_connected
    log_message("FIRMWARE UPDATE STARTED...")
    if arduino and arduino.is_open:
        arduino.close()
    arduino_connected = False
    try:
        log_message("Compiling...")
        subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)
        log_message("Uploading...")
        subprocess.run(["arduino-cli", "upload", "-p", "/dev/ttyACM0", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)
        success_msg = "Firmware updated."
    except Exception as e:
        log_message(f"UPDATE ERROR: {str(e)}")
        success_msg = f"Error: {str(e)}"
    
    # Reconnect
    try:
        time.sleep(2) 
        arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1)
        arduino_connected = True
        threading.Thread(target=read_from_serial, daemon=True).start()
        log_message("Serial reconnected.")
    except Exception as e:
        log_message(f"FAILED TO RECONNECT SERIAL: {e}")
        
    return jsonify(success=True, message=success_msg)

# === COMMAND ROUTES ===

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode_block = data.get("gcode")
    speed_override = data.get("speed_override") 

    if not gcode_block: return jsonify(success=False, error="No G-code received."), 400
    if not arduino_connected: return jsonify(success=False, error="Arduino not connected."), 500

    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive():
        return jsonify(success=False, error="Busy. Clear queue first."), 503

    # Backward compatibility for Speed Override (if used by old controls)
    # The new designs.html applies speed directly to the G-code string, so this might be null
    if speed_override:
        new_block = []
        for line in gcode_block.split('\n'):
            if line.strip().startswith('G1'):
                parts = line.split()
                if len(parts) >= 3:
                    new_block.append(f"{parts[0]} {parts[1]} {parts[2]} {speed_override}")
            else:
                new_block.append(line)
        gcode_block = "\n".join(new_block)
        log_message(f"Speed override applied: {speed_override}µs")

    # --- AUTO-RESUME FEATURE ---
    # Ensure the table is not paused when starting a new design
    with lock:
        arduino.write(b"RESUME\n")
    log_message("Auto-Resumed for new job.")

    # Start the job
    runner = GCodeRunner(gcode_block)
    runner.start()
    return jsonify(success=True, message="Started.")

@app.route("/send_single_gcode_line", methods=["POST"])
def send_single_gcode_line_route():
    data = request.json
    line = data.get("gcode_line", "").strip()

    if not line: return jsonify(success=False), 400
    if not arduino_connected: return jsonify(success=False, error="No Arduino"), 500

    if not line.upper().startswith('G1'):
         return jsonify(success=False, error="Only G1 allowed"), 400

    try:
        with lock:
            arduino.write((line + "\n").encode())
        log_message(f"Manual: {line}")
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    log_message(f"WEB RECEIVED: {cmd}")

    if not cmd: return jsonify(success=False)

    try:
        # BLE COMMANDS
        if cmd.startswith("LED:") or cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
            if cmd.startswith("LED:"):
                parts = cmd.split(":")[1].split(",")
                r, g, b, br = map(int, parts)
                asyncio.run_coroutine_threadsafe(ble_controller.send_led_command(r, g, b, br), ble_controller.loop)
            else:
                asyncio.run_coroutine_threadsafe(ble_controller.handle_command(cmd), ble_controller.loop)
            return jsonify(success=True, message="Queued")

        # ARDUINO COMMANDS
        if arduino_connected:
            # --- DEEP CLEAR FEATURE ---
            if cmd == "CLEAR":
                global current_gcode_runner
                # 1. Kill the Python Thread so it stops sending
                if current_gcode_runner and current_gcode_runner.is_alive():
                    current_gcode_runner.is_running = False 
                    # Wake it up if it's waiting for credits so it can die gracefully
                    current_gcode_runner.slot_available_event.set()
                    log_message("Stopping active print job thread...")
                
                # 2. Tell Arduino to dump its physical buffer
                with lock:
                    arduino.write((cmd + "\n").encode())
                
                log_message("Queue Cleared.")
                return jsonify(success=True, message="Queue cleared and job stopped.")

            # Normal Command
            with lock:
                arduino.write((cmd + "\n").encode())
            log_message(f"Sent: {cmd}")
            return jsonify(success=True)
        
        log_message(f"Command '{cmd}' ignored (No Connection).")
        return jsonify(success=False, error="No device connected."), 500

    except Exception as e:
        log_message(f"Error: {e}")
        return jsonify(success=False, error=str(e)), 500

@app.route("/status", methods=["GET"])
def get_status():
    return jsonify({"connected": ble_controller.is_connected()})

@app.route("/terminal/logs")
def get_logs():
    with lock:
        return jsonify(list(serial_log))

# --- TEMPLATE ROUTES ---
@app.route("/controls")
def controls(): return render_template("index.html")
@app.route("/terminal")
def terminal(): return render_template("terminal.html")
@app.route("/led_controls")
def led_controls(): return render_template("led_controls.html")
@app.route("/script")
def script(): return render_template("script.html")
@app.route("/AI_builder")
def AI_builder(): return render_template("AI_builder.html")
@app.route("/designs")
def designs(): return render_template("designs.html")

@app.route('/designs/<path:filename>')
def serve_design_file(filename):
    return send_from_directory(os.path.join(app.root_path, 'templates', 'designs'), filename)

@app.route('/api/designs')
def list_designs():
    try:
        files = [f for f in os.listdir(os.path.join(app.root_path, 'templates', 'designs')) if f.endswith('.txt')]
        return jsonify(files)
    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

