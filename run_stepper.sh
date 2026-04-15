#!/bin/bash

cd /home/packer/Videos/stepper_gcode_project/
source venv/bin/activate
pkill -f app.py
while true; do
    python3 app.py
    echo "App crashed or was told to exit. Restarting in 1 second..."
    sleep 1
done
