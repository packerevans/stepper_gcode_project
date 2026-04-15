#!/bin/bash

pkill -f app.py
cd stepper_gcode_project/
source venv/bin/activate

python3 app.py
