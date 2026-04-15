from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess, sys
import os
import re
import socket
import json
import datetime
from collections import deque
import wifi_tools 
from pyngrok import ngrok, conf 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

# === CONFIGURATION ===
BASE_DIR = app.root_path
DESIGNS_FOLDER = os.path.join(BASE_DIR, 'templates', 'designs')
SCHEDULE_FILE = os.path.join(BASE_DIR, 'schedules.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
ARDUINO_PROJECT_PATH = os.path.join(BASE_DIR, 'Sand') 

# Default Settings
DEFAULT_SETTINGS = {
    "cooldown": 30,
    "speed": 1.0
}

# Load Settings Helper
def load_app_settings():
    if not os.path.exists(SETTINGS_FILE): return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            return merged
    except: return DEFAULT_SETTINGS.copy()

def save_app_settings(data):
    try:
        with open(SETTINGS_FILE, 'w') as f: json.dump(data, f)
    except Exception as e:
        print(f"Error saving settings: {e}")

# Initialize Global Settings
SYSTEM_SETTINGS = load_app_settings()

# === UTILITIES ===
def find_available_port():
    for port in [5000, 6000]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port 
            except OSError:
                print(f"⚠️ Port {port} is busy. Trying next...")
                continue
    return 5000 

def find_arduino_port():
    # Priority for Hardware UART (RX/TX on pins 8/10)
    ports = [
        '/dev/serial0', '/dev/ttyAMA0', '/dev/ttyS0', # Hardware UART (RX/TX)
        '/dev/ttyUSB0', '/dev/ttyUSB1',               # USB Serial (CH340/FTDI)
        '/dev/ttyACM0', '/dev/ttyACM1'                # USB CDC (Uno/Mega/Leo)
    ]
    for p in ports:
        if os.path.exists(p):
            return p
    return None

def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.254.254.254', 1))
        ip = s.getsockname()[0]; s.close()
    except: ip = '127.0.0.1'
    return ip

# === STATE MANAGEMENT ===
job_queue = deque()      
loop_playlist = []       
is_looping = False       
is_paused = False        
is_waiting = False       
is_calibrating = False   
calibration_done = False 
skip_cooldown = False    

current_job_name = None  
next_job_name = None     

serial_log = []
lock = threading.Lock()

def log_message(msg):
    timestamp = time.strftime("[%H:%M:%S] ")
    with lock:
        serial_log.append(timestamp + msg)
        if len(serial_log) > 200:
            serial_log.pop(0)

# === SERIAL CONNECTION ===
arduino = None
arduino_connected = False
current_gcode_runner = None
arduino_port = "/dev/ttyUSB0" # Default

def send_speed_to_arduino():
    global arduino_connected
    if arduino_connected:
        spd = SYSTEM_SETTINGS.get("speed", 1.0)
        cmd = f"SPEED {spd}\n"
        with lock: arduino.write(cmd.encode())
        log_message(f"Sent initial speed: {spd}")

def connect_arduino():
    global arduino, arduino_connected, arduino_port
    try:
        port = find_arduino_port()
        if not port:
            raise Exception("No serial port found (UART or USB)")
        
        arduino_port = port
        # Updated to 250000 baud per request
        arduino = serial.Serial(port, 250000, timeout=0.1) 
        time.sleep(2) 
        arduino_connected = True
        threading.Thread(target=read_from_serial, daemon=True).start()
        log_message(f"Arduino Connected: {port} @ 250000")
        print(f"Connected to Arduino on {port}")
        
        # Send current speed setting
        send_speed_to_arduino()
    except Exception as e:
        arduino_connected = False
        print(f"WARNING: Arduino not connected: {e}") 
        log_message(f"Arduino Init Failed: {e}")

# === PERSISTENT LED HELPER ===
def send_led_persistent(r, g, b):
    """Sends RGB values to Arduino via Serial."""
    if arduino_connected:
        try:
            cmd = f"{r},{g},{b}\n"
            with lock: arduino.write(cmd.encode())
            log_message(f"LED Serial: {r},{g},{b}")
            return True
        except Exception as e:
            log_message(f"LED Serial Error: {e}")
    else:
        log_message("LED Fail: Arduino not connected.")
    return False

# === SCHEDULER ===
def load_schedules():
    if not os.path.exists(SCHEDULE_FILE): return []
    try:
        with open(SCHEDULE_FILE, 'r') as f: return json.load(f)
    except: return []

def save_schedules(data):
    with open(SCHEDULE_FILE, 'w') as f: json.dump(data, f)

def hex_to_rgb(hex_val):
    hex_val = hex_val.lstrip('#')
    return tuple(int(hex_val[i:i+2], 16) for i in (0, 2, 4))

class SchedulerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.last_minute_checked = None

    def run(self):
        log_message("Scheduler Service Started")
        while True:
            now = datetime.datetime.now()
            current_time = now.strftime("%H:%M") 
            current_day = now.strftime("%a")     
            
            if self.last_minute_checked != current_time:
                self.last_minute_checked = current_time
                self.check_triggers(current_time, current_day)
            
            time.sleep(5) 

    def check_triggers(self, time_str, day_str):
        schedules = load_schedules()
        for item in schedules:
            if item['time'] == time_str and day_str in item['days']:
                self.execute_action(item)

    def execute_action(self, item):
        global is_looping, loop_playlist, is_paused
        action = item['type']
        val = item.get('value')
        log_message(f"Scheduler Trigger: {action}")

        if action == "led_off":
            # UPDATED: Set Color to Black (Serial)
            send_led_persistent(0, 0, 0)
        
        elif action == "led_color" and val:
            try:
                r, g, b = hex_to_rgb(val)
                # UPDATED: Use Serial Sender
                send_led_persistent(r, g, b)
            except: pass
            
        elif action == "stop_sand":
            # UPDATED: 'Stop' button logic changed to PAUSE per request
            is_paused = True
            if arduino_connected:
                with lock: arduino.write(b"PAUSE\n")
            log_message("Automation: Sand Table Paused.")

        elif action == "sand_shuffle":
            try:
                files = [f for f in os.listdir(DESIGNS_FOLDER) if f.endswith('.txt')]
                if files:
                    import random
                    random.shuffle(files)
                    loop_playlist = files
                    is_looping = True
                    log_message(f"Scheduler: Loop started with {len(files)} designs.")
                    if current_gcode_runner is None or not current_gcode_runner.is_alive():
                        process_queue(wait_enabled=False)
            except Exception as e: log_message(str(e))
        elif action == "sand_specific" and val:
            path = os.path.join(DESIGNS_FOLDER, val)
            if os.path.exists(path):
                with open(path, 'r') as f: gcode = f.read()
                if current_gcode_runner is None or not current_gcode_runner.is_alive():
                    start_job({'gcode': gcode, 'filename': val})
                else:
                    job_queue.append({'gcode': gcode, 'filename': val})

# === THETA-RHO RUNNER ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block, filename, on_complete=None):
        super().__init__(daemon=True)
        # Parse lines, stripping comments and keeping non-empty lines
        self.lines = [l.split('#')[0].strip() for l in gcode_block.split('\n') if l.split('#')[0].strip()]
        self.total_lines = len(self.lines)
        self.filename = filename
        self.is_running = True
        self.on_complete = on_complete
        self.ARDUINO_BUFFER_SIZE = 1 # Simple 1-line-at-a-time for Theta-Rho
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
        clean_line = line.strip().upper()
        if clean_line == "OK" or clean_line == "RGB_OK":
            with lock:
                if self.credits < self.ARDUINO_BUFFER_SIZE: self.credits += 1
                self.slot_available_event.set() 

    def send_line(self, line):
        try:
            log_message(f"TX (Runner): {line}")
            with lock: arduino.write((line + "\n").encode())
            self.lines_sent += 1
            self.credits -= 1 
            return True
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner, current_job_name, is_paused
        current_gcode_runner = self
        current_job_name = self.filename
        is_paused = False 
        
        log_message(f"Job Started: {self.filename}")

        while self.is_running and self.lines_sent < self.total_lines:
            if self.credits <= 0:
                self.slot_available_event.clear()
                if not self.slot_available_event.wait(timeout=10.0): break
            if self.is_running:
                if not self.send_line(self.lines[self.lines_sent]): break
                time.sleep(0.002) 

        if self.is_running:
            while self.credits < self.ARDUINO_BUFFER_SIZE:
                time.sleep(0.2)
                if not self.is_running: break

        current_job_name = None
        current_gcode_runner = None
        if self.on_complete: self.on_complete()

def on_job_finished(): process_queue(wait_enabled=True)

def process_queue(wait_enabled=True):
    global current_job_name, is_looping, is_waiting, is_paused
    if current_gcode_runner and current_gcode_runner.is_alive(): return

    next_job = None
    if len(job_queue) > 0: next_job = job_queue.popleft()
    elif is_looping and len(loop_playlist) > 0:
        next_file = loop_playlist.pop(0)
        loop_playlist.append(next_file) 
        try:
            with open(os.path.join(DESIGNS_FOLDER, next_file), 'r') as f: 
                next_job = {'gcode': f.read(), 'filename': next_file}
        except: process_queue(wait_enabled=False); return

    if next_job:
        if wait_enabled:
            is_waiting = True
            
            # --- UPDATED COOLDOWN LOGIC ---
            wait_time = int(SYSTEM_SETTINGS.get('cooldown', 30))
            log_message(f"Cooling down for {wait_time}s...")
            
            if arduino_connected:
                with lock: arduino.write(b"PAUSE\n")
            
            for _ in range(wait_time): 
                if skip_cooldown: break
                if not is_looping and len(job_queue) == 0: break
                time.sleep(1)
            is_waiting = False
            skip_cooldown = False
        start_job(next_job)
    else:
        log_message("Queue empty.")
        current_job_name = None
        if arduino_connected:
            with lock: arduino.write(b"PAUSE\n")

def start_job(job_data):
    if not arduino_connected: return
    with lock: arduino.write(b"RESUME\n") 
    GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=on_job_finished).start()

