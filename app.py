from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory
import serial, threading, time, subprocess
import os
import re
import asyncio
import wifi_tools 
import ble_controller 
import socket  # Ensure this is imported at the top


app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === SERIAL LOGGING SETUP ===
serial_log = []
lock = threading.Lock()

def log_message(msg):
    """Adds a message to the serial log thread-safely."""
    timestamp = time.strftime("[%H:%M:%S] ")
    with lock:
        serial_log.append(timestamp + msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# CONNECT BLE LOGS TO FLASK
ble_controller.set_logger(log_message)

# === SERIAL SETUP ===
arduino = None
arduino_connected = False

try:
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1) 
    time.sleep(2)
    arduino_connected = True
except Exception as e:
    print(f"WARNING: Arduino not connected: {e}") 
    log_message(f"Arduino Init Failed: {e}")

def read_from_serial():
    global current_gcode_runner
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(line)
                    if current_gcode_runner and line.strip().upper() == "DONE":
                        current_gcode_runner.handshake_event.set()       
            else:
                time.sleep(0.01) 
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            time.sleep(1)

current_gcode_runner = None
def get_current_ip():
    try:
        # We don't actually connect, just check how we WOULD route to the internet
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        # This doesn't send data, just checks the routing table
        s.connect(('10.254.254.254', 1)) 
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = '127.0.0.1'
    return ip

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
        
        PRIME_COUNT = 8 
        log_message(f"Starting G-code: {num_commands} lines.")

        for _ in range(PRIME_COUNT):
            if self.is_running and self.line_index < num_commands:
                self.send_line(self.lines[self.line_index])
                time.sleep(0.05) 
            else:
                break 
        
        if self.is_running and self.line_index > 0:
            time.sleep(0.1)

        while self.is_running and self.line_index < num_commands:
            if self.handshake_event.wait(timeout=None): 
                self.handshake_event.clear()
                if not self.send_line(self.lines[self.line_index]):
                    break 
            else:
                log_message("TIMEOUT: Arduino failed to respond.")
                self.is_running = False

        current_gcode_runner = None
        log_message("G-code finished.")


if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()


# ---------------- UTILITY ROUTES ----------------

@app.route("/wifi_setup")
def wifi_setup_page():
    networks = wifi_tools.get_wifi_networks()
    current_ip = get_current_ip()
    hostname = socket.gethostname()
    
    return render_template(
        "wifi_setup.html", 
        networks=networks, 
        ip_address=current_ip, 
        hostname=hostname  # <--- Pass it here
    )

@app.route("/api/configure_wifi", methods=["POST"])
def configure_wifi():
    data = request.json
    ssid = data.get("ssid")
    password = data.get("password")
    
    if not ssid:
        return jsonify(success=False, message="No SSID provided")

    log_message(f"Attempting to connect to {ssid}...")
    success, msg = connect_to_wifi(ssid, password)
    
    if success:
        # Optional: Trigger a reboot after 5 seconds so the connection takes over
        def reboot_later():
            time.sleep(5)
            subprocess.run(["sudo", "reboot"])
        threading.Thread(target=reboot_later).start()
        
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
    try:
        subprocess.Popen(["sudo", "shutdown", "now"])
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

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

@app.route("/reboot", methods=["POST"])
def reboot():
    log_message("System rebooting...")
    try:
        subprocess.Popen(["sudo", "reboot"])
        return jsonify(success=True)
    except Exception as e:
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
    try:
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
    return render_template("terminal.html")

@app.route("/led_controls")
def led_controls():
    return render_template("led_controls.html")

@app.route("/script")
def script():
    return render_template("script.html")
    
@app.route("/AI_builder")
def AI_builder():
    return render_template("AI_builder.html")

@app.route("/designs")
def designs():
    return render_template("designs.html")

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

# ---------------- COMMAND HANDLING ----------------

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode_block = data.get("gcode")
    speed_override = data.get("speed_override")

    if not gcode_block: return jsonify(success=False, error="No G-code received."), 400
    if not arduino_connected: return jsonify(success=False, error="Arduino not connected."), 500

    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive():
        return jsonify(success=False, error="Busy."), 503

    if speed_override:
        new_block = []
        for line in gcode_block.split('\n'):
            line = line.strip()
            if line.startswith('G1'):
                parts = line.split()
                if len(parts) >= 3:
                    new_block.append(f"{parts[0]} {parts[1]} {parts[2]} {speed_override}")
                else:
                    new_block.append(line)
            else:
                new_block.append(line)
        gcode_block = "\n".join(new_block)
        log_message(f"Speed set to {speed_override}µs")

    process_and_send_gcode(gcode_block)
    return jsonify(success=True, message="Started.")

def process_and_send_gcode(gcode_block):
    runner = GCodeRunner(gcode_block)
    runner.start()

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
    
    # *** FIX: LOGGING ADDED HERE ***
    # This forces a log into the terminal so you KNOW the button worked.
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
