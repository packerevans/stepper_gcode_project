import asyncio
from bleak import BleakClient

ADDRESS = "BE:67:00:44:05:61"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

# Define a set of example commands to test
COMMANDS = {
    "turn_on":       bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef]),
    "turn_off":      bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef]),
    "blue":          bytearray([0x7e, 0x07, 0x05, 0x03, 0x00, 0x00, 0xff, 0x10, 0xef]),
    "red":           bytearray([0x7e, 0x07, 0x05, 0x03, 0xff, 0x00, 0x00, 0x10, 0xef]),
    "green":         bytearray([0x7e, 0x07, 0x05, 0x03, 0x00, 0xff, 0x00, 0x10, 0xef]),
    "crossfade_7":    bytearray([0x7e, 0x05, 0x03, 0x03, 0x01, 0x10, 0x00, 0x00, 0xef]),  # 7-color crossfade, medium speed
    "flash":       bytearray([0x7e, 0x05, 0x03, 0x03, 0x0f, 0x10, 0x00, 0x00, 0xef]),  # flash mode
    "strobe":      bytearray([0x7e, 0x05, 0x03, 0x03, 0x0e, 0x10, 0x00, 0x00, 0xef]),  # strobe mode
    "fade":        bytearray([0x7e, 0x05, 0x03, 0x03, 0x0c, 0x10, 0x00, 0x00, 0xef])   # generic fade

}

LOG_FILE = "led_command_log.txt"


async def interactive_control(address):
    async with BleakClient(address) as client:
        if not client.is_connected:
            print(f"Failed to connect to {address}")
            return

        print("Connected to LED controller.")
        print("Available commands:")
        for key in COMMANDS.keys():
            print(f" - {key}")
        print("Type the command name (or 'exit' to quit).")

        while True:
            cmd_name = input("\nEnter command: ").strip().lower()

            if cmd_name == "exit":
                print("Exiting.")
                break

            if cmd_name not in COMMANDS:
                print("Unknown command. Try again.")
                continue

            # Send command
            cmd_bytes = COMMANDS[cmd_name]
            await client.write_gatt_char(WRITE_UUID, cmd_bytes)

            print(f"Sent: {cmd_name} → {list(cmd_bytes)}")

            # Log to file
            with open(LOG_FILE, "a") as f:
                f.write(f"Sent {cmd_name}: {list(cmd_bytes)}\n")

        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(interactive_control(ADDRESS))
