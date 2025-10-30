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
const unsigned long STEP_DELAY_US = 4000;  // Speed

// === Motor State ===
long basePos = 0;
long armPos = 0;
long targetBase = 0;  // steps per loop
long targetArm = 0;   // steps per loop

bool paused = true;   // start paused
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
  Serial.println("Ready. System starts PAUSED.");
}

void loop() {
  handleSafetyButton();
  handleSerial();

  if (!paused) {
    enableMotors();
    runMotors();
  } else {
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
      Serial.print("BASE steps per loop: "); Serial.println(targetBase);
    } else if (cmd.startsWith("ARM:")) {
      targetArm = cmd.substring(4).toInt();
      Serial.print("ARM steps per loop: "); Serial.println(targetArm);
    } else if (cmd == "PAUSE") {
      paused = true;
      disableMotors();
      Serial.println("Paused");
    } else if (cmd == "RESUME") {
      paused = false;
      enableMotors();
      Serial.println("Resumed");
    } else if (cmd == "GET") {
      Serial.print("ARM="); Serial.print(armPos);
      Serial.print(" BASE="); Serial.println(basePos);
    } else {
      Serial.print("Unknown: "); Serial.println(cmd);
    }
  }
}

void enableMotors() {
  digitalWrite(EN_PIN, LOW);  // Enable drivers (active LOW for A4988/DRV8825)
}

void disableMotors() {
  digitalWrite(EN_PIN, HIGH);  // Disable drivers
}

// === Run motors based on step counts per loop ===
void runMotors() {
  long baseSteps = 0;
  long armSteps = 0;

  // Compute steps per loop safely
  if (armPos != 0) {
    baseSteps = targetBase / armPos;
  } else {
    baseSteps = targetBase;  // fallback if armPos = 0
  }

  if (basePos != 0) {
    armSteps = targetArm / basePos;
  } else {
    armSteps = targetArm;  // fallback if basePos = 0
  }

  // Base motor
  if (baseSteps != 0) {
    int dirBase = baseSteps > 0 ? HIGH : LOW;
    digitalWrite(BASE_DIR_PIN, dirBase);
    for (int i = 0; i < abs(baseSteps); i++) {
      stepMotor(BASE_STEP_PIN);
      basePos += (dirBase == HIGH ? 1 : -1);
    }
  }

  // Arm motor
  if (armSteps != 0) {
    int dirArm = armSteps > 0 ? HIGH : LOW;
    digitalWrite(ARM_DIR_PIN, dirArm);
    for (int i = 0; i < abs(armSteps); i++) {
      stepMotor(ARM_STEP_PIN);
      armPos += (dirArm == HIGH ? 1 : -1);
    }
  }

  Serial.print("Loop complete. BASE="); Serial.print(basePos);
  Serial.print(" ARM="); Serial.println(armPos);
}

void stepMotor(int stepPin) {
  digitalWrite(stepPin, HIGH);
  delayMicroseconds(STEP_DELAY_US / 2);
  digitalWrite(stepPin, LOW);
  delayMicroseconds(STEP_DELAY_US / 2);
}
