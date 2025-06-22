from flask import Flask, render_template, request, redirect, url_for
import serial
import time

arduino = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
time.sleep(2)

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        command = request.form.get('command')
        repeat = int(request.form.get('repeat', 1))
        if command:
            for _ in range(repeat):
                arduino.write((command + '\n').encode())
                time.sleep(0.2)
        return redirect(url_for('index'))
    return render_template('index.html')


@app.route('/designs', methods=['GET', 'POST'])
def designs():
    if request.method == 'POST':
        design = request.form.get('design')
        if design == "Spiral":
            arduino.write(b"A1 L100 U3200\n")
        elif design == "Zigzag":
            arduino.write(b"A1 L300 U300\n")
        elif design == "Circle":
            arduino.write(b"A1 L50 U3200\n")
        elif design == "Wave":
            arduino.write(b"A1 L200 U200\n")
        time.sleep(0.5)
        return redirect(url_for('designs'))
    return render_template('designs.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