def read_from_serial():
    global current_gcode_runner, is_calibrating, calibration_done
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    log_message(f"Ard: {line}")
                    if "STATUS:CALIBRATING" in line:
                        is_calibrating = True
                        calibration_done = False
                        log_message("Calibration Started...")
                    if "CALIBRATION_COMPLETE" in line:
                        is_calibrating = False
                        calibration_done = True
                        log_message("CALIBRATION COMPLETE!")
                    
                    if current_gcode_runner: current_gcode_runner.process_incoming_serial(line)
            else: time.sleep(0.01) 
        except Exception: 
            time.sleep(1) # RETRY on error

@app.route("/api/calibration_status")
def calibration_status():
    global is_calibrating, calibration_done
    return jsonify({
        "is_calibrating": is_calibrating,
        "calibration_done": calibration_done
    })

@app.route("/api/skip_cooldown", methods=["POST"])
def skip_cooldown_route():
    global skip_cooldown
    skip_cooldown = True
    return jsonify(success=True)

# === TUNNELING SERVICE ===
@app.route("/api/tunnel", methods=["GET"])
def get_tunnel_status():
    public_url = None
    try:
        tunnels = ngrok.get_tunnels()
        public_url = tunnels[0].public_url if tunnels else None
    except: pass
    has_token = False
    try:
        if os.path.exists(conf.get_default().config_path):
            with open(conf.get_default().config_path, 'r') as f:
                if "authtoken" in f.read(): has_token = True
    except: pass
    return jsonify({"public_url": public_url, "has_token": has_token})

