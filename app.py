from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess
import os
import re
import asyncio
import socket
import cv2                # Machine Vision
import numpy as np        # Math/Arrays
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
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1) 
    time.sleep(2) 
    arduino_connected = True
except Exception as e:
    print(f"WARNING: Arduino not connected: {e}") 
    log_message(f"Arduino Init Failed: {e}")

# === G-CODE RUNNER ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block, filename, on_complete=None):
        super().__init__(daemon=True)
        self.lines = [
            line.split(';')[0].strip() for line in gcode_block.split('\n') 
            if line.split(';')[0].strip().upper().startswith('G1')
        ]
        self.total_lines = len(self.lines)
        self.filename = filename
        self.is_running = True
        self.on_complete = on_complete
        
        self.ARDUINO_BUFFER_SIZE = 40  
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
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
            if self.credits <= 0:
                self.slot_available_event.clear()
                got_slot = self.slot_available_event.wait(timeout=10.0) 
                if not got_slot:
                    log_message("TIMEOUT: Arduino stopped responding.")
                    break
            
            if self.is_running:
                next_line = self.lines[self.lines_sent]
                if not self.send_line(next_line): break
                time.sleep(0.002) 

        current_job_name = None
        current_gcode_runner = None
        log_message("Design Completed.")
        
        if self.on_complete:
            self.on_complete()

# === AI IMAGE PROCESSOR (The New Logic) ===
class SandTableGenerator:
    def __init__(self, table_radius_mm=202.6):
        self.R = table_radius_mm

    def process(self, file_stream, density=1.0):
        # 1. Decode Image from Memory
        file_bytes = np.asarray(bytearray(file_stream.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            raise ValueError("Could not decode image")

        # 2. Resize & Edge Detect
        h, w = img.shape
        new_size = (int(w * density), int(h * density))
        img = cv2.resize(img, new_size)
        
        # Blur slightly to remove noise before edge detection
        img_blur = cv2.GaussianBlur(img, (5, 5), 0)
        edges = cv2.Canny(img_blur, 50, 150)
        
        # Find Contours
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        # 3. Map to Table Coordinates (mm)
        scale = (self.R * 1.8) / max(new_size) 
        cx, cy = new_size[0] / 2, new_size[1] / 2
        mapped_paths = []
        
        for cnt in contours:
            epsilon = 0.005 * cv2.arcLength(cnt, False)
            approx = cv2.approxPolyDP(cnt, epsilon, False)
            
            if len(approx) > 2:
                path = []
                for point in approx:
                    x = (point[0][0] - cx) * scale
                    y = (cy - point[0][1]) * scale 
                    path.append([x, y])
                mapped_paths.append(np.array(path))

        # 4. Greedy Optimization (Sandify Logic)
        if not mapped_paths:
            return []
            
        current_pos = np.array([self.R, 0.0]) 
        ordered_points = [{'x': float(self.R), 'y': 0.0, 'type': 'point'}]
        pool = mapped_paths.copy()

        while pool:
            best_idx = -1
            best_dist = float('inf')
            reverse_contour = False

            for i, contour in enumerate(pool):
                d_start = np.linalg.norm(contour[0] - current_pos)
                d_end = np.linalg.norm(contour[-1] - current_pos)

                if d_start < best_dist:
                    best_dist = d_start
                    best_idx = i
                    reverse_contour = False
                if d_end < best_dist:
                    best_dist = d_end
                    best_idx = i
                    reverse_contour = True

            chosen = pool.pop(best_idx)
            if reverse_contour:
                chosen = chosen[::-1]

            for p in chosen:
                ordered_points.append({'x': float(p[0]), 'y': float(p[1]), 'type': 'point'})
            
            current_pos = chosen[-1]

        ordered_points.append({'x': float(self.R), 'y': 0.0, 'type': 'point'})
        return ordered_points

# === JOB MANAGER ===
def on_job_finished():
    process_queue(wait_enabled=True)

def process_queue(wait_enabled=True):
    global current_job_name, is_looping

    if current_gcode_runner and current_gcode_runner.is_alive():
        return

    next_job = None
    
    # 1. Check High Priority Queue
    if len(job_queue) > 0:
        next_job = job_queue.popleft()
    
    # 2. Check Loop (if Queue empty)
    elif is_looping and len(loop_playlist) > 0:
        next_file = loop_playlist.pop(0)
        loop_playlist.append(next_file) # Rotate to end
        try:
            path = os.path.join(DESIGNS_FOLDER, next_file)
            with open(path, 'r') as f: gcode = f.read()
            next_job = {'gcode': gcode, 'filename': next_file}
        except Exception as e:
            log_message(f"Loop Load Error: {e}")
            process_queue(wait_enabled=False) 
            return

    if next_job:
        if wait_enabled:
            log_message("Design complete. Pausing motors for 60s...")
            if arduino_connected:
                with lock: arduino.write(b"PAUSE\n")
            
            for _ in range(60): 
                if not is_looping and len(job_queue) == 0:
                    break
                time.sleep(1)
            
            if arduino_connected:
                with lock: arduino.write(b"RESUME\n")
            
        start_job(next_job)
    else:
        log_message("Queue empty. Standing by.")
        current_job_name = None

def start_job(job_data):
    if not arduino_connected: return
    
    with lock:
        arduino.write(b"RESUME\n") 
    
    runner = GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=on_job_finished)
    runner.start()

# === SERIAL READER ===
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
        except Exception: time.sleep(1)

if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()

# === UTILITY ===
def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('10.254.254.254', 1)) 
        ip = s.getsockname()[0]
        s.close()
    except Exception: ip = '127.0.0.1'
    return ip

