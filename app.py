from flask import Flask, render_template, request, redirect, url_for
import serial
import time

# Setup serial connection to Arduino
arduino = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
time.sleep(2)  # Wait for Arduino to reset

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        command = request.form.get('command')
        if command:
            print(f"Sending to Arduino: {command}")
            arduino.write((command + '\n').encode())
            time.sleep(0.1)
        return redirect(url_for('index'))
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
