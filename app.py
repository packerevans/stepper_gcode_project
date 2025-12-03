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

# === STATE MANAGEMENT ===
job_queue = deque()      # High Priority (Manual adds)
loop_playlist = []       # Low Priority (Looping files)
is_looping = False       # Flag

current_job_name = None  # What is currently running
next_job_name = None     # What is coming up next

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
        global current_gcode_runner, current_job_name
        current_gcode_runner = self
        current_job_name = self.filename
        
        log_message(f"Starting: {self.filename}")

        while self.is_running and self.lines_sent < self.total_lines:
            if self.credits <= 0:
                self.slot_available_event.clear()
                if not self.slot_available_event.wait(timeout=10.0):
                    log_message("TIMEOUT: Arduino unresponsive.")
                    break
            
            if self.is_running:
                if not self.send_line(self.lines[self.lines_sent]): break
                time.sleep(0.002) 

        # End of job logic
        current_job_name = None
        current_gcode_runner = None
        
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
        
        # We perform the sleep in a separate thread so we don't block Flask
        # But here we are likely inside the previous GCodeRunner thread, which is fine to block.
        # However, to be safe and allow 'High Priority' inserts during wait, we should ideally sleep in chunks.
        
        for _ in range(60): 
            # Every second, check if the user cancelled everything
            if not is_looping and len(job_queue) == 0 and current_job_name is None:
                # Loop was cancelled during wait?
                pass 
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
    
    # --- NO G0 HERE (User Requested Removal) ---
    # Just ensure we are active
    with lock:
        arduino.write(b"RESUME\n") 
    
    runner = GCodeRunner(job_data['gcode'], job_data['filename'], on_complete=process_queue)
    runner.start()

# === FLASK ROUTES ===

@app.route("/status_full", methods=["GET"])
def status_full():
    """Returns detailed status for the UI"""
    
    # Determine 'Next' Item
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

    if not gcode: return jsonify(success=False, error="No G-code"), 400
    
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
    if not filename: return jsonify(success=False)
    
    clean_name = os.path.basename(filename)
    path_txt = os.path.join(DESIGNS_FOLDER, clean_name)
    path_png = os.path.join(DESIGNS_FOLDER, clean_name.replace('.txt','.png'))
    
    try:
        if os.path.exists(path_txt):
            os.remove(path_txt)
            if os.path.exists(path_png): os.remove(path_png)
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
        if current_gcode_runner and current_gcode_runner.is_alive():
            current_gcode_runner.is_running = False 
            current_gcode_runner.slot_available_event.set()
        job_queue.clear()
        loop_playlist = [] # Clear loop on clear all
        is_looping = False
        
        if arduino_connected:
            with lock: arduino.write(b"CLEAR\n")
        return jsonify(success=True)

    if arduino_connected:
        with lock: arduino.write((cmd + "\n").encode())
        return jsonify(success=True)
    return jsonify(success=False)

# ... Standard Routes ...
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
