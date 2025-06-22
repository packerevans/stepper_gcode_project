from flask import Flask, render_template, request, redirect, url_for
import serial
import time

# Setup serial connection
arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
time.sleep(2)

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        command = request.form.get('command')
        repeat = int(request.form.get('repeat', 1))
        if command:
            for i in range(repeat):
                print(f"Sending: {command} ({i+1}/{repeat})")
                arduino.write((command + '\n').encode())
                time.sleep(0.2)
        return redirect(url_for('index'))
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
