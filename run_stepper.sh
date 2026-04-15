#!/bin/bash

# Navigate to the project directory
cd /home/packer/Videos/stepper_gcode_project

# Pull latest changes (optional, but keep if user likes it)
git pull origin master

# Start the application
# We use a loop to ensure it restarts if it crashes
while true; do
    python3 app.py
    echo "App crashed or was told to exit. Restarting in 1 second..."
    sleep 1
done
