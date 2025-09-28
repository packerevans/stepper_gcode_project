import asyncio
from bleak import BleakClient

# The MAC address of your LED strip controller
ADDRESS = "BE:67:00:44:05:61" # <-- Your address goes here!

# The likely write characteristic UUID. You might need to try the other one if this fails.
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb" 

async def main(address):
    print(f"Attempting to connect to {address}...")
    async with BleakClient(address) as client:
        if client.is_connected:
            print("Successfully connected!")
            
            # This is an example command to set the color to blue (R=0, G=0, B=255)
            # Command formats can vary, so this is a common example to start with.
            blue_command = bytearray([0x7e, 0x07, 0x05, 0x03, 0x00, 0x00, 0xff, 0x10, 0xef])

            await client.write_gatt_char(WRITE_UUID, blue_command)
            print("Sent command to turn the lights blue.")
        else:
            print(f"Failed to connect to {address}")

if __name__ == "__main__":
    # Ensure you run this on a device with Bluetooth enabled and the bleak library installed
    # pip install bleak
    asyncio.run(main(ADDRESS))
