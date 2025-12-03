from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess
import os
import re
import asyncio
import socket
from collections import deque
# Assuming these modules exist in your project folder
import wifi_tools 
import ble_controller 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

# === CONFIGURATION ===
DESIGNS_FOLDER = os.path.join(app.root_path, 'templates', 'designs')
if not os.path.exists(DESIGNS_FOLDER):
    try:
        os.makedirs(DESIGNS_FOLDER)
        print(f"Created designs folder at: {DESIGNS_FOLDER}")
    except Exception as e:
        print(f"Error creating designs folder: {e}")

@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === STATE MANAGEMENT ===
job_queue = deque()      # High Priority (Manual adds)
loop_playlist = []       # Low Priority (Looping files)
is_looping = False       # Flag

current_job_name = None  # What is currently running
next_job_name = None     # What is coming up next

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

# === G-CODE RUNNER (FLOW CONTROL) ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block, filename, on_complete=None):
        super().__init__(daemon=True)
        # Filter and store only G1 commands
        self.lines = [
            line.split(';')[0].strip() for line in gcode_block.split('\n') 
            if line.split(';')[0].strip().upper().startswith('G1')
        ]
        self.total_lines = len(self.lines)
        self.filename = filename
        self.is_running = True
        self.on_complete = on_complete
        
        # FLOW CONTROL CONFIGURATION
        self.ARDUINO_BUFFER_SIZE = 40  
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        
        # Thread Event: Used to pause sending when credits are 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
        """Called by the serial reader thread when Arduino speaks."""
        line = line.strip().upper()
        if line == "DONE":
            with lock:
                self.credits += 1
                self.slot_available_event.set() 

    def send_line(self, line):
        try:
            with lock:
                arduino.write((line + "\n").encode())
            self.lines_sent += 1
            self.credits -= 1 
            return True
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner, current_job_name
        current_gcode_runner = self
        current_job_name = self.filename
        
        log_message(f"Job Started: {self.filename}")

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
                
                # Tiny sleep to let the serial bus breathe
                time.sleep(0.002) 

        current_job_name = None
        current_gcode_runner = None
        log_message("Design Completed.")
        
        if self.on_complete:
            self.on_complete()

# === JOB MANAGER ===
def process_queue():
    """Decides what runs next. Handles the 1-minute wait logic."""
    global current_job_name, is_looping

    # If something is already running, do nothing
    if current_gcode_runner and current_gcode_runner.is_alive():
        return

    # Determine next job
    next_job = None
    
    # 1. Check High Priority Queue
    if len(job_queue) > 0:
        next_job = job_queue.popleft()
    
    # 2. Check Loop (if Queue empty)
    elif is_looping and len(loop_playlist) > 0:
        # Move first item to end (Rotate)
        next_file = loop_playlist.pop(0)
        loop_playlist.append(next_file)
        
        # Load the file content
        try:
            path = os.path.join(DESIGNS_FOLDER, next_file)
            with open(path, 'r') as f:
                gcode = f.read()
            next_job = {'gcode': gcode, 'filename': next_file}
        except Exception as e:
            log_message(f"Loop Load Error: {e}")
            process_queue() # Skip and try next
            return

    # If we found a job, Execute the Wait Logic then Start
    if next_job:
        # --- THE "M0" 60 SECOND WAIT FEATURE ---
        log_message("Design complete. Pausing motors for 60s...")
        if arduino_connected:
            with lock: arduino.write(b"PAUSE\n")
        
        # Wait 60 seconds
        for _ in range(60): 
            # (Optional: Add logic here to break early if needed)
            time.sleep(1)
        
        # After wait, RESUME motors
        if arduino_connected:
            with lock: arduino.write(b"RESUME\n")
            
        start_job(next_job)
    else:
        log_message("Queue empty. Standing by.")
        current_job_name = None

def start_job(job_data):
    if not arduino_connected: return
    
    # Ensure motors are active (Resume)
    with lock:
        arduino.write(b"RESUME\n") 
    
    runner = GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=process_queue)
    runner.start()

# === SERIAL READER THREAD ===
def read_from_serial():
    global current_gcode_runner
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(f"Ard: {line}") 
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

# === FLASK ROUTES (ALL RESTORED) ===

