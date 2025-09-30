import asyncio
import threading
from bleak import BleakClient
from typing import Optional

# --- Configuration ---
ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# --- State Management ---
client: Optional[BleakClient] = None
is_connected_flag: bool = False
# Lock to ensure only one command is sent at a time
command_lock = asyncio.Lock()
# Global variable to hold the event loop for use in Flask
loop = asyncio.new_event_loop()


# --- Command Creation and Definitions ---

def create_rgb_command(r: int, g: int, b: int, brightness: int) -> bytearray:
    """
    Creates the 9-byte command for setting RGB color with brightness (1-16).
    The brightness value is clamped between 1 and 16 to match device requirements.
    """
    
    # Ensure values are capped (R, G, B: 0-255; Brightness: 1-16)
    r = min(255, max(0, r))
    g = min(255, max(0, g))
    b = min(255, max(0, b))
    brightness = min(16, max(1, brightness))  # CRITICAL: Ensures brightness is in the 1-16 range
    
    return bytearray([
        0x7e, 0x07, 0x05, 0x03,  # Header: Start, Len (7), Function, Channel
        r, g, b,                 # R, G, B (Raw values from sliders)
        brightness,              # Brightness (1 to 16 byte)
        0xef                     # End Byte
    ])

# Predefined commands
COMMANDS = {
    # UPDATED: Use the specific ON command bytearray
    "POWER:ON": bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef]), 
    # Official OFF command
    "POWER:OFF": bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef]), 
}

# --- BLE Client Management Functions ---

async def connect_client(address: str):
    """Connects the client and makes it persistent."""
    global client, is_connected_flag
    if client and client.is_connected:
        is_connected_flag = True
        return True

    try:
        print(f"BLE: Attempting to connect to {address}...")
        client = BleakClient(address)
        await client.connect()
        is_connected_flag = client.is_connected
        print(f"BLE: Connection {'Successful' if is_connected_flag else 'Failed'}")
        return is_connected_flag
    except Exception as e:
        print(f"BLE: Connection Error - {e}")
        is_connected_flag = False
        return False

async def disconnect_client():
    """Disconnects the persistent client."""
    global client, is_connected_flag
    if client and client.is_connected:
        try:
            await client.disconnect()
            print("BLE: Disconnected.")
        except Exception as e:
            print(f"BLE: Disconnect Error - {e}")
    client = None
    is_connected_flag = False

def is_connected() -> bool:
    """Returns the current connection status."""
    global is_connected_flag
    if client and client.is_connected:
        is_connected_flag = True
    return is_connected_flag

# --- Command Execution Functions ---

async def send_command(cmd_bytes: bytearray, cmd_name: str):
    """A helper function to send byte commands over BLE, handles reconnection."""
    global is_connected_flag
    if not is_connected_flag:
        if not await connect_client(ADDRESS):
             print("BLE: Reconnect failed. Command aborted.")
             return

    async with command_lock:
        try:
            await client.write_gatt_char(WRITE_UUID, cmd_bytes)
            print(f"BLE: Sent {cmd_name} → {list(cmd_bytes)}")
        except Exception as e:
            print(f"BLE: Error sending command {cmd_name} - {e}")
            is_connected_flag = False

async def handle_command(cmd: str):
    """Handles simple commands like CONNECT, DISCONNECT, POWER:ON/OFF."""
    if cmd == "CONNECT":
        await connect_client(ADDRESS)
    elif cmd == "DISCONNECT":
        await disconnect_client()
    elif cmd in COMMANDS:
        await send_command(COMMANDS[cmd], cmd)

async def send_led_command(r: int, g: int, b: int, brightness: int):
    """Creates and sends an RGB command."""
    if not is_connected():
        if not await connect_client(ADDRESS):
            return

    cmd_bytes = create_rgb_command(r, g, b, brightness)
    await send_command(cmd_bytes, f"RGB({r},{g},{b}) Br:{brightness}")

# --- Initialization and Background Loop ---

async def ble_runner():
    """Keeps the BLE connection alive and handles background tasks."""
    # Attempt initial connection
    await connect_client(ADDRESS)

    while True:
        # Reconnect logic
        if not is_connected():
            await connect_client(ADDRESS)
        await asyncio.sleep(10)

def start_ble_loop():
    """Starts the asyncio event loop in a separate thread."""
    def run_loop(loop_to_run):
        asyncio.set_event_loop(loop_to_run)
        loop_to_run.run_until_complete(ble_runner())

    # Start the thread as a daemon so it closes with the main application
    thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    thread.start()
    print("BLE: Controller background thread started.")

# Start the Bluetooth loop immediately upon import
start_ble_loop()
