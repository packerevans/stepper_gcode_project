// ==========================================
//      ARDUINO SAND TABLE FIRMWARE
//      Optimized for Flow Control (Credit System)
// ==========================================

// --- MOTOR PIN DEFINITIONS ---
const int dirBase = 9;
const int stepBase = 8;

const int dirArm = 11;
const int stepArm = 10;

const int enPin = 3;

// Microstepping Pins
const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

// --- STATE VARIABLES ---
bool paused = true;

// --- QUEUE CONFIGURATION ---
// 50 is the safe limit for Arduino Uno RAM
const int MAX_QUEUE_SIZE = 50; 

struct GCommand {
  long armSteps;
  long baseSteps;
  int speed;
};

GCommand queue[MAX_QUEUE_SIZE];
int queueHead = 0;
int queueTail = 0;
int queueCount = 0;

// --- SETUP ---
void setup() {
  Serial.begin(9600);

  pinMode(stepBase, OUTPUT);
  pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);
  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  // Microstepping Setup (High/High/High = 1/16th Step typically)
  pinMode(ms1, OUTPUT);
  pinMode(ms2, OUTPUT);
  pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH);
  digitalWrite(ms2, HIGH);
  digitalWrite(ms3, HIGH);

  digitalWrite(dirBase, HIGH);
  digitalWrite(dirArm, HIGH);
  digitalWrite(enPin, HIGH); // Start disabled (High = Off)

  // SIGNAL TO PYTHON WE ARE ALIVE
  // The F() macro keeps this string in Flash memory to save RAM
  Serial.println(F("READY")); 
}

// --- MAIN LOOP ---
void loop() {
  handleSerial();

  if (!paused && queueCount > 0) {
    executeNextCommand();
  }
}

// --- SERIAL HANDLING ---
void handleSerial() {
  // Process ALL available data in the buffer to prevent lag
  while (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.length() == 0) continue;

    // IMMEDIATE COMMANDS (Bypass Queue)
    if (cmd.equalsIgnoreCase("PAUSE")) {
      paused = true;
      digitalWrite(enPin, HIGH);
      Serial.println(F("Paused"));
      return; 
    }
    else if (cmd.equalsIgnoreCase("RESUME")) {
      paused = false;
      digitalWrite(enPin, LOW);
      Serial.println(F("Resumed"));
      return;
    }
    else if (cmd.equalsIgnoreCase("CLEAR")) {
      queueHead = queueTail = queueCount = 0;
      Serial.println(F("Cleared"));
      return;
    }
    
    // G-CODE COMMANDS (G1)
    if (cmd.startsWith("G1")) {
      parseAndEnqueue(cmd);
    }
  }
}

void parseAndEnqueue(String cmd) {
  // Expected Format: G1 <ArmSteps> <BaseSteps> <SpeedUs>
  long armSteps = 0;
  long baseSteps = 0;
  int speed = 1000;

  int firstSpace = cmd.indexOf(' ');
  if (firstSpace > 0) {
    String params = cmd.substring(firstSpace + 1);
    
    int space1 = params.indexOf(' ');
    int space2 = params.indexOf(' ', space1 + 1);

    if (space1 != -1) {
       armSteps = params.substring(0, space1).toInt();
       if (space2 != -1) {
         baseSteps = params.substring(space1 + 1, space2).toInt();
         speed = params.substring(space2 + 1).toInt();
       } else {
         baseSteps = params.substring(space1 + 1).toInt();
       }
    }
  }

  // Add to Queue
  if (queueCount >= MAX_QUEUE_SIZE) {
    Serial.println(F("ERROR:OVERFLOW")); 
    return;
  }

  queue[queueTail].armSteps = armSteps;
  queue[queueTail].baseSteps = baseSteps;
  queue[queueTail].speed = speed;

  queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
  queueCount++;
}

// --- EXECUTION ---
void executeNextCommand() {
  GCommand cmd = queue[queueHead];
  queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
  queueCount--; 

  moveBothMotors(cmd.armSteps, cmd.baseSteps, cmd.speed);

  // CRITICAL: Tell Python we finished one move so it can send one more
  // This is the "Credit Return" signal
  Serial.println(F("Done")); 
}

// --- MOTOR MOVEMENT ---
void moveBothMotors(long armSteps, long baseSteps, int delayUs) {
  digitalWrite(enPin, LOW); // Enable motors

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
    // CHECK FOR PAUSE MID-MOVE
    // This ensures that if you hit "Pause", it stops instantly,
    // not after the current line finishes.
    if (Serial.available()) handleSerial();

    while (paused) {
      digitalWrite(enPin, HIGH); // Disable motors to reduce heat
      delay(200);
      handleSerial(); // Check for RESUME
      if (!paused) digitalWrite(enPin, LOW); // Re-enable
    }

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
  // Note: Motors remain enabled here for holding torque between queued moves.
  // They are only disabled if the queue empties or we pause.
}

void pulsePin(int pin, int delayUs) {
  digitalWrite(pin, HIGH);
  delayMicroseconds(delayUs);
  digitalWrite(pin, LOW);
  delayMicroseconds(delayUs);
}