@app.route("/api/tunnel/key", methods=["POST"])
def set_tunnel_key():
    try:
        ngrok.set_auth_token(request.json.get("token"))
        return jsonify(success=True)
    except Exception as e: return jsonify(success=False, message=str(e))

@app.route("/api/tunnel/start", methods=["POST"])
def start_tunnel():
    try:
        ngrok.kill()
        time.sleep(1)
    except: pass
    try:
        url = ngrok.connect(SERVER_PORT).public_url
        log_message(f"Tunnel Started: {url}")
        return jsonify(success=True, public_url=url)
    except Exception as e: return jsonify(success=False, message=str(e))

@app.route("/api/tunnel/stop", methods=["POST"])
def stop_tunnel():
    try:
        ngrok.kill()
        return jsonify(success=True)
    except Exception as e: return jsonify(success=False, message=str(e))

# === AUTO-START NGROK (RUNS IN THREAD) ===
def auto_start_ngrok_thread():
    # Wait 90 seconds to match the successful manual connection timestamp
    time.sleep(90)
    log_message("Checking network connection for Ngrok auto-start...")
    
    try:
        # Try exactly once to reach an external server
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        log_message("No internet/WiFi detected. Skipping Ngrok auto-start.")
        return

    try:
        # Replicating the successful manual start logic to avoid the NoneType config error
        try:
            ngrok.kill()
            time.sleep(1)
        except: pass
        
        log_message("Internet confirmed. Auto-starting Ngrok...")
        url = ngrok.connect(SERVER_PORT).public_url
        log_message(f"Ngrok Auto-Started: {url}")
        print(f" * Public URL: {url}")
    except Exception as e:
        log_message(f"Ngrok Auto-Start Failed: {e}")

