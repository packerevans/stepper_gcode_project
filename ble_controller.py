import asyncio
import threading
from bleak import BleakClient
from typing import Optional

# --- Configuration ---
ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# --- State Management ---
client: Optional[BleakClient] = None
command_lock = asyncio.Lock()
loop = asyncio.new_event_loop()

# --- Helper: Byte Generation ---
def create_rgb_command(r: int, g: int, b: int, brightness: int) -> bytearray:
    r = min(255, max(0, r))
    g = min(255, max(0, g))
    b = min(255, max(0, b))
    brightness = min(16, max(1, brightness)) 
    
    return bytearray([
        0x7e, 0x07, 0x05, 0x03,
        r, g, b,
        brightness,
        0xef
    ])

COMMANDS = {
    "POWER:ON": bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef]), 
    "POWER:OFF": bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef]), 
}

# --- Connection Logic ---

def is_connected() -> bool:
    """Checks if the client exists and is actually connected."""
    global client
    return client is not None and client.is_connected

async def ensure_connection():
    """
    Tries to connect. If already connected, returns True immediately.
    If not, tries to connect. Returns True/False based on success.
    """
    global client
    if is_connected():
        return True

    print(f"BLE: Connecting to {ADDRESS}...")
    try:
        # Create new client instance
        client = BleakClient(ADDRESS, timeout=5.0) # 5s timeout prevents hanging
        await client.connect()
        print(f"BLE: Connected!")
        return True
    except Exception as e:
        print(f"BLE: Connection Failed - {e}")
        client = None
        return False

async def disconnect_client():
    global client
    if client:
        try:
            await client.disconnect()
            print("BLE: Disconnected.")
        except Exception as e:
            print(f"BLE: Error during disconnect - {e}")
        finally:
            client = None

# --- Command Sending ---

async def send_raw_command(data: bytearray, name: str):
    """
    The core logic: Connect -> Send -> Handle Errors.
    """
    # 1. Ensure Lock (prevents commands crashing into each other)
    async with command_lock:
        # 2. Ensure Connection
        if not await ensure_connection():
            print(f"BLE: Could not send '{name}' - Device not reachable.")
            return False

        # 3. Write Data
        try:
            await client.write_gatt_char(WRITE_UUID, data)
            print(f"BLE: Sent {name}")
            return True
        except Exception as e:
            print(f"BLE: Write Failed ({name}) - {e}")
            # If write fails, assume connection is dead
            await disconnect_client() 
            return False

# --- Public Handlers (Called by Flask) ---

async def handle_command(cmd: str):
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
# We do NOT run a permanent while True loop anymore.
# We only keep the Event Loop alive to process requests as they come in.

def start_ble_loop():
    """Starts the asyncio loop in a background thread."""
    def run_loop(loop_ref):
        asyncio.set_event_loop(loop_ref)
        loop_ref.run_forever()

    t = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    t.start()
    print("BLE: Background thread running.")

# Start immediately
start_ble_loop()
