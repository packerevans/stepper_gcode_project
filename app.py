from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, redirect
import serial, threading, time, subprocess
import os
import re
import asyncio
import socket
from collections import deque
import wifi_tools 
import ble_controller 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

DESIGNS_FOLDER = os.path.join(app.root_path, 'templates', 'designs')
if not os.path.exists(DESIGNS_FOLDER): os.makedirs(DESIGNS_FOLDER)

# === QUEUE & STATE ===
job_queue = deque() 
lock = threading.Lock()
serial_log = []

# === LOGGING ===
def log_message(msg):
    timestamp = time.strftime("[%H:%M:%S] ")
    with lock:
        serial_log.append(timestamp + msg)
        if len(serial_log) > 200: serial_log.pop(0)

ble_controller.set_logger(log_message)

# === SERIAL SETUP ===
arduino = None
arduino_connected = False
current_gcode_runner = None

try:
    arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=0.1) 
    time.sleep(2) 
    arduino_connected = True
except Exception as e:
    print(f"WARNING: Arduino not connected: {e}") 

# === G-CODE RUNNER ===
class GCodeRunner(threading.Thread):
    def __init__(self, gcode_block, filename, on_complete=None):
        super().__init__(daemon=True)
        self.lines = [l.split(';')[0].strip() for l in gcode_block.split('\n') 
                      if l.split(';')[0].strip().upper().startswith('G1')]
        
        self.total_lines = len(self.lines)
        self.filename = filename
        self.is_running = True
        self.on_complete = on_complete
        
        self.ARDUINO_BUFFER_SIZE = 40  
        self.credits = self.ARDUINO_BUFFER_SIZE 
        self.lines_sent = 0
        self.slot_available_event = threading.Event()

    def process_incoming_serial(self, line):
        if line.strip().upper() == "DONE":
            with lock:
                self.credits += 1
                self.slot_available_event.set()

    def send_line(self, line):
        try:
            with lock: arduino.write((line + "\n").encode())
            self.lines_sent += 1
            self.credits -= 1
            return True
        except Exception as e:
            log_message(f"SERIAL ERROR: {e}")
            self.is_running = False
            return False

    def run(self):
        global current_gcode_runner
        current_gcode_runner = self
        log_message(f"Starting: {self.filename} ({self.total_lines} steps)")

        while self.is_running and self.lines_sent < self.total_lines:
            if self.credits <= 0:
                self.slot_available_event.clear()
                if not self.slot_available_event.wait(timeout=10.0):
                    log_message("TIMEOUT: Arduino unresponsive.")
                    break
            
            if self.is_running:
                if not self.send_line(self.lines[self.lines_sent]): break
                time.sleep(0.002) 

        if self.is_running:
            log_message("Design finished. Sending PAUSE.")
            with lock: arduino.write(b"PAUSE\n")
        
        current_gcode_runner = None
        if self.on_complete: self.on_complete()

# === JOB MANAGER ===
def process_queue():
    global current_gcode_runner
    if current_gcode_runner and current_gcode_runner.is_alive(): return
    if len(job_queue) > 0:
        next_job = job_queue.popleft()
        start_job(next_job)
    else:
        log_message("Queue empty. Standing by.")

def start_job(job_data):
    if not arduino_connected: return
    with lock:
        arduino.write(b"RESUME\n")
        time.sleep(0.1)
        arduino.write(b"G0\n")
        time.sleep(0.1)
    
    runner = GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=process_queue)
    runner.start()

# === SERIAL READER ===
def read_from_serial():
    while arduino_connected:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode(errors="ignore").strip()
                if line:
                    if current_gcode_runner:
                        current_gcode_runner.process_incoming_serial(line)
            else:
                time.sleep(0.01) 
        except Exception: time.sleep(1)

if arduino_connected:
    threading.Thread(target=read_from_serial, daemon=True).start()

# === FLASK ROUTES ===

@app.route("/send_gcode_block", methods=["POST"])
def send_gcode_block_route():
    data = request.json
    gcode = data.get("gcode")
    filename = data.get("filename", "Unknown")

    if not gcode: return jsonify(success=False, error="No G-code"), 400
    if not arduino_connected: return jsonify(success=False, error="No Arduino"), 500

    job_queue.append({'gcode': gcode, 'filename': filename})
    
    if current_gcode_runner is None or not current_gcode_runner.is_alive():
        process_queue()
        return jsonify(success=True, message=f"Started {filename}")
    else:
        return jsonify(success=True, message=f"Added {filename} to Queue (Pos: {len(job_queue)})")

@app.route("/delete_design", methods=["POST"])
def delete_design():
    """Deletes a design file from the server."""
    data = request.json
    filename = data.get("filename")
    if not filename: return jsonify(success=False, error="No filename provided")
    
    # Sanitize slightly to prevent directory traversal
    clean_name = os.path.basename(filename)
    path_txt = os.path.join(DESIGNS_FOLDER, clean_name)
    
    try:
        if os.path.exists(path_txt):
            os.remove(path_txt)
            log_message(f"Deleted file: {clean_name}")
            return jsonify(success=True, message=f"Deleted {clean_name}")
        else:
            return jsonify(success=False, error="File not found")
    except Exception as e:
        log_message(f"DELETE ERROR: {e}")
        return jsonify(success=False, error=str(e))

@app.route("/queue_count", methods=["GET"])
def get_queue_count():
    return jsonify(count=len(job_queue))

@app.route("/send", methods=["POST"])
def send_command():
    data = request.json
    cmd = data.get("command")
    
    if cmd == "CLEAR":
        global current_gcode_runner
        if current_gcode_runner and current_gcode_runner.is_alive():
            current_gcode_runner.is_running = False 
            current_gcode_runner.slot_available_event.set()
        job_queue.clear()
        if arduino_connected:
            with lock: arduino.write(b"CLEAR\n")
        log_message("Queue and current job cleared.")
        return jsonify(success=True)

    if arduino_connected:
        with lock: arduino.write((cmd + "\n").encode())
        return jsonify(success=True)
    return jsonify(success=False, error="Not connected")

@app.route("/")
def index(): return render_template("designs.html")

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
