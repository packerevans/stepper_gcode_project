from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess
import os
import re
import asyncio
import socket
import json
import datetime
from collections import deque
import wifi_tools 
import ble_controller 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

# === CONFIGURATION ===
DESIGNS_FOLDER = os.path.join(app.root_path, 'templates', 'designs')
SCHEDULE_FILE = os.path.join(app.root_path, 'schedules.json')

if not os.path.exists(DESIGNS_FOLDER):
    try: os.makedirs(DESIGNS_FOLDER)
    except: pass

@app.after_request
def apply_ngrok_header(response: Response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# === STATE MANAGEMENT ===
job_queue = deque()      
loop_playlist = []       
is_looping = False       
is_paused = False        
is_waiting = False       

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

ble_controller.set_logger(log_message)

# === SERIAL CONNECTION SETUP ===
arduino = None
arduino_connected = False
current_gcode_runner = None

try:
    arduino = serial.Serial('/dev/ttyACM0', 115200, timeout=0.1) 
    time.sleep(2) 
    arduino_connected = True
except Exception as e:
    print(f"WARNING: Arduino not connected: {e}") 
    log_message(f"Arduino Init Failed: {e}")

# === SCHEDULER SYSTEM ===
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
            current_time = now.strftime("%H:%M") # "22:00"
            current_day = now.strftime("%a")     # "Mon", "Tue"
            
            # Run only once per minute
            if self.last_minute_checked != current_time:
                self.last_minute_checked = current_time
                self.check_triggers(current_time, current_day)
            
            time.sleep(5) # Check every 5 seconds to be snappy

    def check_triggers(self, time_str, day_str):
        schedules = load_schedules()
        for item in schedules:
            if item['time'] == time_str and day_str in item['days']:
                self.execute_action(item)

    def execute_action(self, item):
        global is_looping, loop_playlist
        action = item['type']
        val = item.get('value')
        
        log_message(f"Scheduler Trigger: {action}")

        # --- LED ACTIONS ---
        if action == "led_off":
            asyncio.run_coroutine_threadsafe(ble_controller.handle_command("POWER:OFF"), ble_controller.loop)
        
        elif action == "led_color" and val:
            try:
                r, g, b = hex_to_rgb(val)
                # Brightness default 16 (Max)
                asyncio.run_coroutine_threadsafe(ble_controller.send_led_command(r, g, b, 16), ble_controller.loop)
            except: pass

        # --- SAND ACTIONS ---
        elif action == "stop_sand":
             # Similar to CLEAR command
            global current_gcode_runner
            if current_gcode_runner and current_gcode_runner.is_alive():
                current_gcode_runner.is_running = False 
                current_gcode_runner.slot_available_event.set()
            job_queue.clear()
            loop_playlist = []
            is_looping = False
            if arduino_connected:
                with lock: arduino.write(b"CLEAR\n")

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
                # If busy, add to queue. If not, start.
                if current_gcode_runner is None or not current_gcode_runner.is_alive():
                    start_job({'gcode': gcode, 'filename': val})
                else:
                    job_queue.append({'gcode': gcode, 'filename': val})

# Start Scheduler
SchedulerThread().start()


# === G-CODE RUNNER (Unchanged logic, just compacted for context) ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block, filename, on_complete=None):
        super().__init__(daemon=True)
        self.lines = [l.split(';')[0].strip() for l in gcode_block.split('\n') if l.split(';')[0].strip().upper().startswith('G1')]
        self.total_lines = len(self.lines)
        self.filename = filename
        self.is_running = True
        self.on_complete = on_complete
        self.ARDUINO_BUFFER_SIZE = 25  
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
        if line.strip().upper() == "DONE":
            with lock:
                if self.credits < self.ARDUINO_BUFFER_SIZE: self.credits += 1
                self.slot_available_event.set() 

    def send_line(self, line):
        try:
            with lock: arduino.write((line + "\n").encode())
            self.lines_sent += 1
            self.credits -= 1 
            return True
        except:
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner, current_job_name, is_paused
        current_gcode_runner = self
        current_job_name = self.filename
        is_paused = False 
        
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

# === JOB MANAGER ===
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
            if arduino_connected:
                with lock: arduino.write(b"PAUSE\n")
            for _ in range(30): 
                if not is_looping and len(job_queue) == 0: break
                time.sleep(1)
            is_waiting = False
        start_job(next_job)
    else:
        current_job_name = None
        if arduino_connected:
            with lock: arduino.write(b"PAUSE\n")

def start_job(job_data):
    if not arduino_connected: return
    with lock: arduino.write(b"RESUME\n") 
    GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=on_job_finished).start()

# === SERIAL READER ===
def read_from_serial():
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    if "ERROR" in line: log_message(f"Ard: {line}")
                    if current_gcode_runner: current_gcode_runner.process_incoming_serial(line)
            else: time.sleep(0.01) 
        except: time.sleep(1)

if arduino_connected: threading.Thread(target=read_from_serial, daemon=True).start()

# === UTILITY ===
def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.254.254.254', 1)) 
        ip = s.getsockname()[0]; s.close()
    except: ip = '127.0.0.1'
    return ip

# === FLASK ROUTES ===

@app.route("/")
def index():
    if get_current_ip() in ["10.42.0.1", "192.168.4.1"]: return redirect(url_for('wifi_setup_page'))
    return render_template("designs.html")

@app.route("/settings")
def settings_page():
    return render_template("settings.html")

