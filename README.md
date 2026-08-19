# Sand Table

A kinematic sand table driven by a 2-link SCARA arm with stepper motors, controlled through a web interface hosted on a Raspberry Pi.

**Live Demo:** [elevatedcleaning.page/sandtable](https://elevatedcleaning.page/sandtable)

## Overview

Designs are stored as theta-rho polar coordinate files. The web UI allows you to queue designs, control LEDs, schedule automated actions, and manually jog the arm. The firmware runs on an LGT8F328P (Arduino-compatible, 32 MHz) and handles inverse kinematics, micro-segmented motion planning, and stepper pulse generation for TMC2209 drivers.

## Features

- Web-based control interface (Flask / Waitress, 8 threads)
- Theta-rho polar coordinate design format with auto-thumbnailing
- Inverse kinematics for 2-link SCARA arm (101.3 mm per link)
- TMC2209 stepper drivers with configurable microstepping
- RGB LED control with static color, flash, fade, and cycle modes
- Design queue with loop/shuffle playlist and cooldown between jobs
- Scheduler for timed LED and sand actions by day/time
- Ngrok tunneling for remote access outside the local network
- Over-the-air firmware compile and flash from the web UI
- WiFi setup page (auto-redirects when in AP mode)
- Auto-calibration via magnet endstops with center-finding

## Architecture

```
[Browser] <--HTTP--> [Raspberry Pi: Flask app (app.py)]
                              |
                        Serial (250000 baud)
                              |
                     [LGT8F328P: Sand.ino]
                              |
                     TMC2209 drivers --> Stepper motors
                              |
                     RGB LEDs (PWM)
```

The Pi runs `app.py` under Waitress as a production WSGI server. It parses theta-rho design files, manages the job queue, and streams coordinates one at a time over serial. The Arduino firmware converts each polar coordinate to Cartesian via inverse kinematics, micro-segments the path, and drives both steppers using Bresenham line drawing with a non-blocking state machine.

## Requirements

- Raspberry Pi (any model with GPIO UART)
- LGT8F328P board (or Arduino Uno/Nano equivalent)
- Python 3 with packages: `flask`, `waitress`, `pyserial`, `pyngrok`, `Pillow`
- `arduino-cli` (for firmware flashing from the web UI)
- TMC2209 stepper drivers, NEMA 17 motors, hall-effect endstops

## Setup

1. Clone the repository onto the Pi.
2. Install dependencies: `pip install -r requirements.txt`
3. Connect the Arduino to the Pi via UART or USB serial.
4. Run the server: `bash run_stepper.sh` (loops on crash for auto-restart).
5. Open the Pi's IP on port 5000 in a browser.

## Design Format

Each `.txt` file in `templates/designs/` contains one coordinate per line:

```
theta rho
```

Where `theta` is the angle in radians (unbounded, continuous) and `rho` is the normalized radius (0.0 = center, 1.0 = edge).