@app.route("/")
def index():
    current_ip = get_current_ip()
    # Force setup if on Hotspot
    if current_ip in ["10.42.0.1", "192.168.4.1"]:
        return redirect(url_for('wifi_setup_page'))
    return render_template("designs.html")

@app.route("/status_full", methods=["GET"])
def status_full():
    """Returns detailed status for the UI"""
    next_text = "None"
    if len(job_queue) > 0:
        next_text = job_queue[0]['filename'].replace('.txt', '')
    elif is_looping and len(loop_playlist) > 0:
        next_text = f"{loop_playlist[0].replace('.txt', '')} (Loop)"
        
    return jsonify({
        "playing": current_job_name.replace('.txt', '') if current_job_name else None,
        "queue_count": len(job_queue),
        "next_up": next_text,
        "is_looping": is_looping
    })

@app.route("/set_loop", methods=["POST"])
def set_loop():
    global is_looping, loop_playlist
    data = request.json
    files = data.get("files", [])
    if not files: return jsonify(success=False)
    loop_playlist = files
    is_looping = True
    log_message(f"Loop started with {len(files)} designs.")
    # If nothing running, kickstart
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        process_queue()
    return jsonify(success=True)

@app.route("/cancel_loop", methods=["POST"])
def cancel_loop():
    global is_looping, loop_playlist
    is_looping = False
    loop_playlist = []
    log_message("Loop cancelled.")
    return jsonify(success=True)

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode = data.get("gcode")
    filename = data.get("filename", "Unknown")

    if not gcode: return jsonify(success=False, error="No G-code received."), 400
    if not arduino_connected: return jsonify(success=False, error="Arduino not connected."), 500

    # Add to High Priority Queue
    job_queue.append({'gcode': gcode, 'filename': filename})
    
    # If idle, start immediately
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        process_queue()
        return jsonify(success=True, message=f"Started {filename}")
    else:
        return jsonify(success=True, message=f"Added to Queue")

@app.route("/delete_design", methods=["POST"])
def delete_design():
    data = request.json
    filename = data.get("filename")
    if not filename: return jsonify(success=False, error="No filename")
    
    clean_name = os.path.basename(filename)
    path_txt = os.path.join(DESIGNS_FOLDER, clean_name)
    path_png = os.path.join(DESIGNS_FOLDER, clean_name.replace('.txt','.png'))
    
    try:
        if os.path.exists(path_txt):
            os.remove(path_txt)
            if os.path.exists(path_png): os.remove(path_png)
            log_message(f"Deleted: {clean_name}")
            return jsonify(success=True)
        else:
            return jsonify(success=False, error="File not found")
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    
    if cmd == "CLEAR":
        global current_gcode_runner, is_looping, loop_playlist
        # Stop current
        if current_gcode_runner and current_gcode_runner.is_alive():
            current_gcode_runner.is_running = False 
            current_gcode_runner.slot_available_event.set()
        
        # Clear Data
        job_queue.clear()
        loop_playlist = []
        is_looping = False
        
        if arduino_connected:
            with lock: arduino.write(b"CLEAR\n")
        log_message("Queue and current job cleared.")
        return jsonify(success=True)

    # BLE COMMANDS
    if cmd.startswith("LED:") or cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
        if cmd.startswith("LED:"):
            parts = cmd.split(":")[1].split(",")
            r, g, b, br = map(int, parts)
            asyncio.run_coroutine_threadsafe(ble_controller.send_led_command(r, g, b, br), ble_controller.loop)
        else:
            asyncio.run_coroutine_threadsafe(ble_controller.handle_command(cmd), ble_controller.loop)
        return jsonify(success=True, message="Queued")

    if arduino_connected:
        with lock: arduino.write((cmd + "\n").encode())
        return jsonify(success=True)
    
    return jsonify(success=False, error="Not connected")

# === ORIGINAL SYSTEM ROUTES ===

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
        subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True, capture_output=True, text=True)
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
    except Exception as e:
        log_message(f"FAILED TO RECONNECT: {e}")
        
    return jsonify(success=True, message=success_msg)

# === NAVIGATION ROUTES ===
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
    return send_from_directory(DESIGNS_FOLDER, filename)

@app.route('/api/designs')
def list_designs():
    try:
        files = [f for f in os.listdir(DESIGNS_FOLDER) if f.endswith('.txt')]
        return jsonify(files)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/terminal/logs")
def get_logs():
    with lock:
        return jsonify(list(serial_log))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
