// -------------------- MOTOR PIN DEFINITIONS --------------------
const int dirBase = 9;     
const int stepBase = 8;

const int dirArm = 11;     
const int stepArm = 10;

const int enPin = 3;       

const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

bool paused = true;
long armTarget = 0;    // latest commanded value
long baseTarget = 0;   // latest commanded value

long armCounter = 0;   // for keeping ratio
long baseCounter = 0;  // for keeping ratio

void setup() {
  Serial.begin(9600);

  pinMode(stepBase, OUTPUT);
  pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);
  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  pinMode(ms1, OUTPUT);
  pinMode(ms2, OUTPUT);
  pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH);
  digitalWrite(ms2, HIGH);
  digitalWrite(ms3, HIGH);

  digitalWrite(dirBase, HIGH);  
  digitalWrite(dirArm, HIGH);   
  digitalWrite(enPin, HIGH);    

  Serial.println("System initialized. Waiting for commands.");
}

void loop() {
  handleSerial();

  if (!paused) {
    if (armTarget > 0 || baseTarget > 0) {
      // Add current targets to counters
      armCounter += armTarget;
      baseCounter += baseTarget;

      // Whenever counters exceed target, step that motor
      if (armCounter >= baseTarget && armTarget > 0) {
        stepMotor(stepArm);
        armCounter -= baseTarget; // keep remainder
      }
      if (baseCounter >= armTarget && baseTarget > 0) {
        stepMotor(stepBase);
        baseCounter -= armTarget; // keep remainder
      }
    }
  }
}

void stepMotor(int pin) {
  digitalWrite(enPin, LOW);  
  digitalWrite(pin, HIGH);
  delayMicroseconds(500);
  digitalWrite(pin, LOW);
  delayMicroseconds(500);
}

void handleSerial() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.startsWith("ARM:")) {
      armTarget = cmd.substring(4).toInt();
      Serial.print("New Arm ratio value: ");
      Serial.println(armTarget);
    } else if (cmd.startsWith("BASE:")) {
      baseTarget = cmd.substring(5).toInt();
      Serial.print("New Base ratio value: ");
      Serial.println(baseTarget);
    } else if (cmd.equalsIgnoreCase("PAUSE")) {
      paused = true;
      digitalWrite(enPin, HIGH);
      Serial.println("Paused. Motors OFF.");
    } else if (cmd.equalsIgnoreCase("RESUME")) {
      paused = false;
      digitalWrite(enPin, LOW);
      Serial.println("Resumed. Motors ON.");
    }
  }
}
