// === Pin Definitions ===
const int BASE_DIR_PIN = 9;
const int BASE_STEP_PIN = 8;
const int ARM_DIR_PIN = 11;
const int ARM_STEP_PIN = 10;

const int EN_PIN = 3;
const int MS1_PIN = 4;
const int MS2_PIN = 5;
const int MS3_PIN = 6;

const int SAFETY_BUTTON_PIN = 12;

// === Movement Settings ===
const unsigned long STEP_DELAY_US = 8000;  // Speed

// === Motor State ===
long basePos = 1000;
long armPos = 1000;
long targetBase = 1000;
long targetArm = 1000;

bool paused = false;
bool lastButtonState = HIGH;

void setup() {
  Serial.begin(9600);

  pinMode(BASE_DIR_PIN, OUTPUT);
  pinMode(BASE_STEP_PIN, OUTPUT);
  pinMode(ARM_DIR_PIN, OUTPUT);
  pinMode(ARM_STEP_PIN, OUTPUT);

  pinMode(EN_PIN, OUTPUT);
  pinMode(MS1_PIN, OUTPUT);
  pinMode(MS2_PIN, OUTPUT);
  pinMode(MS3_PIN, OUTPUT);
  pinMode(SAFETY_BUTTON_PIN, INPUT_PULLUP);

  digitalWrite(MS1_PIN, HIGH);  // 1/16 microstepping
  digitalWrite(MS2_PIN, HIGH);
  digitalWrite(MS3_PIN, HIGH);

  disableMotors();
  Serial.println("Ready.");
}

void loop() {
  handleSafetyButton();
  handleSerial();

  if (!paused && (basePos != targetBase || armPos != targetArm)) {
    enableMotors();
    coordinatedMove();
    disableMotors();
  }
}

void handleSafetyButton() {
  bool buttonState = digitalRead(SAFETY_BUTTON_PIN);
  if (buttonState == LOW && lastButtonState == HIGH) {
    paused = !paused;
    paused ? disableMotors() : enableMotors();
    Serial.println(paused ? "Paused by button" : "Resumed by button");
    delay(300);
  }
  lastButtonState = buttonState;
}

void handleSerial() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.startsWith("BASE:")) {
      targetBase = cmd.substring(5).toInt();
      Serial.print("BASE Target: "); Serial.println(targetBase);
    } else if (cmd.startsWith("ARM:")) {
      targetArm = cmd.substring(4).toInt();
      Serial.print("ARM Target: "); Serial.println(targetArm);
    } else if (cmd == "PAUSE") {
      paused = true;
      disableMotors();
      Serial.println("Paused");
    } else if (cmd == "RESUME") {
      paused = false;
      enableMotors();
      Serial.println("Resumed");
    } else if (cmd == "GET") {
      Serial.print("ARM=");
      Serial.print(armPos);
      Serial.print(" BASE=");
      Serial.println(basePos);
    } else {
      Serial.print("Unknown: "); Serial.println(cmd);
    }
  }
}

void enableMotors() {
  digitalWrite(EN_PIN, LOW);  // Enable drivers
}

void disableMotors() {
  digitalWrite(EN_PIN, HIGH);  // Disable drivers
}

// === Coordinated Movement (Proportional Stepping) ===
void coordinatedMove() {
  long deltaBase = targetBase - basePos;
  long deltaArm = targetArm - armPos;

  int dirBase = deltaBase >= 0 ? HIGH : LOW;
  int dirArm = deltaArm >= 0 ? HIGH : LOW;

  long stepsBase = abs(deltaBase);
  long stepsArm = abs(deltaArm);

  digitalWrite(BASE_DIR_PIN, dirBase);
  digitalWrite(ARM_DIR_PIN, dirArm);

  long steps = max(stepsBase, stepsArm);
  long errBase = 0;
  long errArm = 0;

  for (long i = 0; i < steps; i++) {
    if (paused) break;

    errBase += stepsBase;
    errArm += stepsArm;

    if (errBase >= steps) {
      stepMotor(BASE_STEP_PIN);
      basePos += dirBase == HIGH ? 1 : -1;
      errBase -= steps;
    }

    if (errArm >= steps) {
      stepMotor(ARM_STEP_PIN);
      armPos += dirArm == HIGH ? 1 : -1;
      errArm -= steps;
    }

    delayMicroseconds(STEP_DELAY_US);
  }

  Serial.println("Movement complete.");
}

void stepMotor(int stepPin) {
  digitalWrite(stepPin, HIGH);
  delayMicroseconds(STEP_DELAY_US / 2);
  digitalWrite(stepPin, LOW);
  delayMicroseconds(STEP_DELAY_US / 2);
}