# --- SCHEDULE API ---
@app.route("/api/schedule", methods=["GET", "POST", "DELETE"])
def api_schedule():
    if request.method == "GET":
        return jsonify(load_schedules())
    
    if request.method == "POST":
        data = request.json
        schedules = load_schedules()
        schedules.append(data)
        save_schedules(schedules)
        return jsonify(success=True)

    if request.method == "DELETE":
        idx = request.json.get('index')
        schedules = load_schedules()
        if 0 <= idx < len(schedules):
            del schedules[idx]
            save_schedules(schedules)
            return jsonify(success=True)
        return jsonify(success=False)

@app.route("/status_full", methods=["GET"])
def status_full():
    queue_list = []
    if len(job_queue) > 0:
        for i, job in enumerate(job_queue):
            queue_list.append({"index": i, "name": job['filename'].replace('.txt', ''), "type": "queue"})
    elif is_looping:
        limit = min(len(loop_playlist), 10)
        for i in range(limit):
             queue_list.append({"index": i, "name": loop_playlist[i].replace('.txt', ''), "type": "loop"})

    return jsonify({
        "playing": current_job_name.replace('.txt', '') if current_job_name else None,
        "queue_count": len(job_queue),
        "queue_items": queue_list,
        "next_up": queue_list[0]["name"] if queue_list else "None",
        "is_looping": is_looping,
        "is_paused": is_paused,   
        "is_waiting": is_waiting  
    })

@app.route("/remove_from_queue", methods=["POST"])
def remove_from_queue():
    global job_queue, loop_playlist
    data = request.json
    index = int(data.get("index"))
    q_type = data.get("type")
    try:
        if q_type == "queue":
            del job_queue[index]
        elif q_type == "loop":
            del loop_playlist[index]
            if not loop_playlist: cancel_loop()
        return jsonify(success=True)
    except: return jsonify(success=False)

@app.route("/set_loop", methods=["POST"])
def set_loop():
    global is_looping, loop_playlist
    files = request.json.get("files", [])
    if not files: return jsonify(success=False)
    loop_playlist = files
    is_looping = True
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        process_queue(wait_enabled=False)
    return jsonify(success=True)

@app.route("/cancel_loop", methods=["POST"])
def cancel_loop():
    global is_looping, loop_playlist
    is_looping = False
    loop_playlist = []
    return jsonify(success=True)

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode = data.get("gcode")
    filename = data.get("filename", "Unknown")
    if not gcode: return jsonify(success=False, error="No G-code"), 400
    
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        start_job({'gcode': gcode, 'filename': filename})
    else:
        job_queue.append({'gcode': gcode, 'filename': filename})
    return jsonify(success=True)

@app.route("/delete_design", methods=["POST"])
def delete_design():
    filename = request.json.get("filename")
    path_txt = os.path.join(DESIGNS_FOLDER, filename)
    path_png = os.path.join(DESIGNS_FOLDER, filename.replace('.txt','.png'))
    try:
        if os.path.exists(path_txt): os.remove(path_txt)
        if os.path.exists(path_png): os.remove(path_png)
        return jsonify(success=True)
    except Exception as e: return jsonify(success=False, error=str(e))

@app.route("/send", methods=["POST"])
def send_command():
    global is_paused, current_gcode_runner, is_looping, loop_playlist
    cmd = request.json.get("command")
    
    if cmd == "CLEAR":
        if current_gcode_runner and current_gcode_runner.is_alive():
            current_gcode_runner.is_running = False 
            current_gcode_runner.slot_available_event.set()
        job_queue.clear()
        loop_playlist = []
        is_looping = False
        if arduino_connected:
            with lock: arduino.write(b"CLEAR\n")
        return jsonify(success=True)

    if cmd == "PAUSE": is_paused = True
    elif cmd == "RESUME": is_paused = False

    if cmd.startswith("LED:") or cmd in ["CONNECT", "DISCONNECT", "POWER:ON", "POWER:OFF"]:
        if cmd.startswith("LED:"):
            parts = cmd.split(":")[1].split(",")
            r, g, b, br = map(int, parts)
            asyncio.run_coroutine_threadsafe(ble_controller.send_led_command(r, g, b, br), ble_controller.loop)
        else:
            asyncio.run_coroutine_threadsafe(ble_controller.handle_command(cmd), ble_controller.loop)
        return jsonify(success=True)

    if arduino_connected:
        with lock: arduino.write((cmd + "\n").encode())
        return jsonify(success=True)
    return jsonify(success=False)

@app.route("/wifi_setup")
def wifi_setup_page():
    return render_template("wifi_setup.html", networks=wifi_tools.get_wifi_networks(), saved_networks=wifi_tools.get_saved_networks())

@app.route("/api/forget_wifi", methods=["POST"])
def forget_wifi_route():
    success, msg = wifi_tools.forget_network(request.json.get("ssid"))
    return jsonify(success=success, message=msg)

@app.route("/check_password", methods=["POST"])
def check_password_route(): return jsonify(success=(request.json.get("password") == "2025"))

@app.route("/shutdown", methods=["POST"])
def shutdown(): subprocess.Popen(["sudo", "shutdown", "now"]); return jsonify(success=True)

@app.route("/reboot", methods=["POST"])
def reboot(): subprocess.Popen(["sudo", "reboot"]); return jsonify(success=True)

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    # Placeholder for firmware update logic
    return jsonify(success=True)

@app.route("/controls")
def controls(): return render_template("index.html")
@app.route("/terminal")
def terminal(): return render_template("terminal.html")
@app.route("/led_controls")
def led_controls(): return render_template("led_controls.html")
@app.route("/AI_builder")
def AI_builder(): return render_template("AI_builder.html")
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
