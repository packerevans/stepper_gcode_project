import asyncio
import threading
from bleak import BleakClient, BleakScanner
from typing import Optional, Callable

# --- Configuration ---
ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# --- State Management ---
client: Optional[BleakClient] = None
command_lock: Optional[asyncio.Lock] = None 
loop = asyncio.new_event_loop()

# GLOBAL STATUS FLAG
# We use this boolean because accessing 'client.is_connected' from 
# the Flask thread can be flaky. This variable is the source of truth.
IS_CONNECTED_FLAG = False

# Logger callback placeholder
log_callback: Optional[Callable[[str], None]] = None

def log(msg: str):
    print(f"[BLE] {msg}") 
    if log_callback:
        log_callback(f"[BLE] {msg}")

def set_logger(callback):
    global log_callback
    log_callback = callback

# --- Helper: Byte Generation ---
def create_rgb_command(r: int, g: int, b: int, brightness: int) -> bytearray:
    r = min(255, max(0, r))
    g = min(255, max(0, g))
    b = min(255, max(0, b))
    brightness = min(16, max(1, brightness)) 
    return bytearray([0x7e, 0x07, 0x05, 0x03, r, g, b, brightness, 0xef])

COMMANDS = {
    "POWER:ON": bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef]), 
    "POWER:OFF": bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef]), 
}

# --- Connection Logic ---

def is_connected() -> bool:
    """Returns the global flag. Safe to call from any thread."""
    return IS_CONNECTED_FLAG

async def ensure_connection():
    global client, IS_CONNECTED_FLAG
    
    # 1. If flag is true, we are good.
    if IS_CONNECTED_FLAG:
        # Double check the actual client just in case
        if client and client.is_connected:
            return True
        else:
            IS_CONNECTED_FLAG = False # Mismatch detected, reset

    # 2. If we have a client object but are disconnected, try DIRECT reconnect.
    # DO NOT SCAN yet. If the device is connected to us but silent, scanning fails.
    if client:
        log("Existing client found. Attempting direct reconnect...")
        try:
            await client.connect()
            IS_CONNECTED_FLAG = True
            log("Reconnected successfully!")
            return True
        except Exception as e:
            log(f"Direct reconnect failed ({e}). Resetting...")
            client = None # Now we are truly dead, so we can scan.

    # 3. Full Scan and Connect (Only if we have no client object)
    log(f"Scanning for {ADDRESS}...")
    try:
        device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
        
        if not device:
            log(f"Device not found. Ensure it is powered on and NOT connected to another phone.")
            return False

        log("Device found! Connecting...")
        client = BleakClient(device, timeout=15.0) 
        
        # Set disconnected callback to auto-update flag
        def on_disconnect(c):
            global IS_CONNECTED_FLAG
            IS_CONNECTED_FLAG = False
            log("Device disconnected unexpectedly.")

        client.set_disconnected_callback(on_disconnect)

        await client.connect()
        IS_CONNECTED_FLAG = True
        log("Connected successfully!")
        return True
    except Exception as e:
        log(f"Connection Failed: {e}")
        IS_CONNECTED_FLAG = False
        client = None
        return False

async def disconnect_client():
    global client, IS_CONNECTED_FLAG
    if client:
        try:
            log("Disconnecting...")
            await client.disconnect()
            log("Disconnected.")
        except Exception as e:
            log(f"Error during disconnect: {e}")
        finally:
            client = None
            IS_CONNECTED_FLAG = False

# --- Command Sending ---

async def send_raw_command(data: bytearray, name: str):
    global command_lock, IS_CONNECTED_FLAG
    
    if command_lock is None:
        log("System starting up...")
        return False

    async with command_lock:
        # Ensure connection inside the lock to prevent race conditions
        if not await ensure_connection():
            log(f"Cannot send '{name}' - Device unreachable.")
            return False

        try:
            await client.write_gatt_char(WRITE_UUID, data)
            log(f"Sent: {name}")
            return True
        except Exception as e:
            log(f"Write Failed ({name}): {e}")
            IS_CONNECTED_FLAG = False # Force flag update immediately
            # We don't kill the client here, we let the next ensure_connection try to reconnect it
            return False

# --- Public Handlers ---

async def handle_command(cmd: str):
    log(f"Processing: {cmd}")
    if cmd == "CONNECT":
        await ensure_connection()
    elif cmd == "DISCONNECT":
        await disconnect_client()
    elif cmd in COMMANDS:
        await send_raw_command(COMMANDS[cmd], cmd)

async def send_led_command(r: int, g: int, b: int, brightness: int):
    data = create_rgb_command(r, g, b, brightness)
    await send_raw_command(data, f"RGB({r},{g},{b})")

# --- Background Loop ---

def start_ble_loop():
    def run_loop(loop_ref):
        asyncio.set_event_loop(loop_ref)
        global command_lock
        command_lock = asyncio.Lock()
        loop_ref.run_forever()

    t = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    t.start()
    print("BLE: Background thread running.")

start_ble_loop()