# === FLASK ROUTES ===
def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.254.254.254', 1)) 
        ip = s.getsockname()[0]; s.close()
    except: ip = '127.0.0.1'
    return ip

@app.route("/")
def index():
    if get_current_ip() in ["10.42.0.1", "192.168.4.1"]: return redirect(url_for('wifi_setup_page'))
    return render_template("designs.html")

@app.route("/controller")
def controller_page(): return render_template("controller.html")

@app.route("/api/move", methods=["POST"])
def manual_move():
    if not arduino_connected: return jsonify(success=False, error="Arduino Disconnected")
    data = request.json
    theta = data.get("theta")
    rho = data.get("rho")
    # Send as raw theta rho to the firmware
    cmd = f"{theta} {rho}\n"
    log_message(f"TX (Manual): {cmd.strip()}")
    with lock: arduino.write(cmd.encode())
    return jsonify(success=True)

@app.route("/settings")
def settings_page(): return render_template("settings.html")

# --- SETTINGS API ---
@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    global SYSTEM_SETTINGS
    if request.method == "GET":
        return jsonify(SYSTEM_SETTINGS)
    if request.method == "POST":
        data = request.json
        SYSTEM_SETTINGS.update(data)
        save_app_settings(SYSTEM_SETTINGS)
        if 'speed' in data:
            send_speed_to_arduino()
        log_message(f"Settings updated: {SYSTEM_SETTINGS}")
        return jsonify(success=True)

@app.route("/pull", methods=["POST"])
def git_pull():
    try:
        r = subprocess.run(["git", "pull"], cwd=BASE_DIR, capture_output=True, text=True, timeout=30)
        return jsonify(success=(r.returncode==0), message=r.stdout[:500] if r.returncode==0 else r.stderr)
    except Exception as e: return jsonify(success=False, message=str(e))

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    global arduino, arduino_connected, arduino_port
    if arduino: 
        try: arduino.close()
        except: pass
        arduino_connected = False
    
    # EXACT FQBN and Parameters that worked manually
    FQBN = "lgt8fx:avr:328:clock_source=internal,clock_div=1,variant=modelP,upload_speed=115200"
    
    # LGT8fx index URL (kept as safety)
    LGT_URL = "https://raw.githubusercontent.com/dbuezas/lgt8fx/master/package_lgt8fx_index.json"
    
    try:
        log_message(f"Starting firmware update on {arduino_port}...")
        
        # 1. Compile & Upload in one go (EXACTLY matching working manual command)
        # Using the same --upload flag and parameters
        flash_cmd = [
            "arduino-cli", "compile", 
            "--additional-urls", LGT_URL,
            "--fqbn", FQBN, 
            ARDUINO_PROJECT_PATH, 
            "--upload", "-p", arduino_port
        ]
        
        log_message(f"Flashing: {' '.join(flash_cmd)}")
        
        # Run and capture output
        result = subprocess.run(flash_cmd, capture_output=True, text=True, check=True)
        
        log_message("Upload Successful!")
        connect_arduino()
        return jsonify(success=True, message="Firmware Updated Successfully!")
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Flash Error: {e.stderr or e.stdout}"
        log_message(error_msg)
        print(error_msg)
        connect_arduino()
        return jsonify(success=False, message=error_msg)
    except Exception as e:
        log_message(f"System Error: {str(e)}")
        connect_arduino()
        return jsonify(success=False, message=str(e))

