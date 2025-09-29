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


# --- Common LED Commands (9-byte protocol) ---
# Format: [0x7e, Length, Function, Channel, R, G, B, Brightness, 0xef]
def create_rgb_command(r: int, g: int, b: int, brightness: int) -> bytearray:
    """Creates the 9-byte command for setting RGB color with brightness."""
    # Ensure values are capped at 255 for R, G, B and 16 for Brightness (0x10)
    r = min(255, max(0, r))
    g = min(255, max(0, g))
    b = min(255, max(0, b))
    # Note: Your HTML uses 1-16. 0x10 is 16. We use the raw value from the slider.
    brightness = min(16, max(1, brightness)) 

    return bytearray([
        0x7e, 0x07, 0x05, 0x03,  # Header: Start, Len (5+2=7), Function, Channel
        r, g, b,                 # R, G, B
        brightness,              # Brightness (e.g., 0x01 to 0x10)
        0xef                     # End Byte
    ])

# Commands using the default brightness (0x10)
COMMANDS = {
    # 0x04 function is often for ON/OFF
    # For ON, we can send a full white at max brightness (0xFF, 0xFF, 0xFF, 0x10)
    "POWER:ON": create_rgb_command(255, 255, 255, 16), 
    "POWER:OFF": bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef]), # Official OFF command
}

# --- BLE Client Management Functions ---

async def connect_client(address: str):
    """Connects the client and makes it persistent."""
    global client, is_connected_flag
    if client and client.is_connected:
        print("BLE: Already connected.")
        is_connected_flag = True
        return True

    print(f"BLE: Attempting to connect to {address}...")
    try:
        # Re-initialize the client for a new connection attempt
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
        print("BLE: Disconnecting...")
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
    # Also check if the client object itself thinks it's connected
    if client and client.is_connected:
        is_connected_flag = True
    return is_connected_flag

# --- Command Execution Functions ---

async def send_command(cmd_bytes: bytearray, cmd_name: str):
    """A helper function to send byte commands over BLE."""
    global is_connected_flag
    if not is_connected_flag:
        print("BLE: Cannot send command, not connected. Attempting reconnect...")
        if not await connect_client(ADDRESS):
             print("BLE: Reconnect failed. Command aborted.")
             return

    async with command_lock:
        try:
            await client.write_gatt_char(WRITE_UUID, cmd_bytes)
            print(f"BLE: Sent {cmd_name} → {list(cmd_bytes)}")
        except Exception as e:
            print(f"BLE: Error sending command {cmd_name} - {e}")
            # If a send fails, assume connection is lost
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
        # Attempt to reconnect if a command is sent while disconnected
        if not await connect_client(ADDRESS):
            print("BLE: Failed to connect, cannot send color.")
            return

    cmd_bytes = create_rgb_command(r, g, b, brightness)
    await send_command(cmd_bytes, f"RGB({r},{g},{b}) Br:{brightness}")

# --- Initialization and Background Loop ---

async def ble_runner():
    """Keeps the BLE connection alive and handles background tasks."""
    # Attempt initial connection
    await connect_client(ADDRESS)

    # Optional: Keep trying to connect if disconnected
    while True:
        if not is_connected():
            print("BLE: Attempting to re-connect in 10s...")
            await connect_client(ADDRESS)
        await asyncio.sleep(10)

def start_ble_loop():
    """Starts the asyncio event loop in a separate thread."""
    def run_loop(loop_to_run):
        asyncio.set_event_loop(loop_to_run)
        loop_to_run.run_until_complete(ble_runner())

    thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    thread.start()
    print("BLE: Controller background thread started.")

# This function call ensures the Bluetooth loop starts immediately when app.py imports this module.
start_ble_loop()
