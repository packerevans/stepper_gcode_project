// -------------------- MOTOR PIN DEFINITIONS --------------------
const int dirBase = 9;
const int stepBase = 8;

const int dirArm = 11;
const int stepArm = 10;

const int enPin = 3;

const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

// -------------------- STATE VARIABLES --------------------
bool paused = true;
int motorSpeed = 1000;  // default µs delay

// -------------------- QUEUE CONFIG --------------------
const int MAX_QUEUE_SIZE = 40;

struct GCommand {
  long armSteps;
  long baseSteps;
  int speed;
};

GCommand queue[MAX_QUEUE_SIZE];
int queueHead = 0; // next to execute
int queueTail = 0; // next to fill
int queueCount = 0;

// -------------------- SETUP --------------------
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

  Serial.println("System initialized. Waiting for G-code commands.");
}

// -------------------- MAIN LOOP --------------------
void loop() {
  handleSerial();

  if (!paused && queueCount > 0) {
    executeNextCommand();
  }
}

// -------------------- SERIAL HANDLER --------------------
void handleSerial() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("PAUSE")) {
    paused = true;
    digitalWrite(enPin, HIGH);
    Serial.println("Paused. Motors OFF.");
    return;
  }

  if (cmd.equalsIgnoreCase("RESUME")) {
    paused = false;
    digitalWrite(enPin, LOW);
    Serial.println("Resumed. Motors ON.");
    return;
  }

  if (cmd.equalsIgnoreCase("CLEAR")) {
    queueHead = queueTail = queueCount = 0;
    Serial.println("Command queue cleared.");
    return;
  }

  // -------------------- G1 Command Parsing --------------------
  if (cmd.startsWith("G1")) {
    long armSteps = 0;
    long baseSteps = 0;
    int speed = 1000;

    // Example: G1 -3200 3200 5000
    int firstSpace = cmd.indexOf(' ');
    if (firstSpace > 0) {
      String params = cmd.substring(firstSpace + 1);
      params.trim();

      int space1 = params.indexOf(' ');
      int space2 = params.indexOf(' ', space1 + 1);

      if (space1 != -1 && space2 != -1) {
        armSteps = params.substring(0, space1).toInt();
        baseSteps = params.substring(space1 + 1, space2).toInt();
        speed = params.substring(space2 + 1).toInt();
      }
    }

    enqueueCommand(armSteps, baseSteps, speed);
  }
}

// -------------------- QUEUE FUNCTIONS --------------------
void enqueueCommand(long arm, long base, int spd) {
  if (queueCount >= MAX_QUEUE_SIZE) {
    Serial.println("Queue full. Command ignored.");
    return;
  }

  queue[queueTail].armSteps = arm;
  queue[queueTail].baseSteps = base;
  queue[queueTail].speed = spd;

  queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
  queueCount++;

  Serial.print("Queued G1 command: ");
  Serial.print(arm);
  Serial.print(" ");
  Serial.print(base);
  Serial.print(" ");
  Serial.println(spd);

  if (paused) {
    Serial.println("(Paused — will execute later)");
  }
}

bool dequeueCommand(GCommand &cmd) {
  if (queueCount == 0) return false;

  cmd = queue[queueHead];
  queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
  queueCount--;
  return true;
}

// -------------------- EXECUTION --------------------
void executeNextCommand() {
  GCommand cmd;
  if (!dequeueCommand(cmd)) return;

  moveBothMotors(cmd.armSteps, cmd.baseSteps, cmd.speed);
  Serial.println("Done");
}

// -------------------- MOVE BOTH MOTORS (UPDATED) --------------------
void moveBothMotors(long armSteps, long baseSteps, int delayUs) {
  // Enable motors
  digitalWrite(enPin, LOW);

  // Direction setup
  digitalWrite(dirArm, (armSteps >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (baseSteps >= 0) ? HIGH : LOW);

  armSteps = abs(armSteps);
  baseSteps = abs(baseSteps);

  long maxSteps = max(armSteps, baseSteps);
  float armRatio = (float)armSteps / maxSteps;
  float baseRatio = (float)baseSteps / maxSteps;

  float armProgress = 0;
  float baseProgress = 0;

  for (long i = 0; i < maxSteps; i++) {
    handleSerial(); // Check for pause commands

    // --- CHANGED LOGIC START ---
    // Instead of 'return' (which skips the rest of the line), 
    // we loop here forever until unpaused.
    while (paused) {
      digitalWrite(enPin, HIGH); // Disable motors to keep cool
      Serial.println("Movement paused mid-execution.");
      delay(500); // Small delay to stop serial flooding
      handleSerial(); // Keep checking for RESUME
      
      // If we resumed, re-enable motors and break this while loop
      if (!paused) {
         digitalWrite(enPin, LOW);
         Serial.println("Resuming movement...");
      }
    }
    // --- CHANGED LOGIC END ---

    armProgress += armRatio;
    baseProgress += baseRatio;

    if (armProgress >= 1.0) {
      pulsePin(stepArm, delayUs);
      armProgress -= 1.0;
    }

    if (baseProgress >= 1.0) {
      pulsePin(stepBase, delayUs);
      baseProgress -= 1.0;
    }
  }

  digitalWrite(enPin, HIGH); // disable motors after move
}

// -------------------- STEP PULSE --------------------
void pulsePin(int pin, int delayUs) {
  digitalWrite(pin, HIGH);
  delayMicroseconds(delayUs);
  digitalWrite(pin, LOW);
  delayMicroseconds(delayUs);
}
