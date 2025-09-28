#!/usr/bin/env python3
"""
Brute-force BLE mode scanner for your LED strip.

Usage:
    python3 BLET_scan.py

After each packet is sent you'll be asked to observe the strip and type:
    y  -> effect observed
    n  -> no change
    o  -> lights went off / unexpected
    q  -> quit the scan

Log is appended to led_mode_scan_log.txt
"""

import asyncio
import sys
import time
from datetime import datetime
from bleak import BleakClient

# === CONFIG ===
ADDRESS = "BE:67:00:44:05:61"  # replace with your address if needed
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
LOG_FILE = "led_mode_scan_log.txt"

# Known safe commands (so you can quickly power back on if needed)
TURN_ON = bytearray([0x7e, 0x04, 0x04, 0x01, 0xff, 0x00, 0x00, 0x00, 0xef])
TURN_OFF = bytearray([0x7e, 0x04, 0x04, 0x00, 0xff, 0x00, 0x00, 0x00, 0xef])

# Packet templates to try.
# Each template is a list; the special tokens "MODE" and "SPEED" will be replaced.
# These represent common layouts seen across controllers.
TEMPLATES = [
    # template name, bytearray template (use ints and placeholder strings)
    ("tmpl_5_03_mode_speed", [0x7e, 0x05, 0x03, 0x03, "MODE", "SPEED", 0x00, 0x00, 0xef]),
    ("tmpl_05_05_mode_speed", [0x7e, 0x05, 0x05, 0x03, "MODE", "SPEED", 0x00, 0x00, 0xef]),
    ("tmpl_07_05_mode_rgb_like", [0x7e, 0x07, 0x05, 0x03, "MODE", "SPEED", 0x00, 0x10, 0xef]),  # tries inserting values into 'RGB' positions
]

# Ranges to try
MODE_RANGE = range(1, 32)     # modes 1..31
SPEED_VALUES = [0x01, 0x04, 0x08, 0x10, 0x1f]  # sample speeds (fast -> slow)

# Delay after sending a command to let you observe (seconds)
OBSERVE_DELAY = 1.5

# --- helper functions ---
def build_packet(template_bytes, mode_value, speed_value):
    packet = []
    for b in template_bytes:
        if b == "MODE":
            packet.append(mode_value & 0xFF)
        elif b == "SPEED":
            packet.append(speed_value & 0xFF)
        else:
            packet.append(b)
    return bytearray(packet)

def log_line(line):
    timestamp = datetime.now().isoformat(sep=' ', timespec='seconds')
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} {line}\n")

async def interactive_scan(address):
    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        if not client.is_connected:
            print("Failed to connect. Make sure device is powered and in range.")
            return

        print("Connected. Starting brute-force mode scan.")
        print("After each packet, type y/n/o/q when prompted.")
        print("If lights go off, type 'o' and you can reapply the TURN_ON command.")
        print("Log file:", LOG_FILE)
        log_line("=== START SCAN SESSION ===")

        try:
            for tmpl_name, tmpl in TEMPLATES:
                print("\n--- Template:", tmpl_name, tmpl)
                log_line(f"TRY_TEMPLATE {tmpl_name} {tmpl}")

                for mode in MODE_RANGE:
                    for speed in SPEED_VALUES:
                        packet = build_packet(tmpl, mode, speed)
                        try:
                            await client.write_gatt_char(WRITE_UUID, packet)
                        except Exception as e:
                            print(f"ERROR sending packet: {e}")
                            log_line(f"ERROR_SEND template={tmpl_name} mode={mode} speed={speed} err={e}")
                            # continue to next

                        # show the packet to the user
                        print(f"[Sent] tmpl={tmpl_name} mode={mode} speed={speed} -> {list(packet)}")
                        log_line(f"SENT template={tmpl_name} mode={mode} speed={speed} bytes={list(packet)}")

                        # give the strip a moment to react
                        await asyncio.sleep(OBSERVE_DELAY)

                        # ask user for observation
                        while True:
                            try:
                                resp = input("Observation? (y = effect / n = no change / o = went off / q = quit) > ").strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                resp = "q"
                            if resp in ("y","n","o","q"):
                                break
                            print("Invalid input. Use y/n/o/q.")

                        log_line(f"OBS template={tmpl_name} mode={mode} speed={speed} obs={resp}")

                        if resp == "o":
                            # attempt to turn back on automatically and log it
                            print("Detected 'went off'. Sending TURN_ON command to restore power.")
                            try:
                                await client.write_gatt_char(WRITE_UUID, TURN_ON)
                                print("TURN_ON sent.")
                                log_line("AUTO_RESTORE TURN_ON sent after 'o'")
                            except Exception as e:
                                print("Failed to send TURN_ON:", e)
                                log_line(f"AUTO_RESTORE_FAILED err={e}")

                            # give it a moment
                            await asyncio.sleep(0.8)

                        if resp == "q":
                            print("Quitting scan by user request.")
                            log_line("USER_QUIT")
                            return

            print("Scan complete.")
            log_line("=== END SCAN SESSION ===")
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            log_line("USER_INTERRUPTED")
            return

# --- entrypoint ---
if __name__ == "__main__":
    print("Starting LED mode brute-force scanner.")
    print("Make sure you are watching the strip when running this.")
    print("Press Ctrl+C to abort at any time.\n")
    try:
        asyncio.run(interactive_scan(ADDRESS))
    except Exception as exc:
        print("Fatal error:", exc)
        log_line(f"FATAL_ERROR {exc}")
        sys.exit(1)
