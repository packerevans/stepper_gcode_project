import asyncio
from bleak import BleakClient

ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# Commands
turn_on = bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef])
crossfade_7color = bytearray([0x7e, 0x04, 0x04, 0x03, 0xff, 0x00, 0x00, 0x00, 0xef])

async def main(address):
    print(f"Attempting to connect to {address}...")
    async with BleakClient(address) as client:
        if client.is_connected:
            print("Successfully connected!")

            # Turn the lights ON
            await client.write_gatt_char(WRITE_UUID, turn_on)
            print("Turned lights ON.")

            # Start 7-color crossfade
            await client.write_gatt_char(WRITE_UUID, crossfade_7color)
            print("Started 7-color crossfade mode.")
        else:
            print(f"Failed to connect to {address}")

if __name__ == "__main__":
    asyncio.run(main(ADDRESS))
