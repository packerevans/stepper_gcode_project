#!/bin/bash
pkill -f app.py
cd /home/packer/Videos/stepper_gcode_project/
source venv/bin/activate

python3 app.py