@app.route("/shutdown", methods=["POST"])
def shutdown(): subprocess.Popen(["sudo", "shutdown", "now"]); return jsonify(success=True)
@app.route("/reboot", methods=["POST"])
def reboot(): subprocess.Popen(["sudo", "reboot"]); return jsonify(success=True)

@app.route("/api/schedule", methods=["GET", "POST", "DELETE"])
def api_schedule():
    if request.method == "GET": return jsonify(load_schedules())
    if request.method == "POST":
        d = request.json; s = load_schedules(); s.append(d); save_schedules(s)
        return jsonify(success=True)
    if request.method == "DELETE":
        idx = request.json.get('index'); s = load_schedules()
        if 0<=idx<len(s): del s[idx]; save_schedules(s)
        return jsonify(success=True)

@app.route("/status")
def get_status():
    return jsonify({"connected": arduino_connected, "port": arduino_port})

@app.route("/status_full", methods=["GET"])
def status_full():
    q = []
    for i, j in enumerate(job_queue): q.append({"index": i, "name": j['filename'].replace('.txt', ''), "type": "queue"})
    if is_looping:
        for i in range(min(len(loop_playlist), 10)): q.append({"index": i, "name": loop_playlist[i].replace('.txt', ''), "type": "loop"})
    return jsonify({
        "playing": current_job_name.replace('.txt', '') if current_job_name else None,
        "queue_count": len(job_queue),
        "queue_items": q,
        "next_up": q[0]["name"] if q else "None",
        "is_looping": is_looping, "is_paused": is_paused, "is_waiting": is_waiting  
    })

@app.route("/remove_from_queue", methods=["POST"])
def remove_from_queue():
    try:
        idx = int(request.json.get("index")); typ = request.json.get("type")
        if typ == "queue": del job_queue[idx]
        elif typ == "loop": del loop_playlist[idx]
        return jsonify(success=True)
    except: return jsonify(success=False)

@app.route("/set_loop", methods=["POST"])
def set_loop():
    global is_looping, loop_playlist
    loop_playlist = request.json.get("files", []); is_looping = True
    if not current_gcode_runner or not current_gcode_runner.is_alive(): process_queue(wait_enabled=False)
    return jsonify(success=True)

@app.route("/cancel_loop", methods=["POST"])
def cancel_loop():
    global is_looping, loop_playlist; is_looping = False; loop_playlist = []; return jsonify(success=True)

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    if not arduino_connected: return jsonify(success=False, error="Arduino Disconnected (Check USB)"), 500
    d = request.json; g = d.get("gcode"); f = d.get("filename")
    if not current_gcode_runner or not current_gcode_runner.is_alive():
        start_job({'gcode': g, 'filename': f}); return jsonify(success=True, message=f"Started {f}")
    else:
        job_queue.append({'gcode': g, 'filename': f}); return jsonify(success=True, message="Queued")

@app.route("/delete_design", methods=["POST"])
def delete_design():
    f = request.json.get("filename"); p = os.path.join(DESIGNS_FOLDER, f)
    if os.path.exists(p): os.remove(p); os.remove(p.replace('.txt','.png')); return jsonify(success=True)
    return jsonify(success=False)

