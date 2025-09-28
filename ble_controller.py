# ble_controller.py
import asyncio
from bleak import BleakClient

# Replace with your LED strip MAC address
ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

async def send_led_command(r, g, b):
    try:
        async with BleakClient(ADDRESS) as client:
            if client.is_connected:
                # Construct command bytearray
                cmd = bytearray([0x7e, 0x07, 0x05, 0x03, r, g, b, 0x10, 0xef])
                await client.write_gatt_char(WRITE_UUID, cmd)
                print(f"Sent LED command: R={r} G={g} B={b}")
            else:
                print("Failed to connect to LED strip")
    except Exception as e:
        print(f"Error sending LED command: {e}")
