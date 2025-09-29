# ble_controller.py
import asyncio
from bleak import BleakClient

# Replace with your LED strip MAC address
ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# Global client
client: BleakClient | None = None

async def connect():
    """Connect to the LED strip via BLE."""
    global client
    if client and client.is_connected:
        return True
    try:
        client = BleakClient(ADDRESS)
        await client.connect()
        print("✅ Connected to LED strip")
        return client.is_connected
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        client = None
        return False

async def disconnect():
    """Disconnect from the LED strip."""
    global client
    if client:
        try:
            await client.disconnect()
            print("🔌 Disconnected from LED strip")
        except Exception as e:
            print(f"⚠️ Error disconnecting: {e}")
    client = None

async def send_led_command(r: int, g: int, b: int):
    """Send an RGB command to the LED strip."""
    global client
    if not client or not client.is_connected:
        connected = await connect()
        if not connected:
            print("❌ Not connected to LED strip")
            return
    try:
        cmd = bytearray([0x7e, 0x07, 0x05, 0x03, r, g, b, 0x10, 0xef])
        await client.write_gatt_char(WRITE_UUID, cmd)
        print(f"🎨 Sent LED command: R={r} G={g} B={b}")
    except Exception as e:
        print(f"⚠️ Error sending LED command: {e}")

async def power_on():
    """Turn LED strip on (white)."""
    await send_led_command(255, 255, 255)

async def power_off():
    """Turn LED strip off (black)."""
    await send_led_command(0, 0, 0)

async def handle_command(command: str):
    """Process commands coming from the frontend."""
    parts = command.split(":")
    cmd = parts[0].upper()

    if cmd == "CONNECT":
        return await connect()
    elif cmd == "DISCONNECT":
        return await disconnect()
    elif cmd == "POWER":
        if len(parts) > 1 and parts[1].upper() == "ON":
            return await power_on()
        else:
            return await power_off()
    elif cmd == "LED":
        try:
            r, g, b = map(int, parts[1].split(","))
            return await send_led_command(r, g, b)
        except Exception as e:
            print(f"⚠️ Invalid LED command: {e}")
    else:
        print(f"⚠️ Unknown command: {command}")