# === FLASK ROUTES ===

@app.route("/")
def index():
    if get_current_ip() in ["10.42.0.1", "192.168.4.1"]:
        return redirect(url_for('wifi_setup_page'))
    return render_template("designs.html")

# --- AI TRACER ROUTES (NEW) ---
@app.route("/ai_tracer")
def ai_tracer_page():
    # Serves the new HTML file you created
    return render_template("AI2.html") 

@app.route('/process_image', methods=['POST'])
def process_image_route():
    file = request.files.get('image')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    try:
        gen = SandTableGenerator(table_radius_mm=202.6)
        points = gen.process(file, density=0.5) 
        return jsonify({'success': True, 'points': points})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# --- STATUS & CONTROLS ---
@app.route("/status_full", methods=["GET"])
def status_full():
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
    
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        process_queue(wait_enabled=False)
        
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

    if not gcode: return jsonify(success=False, error="No G-code"), 400
    if not arduino_connected: return jsonify(success=False, error="No Arduino"), 500

    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        start_job({'gcode': gcode, 'filename': filename})
        return jsonify(success=True, message=f"Started {filename}")
    else:
        job_queue.append({'gcode': gcode, 'filename': filename})
        return jsonify(success=True, message=f"Added to Queue")

@app.route("/delete_design", methods=["POST"])
def delete_design():
    data = request.json
    filename = data.get("filename")
    if not filename: return jsonify(success=False)
    
    clean = os.path.basename(filename)
    path_txt = os.path.join(DESIGNS_FOLDER, clean)
    path_png = os.path.join(DESIGNS_FOLDER, clean.replace('.txt','.png'))
    
    try:
        if os.path.exists(path_txt):
            os.remove(path_txt)
            if os.path.exists(path_png): os.remove(path_png)
            return jsonify(success=True)
        else: return jsonify(success=False, error="File not found")
    except Exception as e: return jsonify(success=False, error=str(e))

@app.route("/save_design", methods=["POST"])
def save_design():
    data = request.json
    filename = data.get("filename")
    gcode = data.get("gcode")

    if not filename or not gcode:
        return jsonify(success=False, error="Missing filename or gcode")

    filename = os.path.basename(filename)
    if not filename.lower().endswith(".txt"):
        filename += ".txt"

    file_path = os.path.join(DESIGNS_FOLDER, filename)

    try:
        with open(file_path, "w") as f:
            f.write(gcode)
        log_message(f"Design saved: {filename}")
        return jsonify(success=True)
    except Exception as e:
        log_message(f"Error saving design: {e}")
        return jsonify(success=False, error=str(e))

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    
    if cmd == "CLEAR":
        global current_gcode_runner, is_looping, loop_playlist
        if current_gcode_runner and current_gcode_runner.is_alive():
            current_gcode_runner.is_running = False 
            current_gcode_runner.slot_available_event.set()
        job_queue.clear()
        loop_playlist = []
        is_looping = False
        if arduino_connected:
            with lock: arduino.write(b"CLEAR\n")
        return jsonify(success=True)

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
    return jsonify(success=False, error="Not connected")

# === WIFI & SYSTEM ROUTES ===
@app.route("/wifi_setup")
def wifi_setup_page():
    return render_template("wifi_setup.html", networks=wifi_tools.get_wifi_networks(), saved_networks=wifi_tools.get_saved_networks(), ip_address=get_current_ip(), hostname=socket.gethostname())

@app.route("/api/forget_wifi", methods=["POST"])
def forget_wifi_route():
    success, msg = wifi_tools.forget_network(request.json.get("ssid"))
    return jsonify(success=success, message=msg)

@app.route("/check_password", methods=["POST"])
def check_password_route():
    return jsonify(success=(request.json.get("password") == "2025"))

@app.route("/shutdown", methods=["POST"])
def shutdown():
    subprocess.Popen(["sudo", "shutdown", "now"])
    return jsonify(success=True)

@app.route("/reboot", methods=["POST"])
def reboot():
    subprocess.Popen(["sudo", "reboot"])
    return jsonify(success=True)

@app.route("/pull", methods=["POST"])
def git_pull():
    try:
        r = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=".", check=True, timeout=15)
        return jsonify(success=True, message=r.stdout[:100])
    except Exception as e: return jsonify(success=False, message=str(e))

@app.route("/update_firmware", methods=["POST"])
def update_firmware():
    global arduino, arduino_connected
    if arduino: arduino.close()
    arduino_connected = False
    try:
        subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True)
        subprocess.run(["arduino-cli", "upload", "-p", "/dev/ttyACM0", "--fqbn", "arduino:avr:uno", "/home/pacpi/Desktop/stepper_gcode_project/Sand"], check=True)
    except Exception as e: return jsonify(success=True, message=str(e))
    try:
        time.sleep(2)
        arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1)
        arduino_connected = True
        threading.Thread(target=read_from_serial, daemon=True).start()
    except: pass
    return jsonify(success=True)

# === FRONTEND PAGES ===
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