@app.route("/save_design", methods=["POST"])
def save_design():
    f = request.json.get("filename"); g = request.json.get("gcode")
    with open(os.path.join(DESIGNS_FOLDER, f), "w") as file: file.write(g)
    return jsonify(success=True)

@app.route("/send", methods=["POST"])
def send_command():
    global is_paused
    cmd = request.json.get("command")
    if cmd == "CLEAR":
        global is_looping, loop_playlist; is_looping = False; loop_playlist = []; job_queue.clear()
        if current_gcode_runner: current_gcode_runner.is_running = False
        if arduino_connected: lock.acquire(); arduino.write(b"CLEAR\n"); lock.release()
        return jsonify(success=True)
    if cmd == "PAUSE": is_paused = True
    elif cmd == "RESUME": is_paused = False
    elif cmd.startswith("LED:") or cmd in ["POWER:ON", "POWER:OFF"]:
        # Use Serial Sender
        if cmd.startswith("LED:"):
            parts = cmd.split(":")[1].split(",")
            r, g, b, br = map(int, parts)
            send_led_persistent(r, g, b)
        elif cmd == "POWER:OFF":
            send_led_persistent(0, 0, 0)
        elif cmd == "POWER:ON":
            send_led_persistent(255, 255, 255)
        return jsonify(success=True)
    
    if arduino_connected: 
        log_message(f"TX (Raw): {cmd}")
        lock.acquire(); arduino.write((cmd+"\n").encode()); lock.release(); return jsonify(success=True)
    return jsonify(success=False)

@app.route("/wifi_setup")
def wifi_setup_page(): return render_template("wifi_setup.html", networks=wifi_tools.get_wifi_networks(), saved_networks=wifi_tools.get_saved_networks())
@app.route("/api/forget_wifi", methods=["POST"])
def forget_wifi_route(): success, msg = wifi_tools.forget_network(request.json.get("ssid")); return jsonify(success=success, message=msg)
@app.route("/check_password", methods=["POST"])
def check_password_route(): return jsonify(success=(request.json.get("password") == "2025"))

# --- PAGES ---
@app.route("/terminal")
def terminal(): return render_template("terminal.html")
@app.route("/AI_builder")
def ai_builder(): return render_template("AI_builder.html")
@app.route("/led_controls")
def led_controls(): return render_template("led_controls.html")
@app.route("/sketch")
def sketch_page(): return render_template("sketch.html")
@app.route("/designs")
def designs(): return render_template("designs.html")
@app.route('/designs/<path:filename>')
def serve_design_file(filename): return send_from_directory(DESIGNS_FOLDER, filename)
@app.route('/api/designs')
def list_designs():
    try: return jsonify([f for f in os.listdir(DESIGNS_FOLDER) if f.endswith('.txt')])
    except: return jsonify([])
@app.route("/terminal/logs")
def get_logs():
    with lock: return jsonify(list(serial_log))

@app.route("/restart_app", methods=["POST"])
def restart_app():
    log_message("Restarting Application...")
    def restart():
        time.sleep(1)
        # Simply exit, the run_stepper.sh script loop will restart it
        os._exit(0) 
    threading.Thread(target=restart).start()
    return jsonify(success=True, message="Application is restarting...")

if __name__ == "__main__":
    # 1. Determine Port First (5000 -> 6000)
    SERVER_PORT = find_available_port()
    
    # 2. Connect Hardware & Scheduler
    connect_arduino() 
    SchedulerThread().start()
    
    # 3. Auto-Start Tunnel (Background Thread)
    # This runs in background to avoid blocking server start
    threading.Thread(target=auto_start_ngrok_thread, daemon=True).start()
    
    # 4. Start PRODUCTION Server (8 threads)
    print(f"Starting PRODUCTION server on port {SERVER_PORT}...")
    
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=SERVER_PORT, threads=8)
    except ImportError:
        print("Waitress not found. Falling back to Flask Dev Server...")
        app.run(host="0.0.0.0", port=SERVER_PORT, debug=True, use_reloader=False)
