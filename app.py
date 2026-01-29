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
from pyngrok import ngrok, conf 

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 

# === CONFIGURATION ===
BASE_DIR = app.root_path
DESIGNS_FOLDER = os.path.join(BASE_DIR, 'templates', 'designs')
SCHEDULE_FILE = os.path.join(BASE_DIR, 'schedules.json')
ARDUINO_PROJECT_PATH = os.path.join(BASE_DIR, 'Sand') 

# === AUTO-PORT SELECTION ===
SERVER_PORT = 5000 

def find_available_port(start_port=5000):
    port = start_port
    while port < 5100: 
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1
    return 5000 

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

# === SERIAL CONNECTION ===
arduino = None
arduino_connected = False
current_gcode_runner = None

def connect_arduino():
    global arduino, arduino_connected
    try:
        # --- CRITICAL FIX: LGT Nano usually is USB0 and 250000 Baud ---
        # If this fails, try '/dev/ttyACM0' 
        arduino = serial.Serial('/dev/ttyUSB0', 250000, timeout=0.1) 
        time.sleep(2) 
        arduino_connected = True
        threading.Thread(target=read_from_serial, daemon=True).start()
        log_message("Arduino Connected: /dev/ttyUSB0 @ 250000")
    except Exception as e:
        arduino_connected = False
        print(f"WARNING: Arduino not connected: {e}") 
        log_message(f"Arduino Init Failed: {e}")

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
        global is_looping, loop_playlist
        action = item['type']
        val = item.get('value')
        log_message(f"Scheduler Trigger: {action}")

        if action == "led_off":
            asyncio.run_coroutine_threadsafe(ble_controller.handle_command("POWER:OFF"), ble_controller.loop)
        elif action == "led_color" and val:
            try:
                r, g, b = hex_to_rgb(val)
                asyncio.run_coroutine_threadsafe(ble_controller.send_led_command(r, g, b, 16), ble_controller.loop)
            except: pass
        elif action == "stop_sand":
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
                if current_gcode_runner is None or not current_gcode_runner.is_alive():
                    start_job({'gcode': gcode, 'filename': val})
                else:
                    job_queue.append({'gcode': gcode, 'filename': val})

# === G-CODE RUNNER ===
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
        self.lines
