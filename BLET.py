import asyncio
from bleak import BleakClient

# The MAC address of your LED strip controller
ADDRESS = "BE:67:00:44:05:61" # <-- Your address goes here!

# The likely write characteristic UUID. You might need to try the other one if this fails.
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# --- COMMON COMMANDS FOR MAGIC HOME TYPE CONTROLLERS ---
# The last byte is usually a checksum (sum of all preceding bytes, modulo 256).

# 1. Command to Turn ON the lights
# Format: [Start Byte (0x7E), Command Type (e.g., 0x04), On/Off (0x01 for ON), Reserved (0x00), Checksum]
# The below is a common 5-byte ON command format:
POWER_ON_COMMAND = bytearray([0xCC, 0x23, 0x33]) # Another common simplified ON command (often for older types)
# A more modern/complex 9-byte ON command often uses the color setting structure:
# ON/OFF uses a specific function code. Assuming a 9-byte structure for consistency:
# The simple [0xCC, 0x23, 0x33] is a good first try for a power toggle.

# 2. Command for 7-Color Crossfade
# Most controllers use an effect code for this. Common effect codes are:
# 0x25: 7-Color Crossfade (common)
# 0x38: 7-Color Jump (another similar one)
# Command Format: [Start Byte, Function Code (0x03 for mode), Mode (0x25), Speed (0x0F - mid speed), Checksum]
# For the 7-Color Crossfade (Mode 0x25) with a medium speed (0x0F - adjust as needed, 0x01 is fastest, 0x1F is slowest)
MODE_7_COLOR_CROSSFADE = bytearray([0xBB, 0x03, 0x25, 0x0F, 0xCC])
# For controllers using the 0x7E/0xef structure, a mode command might look like:
# MODE_7_COLOR_CROSSFADE = bytearray([0x7E, 0x07, 0x03, 0x01, 0x25, 0x0F, 0x00, 0x10, 0xef]) # This is a guess!
# Let's stick to the widely used 5-byte pattern for modes: [0xBB, Function, Mode, Speed, Checksum]

async def main(address):
    print(f"Attempting to connect to {address}...")
    try:
        async with BleakClient(address) as client:
            if client.is_connected:
                print("Successfully connected!")

                # --- 1. Send Command to Turn ON ---
                # Use the simple ON command first. If it doesn't work, you might need to try a more complex one.
                print("Sending command to turn the lights ON...")
                # Note: Not all controllers require a separate ON command if a mode/color command is sent.
                # await client.write_gatt_char(WRITE_UUID, POWER_ON_COMMAND)
                # await asyncio.sleep(0.5) # Give the light time to process the ON command

                # --- 2. Send Command for 7-Color Crossfade ---
                print("Sending command for 7-Color Crossfade (Mode 0x25)...")
                await client.write_gatt_char(WRITE_UUID, MODE_7_COLOR_CROSSFADE)
                print("Command sent. Check the lights for the 7-Color Crossfade effect.")
            else:
                print(f"Failed to connect to {address}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Ensure you run this on a device with Bluetooth enabled and the bleak library installed
    # pip install bleak
    asyncio.run(main(ADDRESS))
