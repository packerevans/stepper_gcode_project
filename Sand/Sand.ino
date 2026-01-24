// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v5
//      Restored Pins + Auto-Pause (M0)
// ==========================================

// --- YOUR SPECIFIC MOTOR PINS ---
const int stepBase = 8;
const int dirBase  = 9;
const int stepArm  = 10;
const int dirArm   = 11;
const int enPin    = 3;

// Microstepping Pins (Optional, keep if you use them)
const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

// --- SETTINGS ---
// MUST match your Python script (usually 9600 for older scripts)
#define BAUD_RATE 115200 

// --- STATE VARIABLES ---
bool paused = true; 
const int MAX_QUEUE_SIZE = 50;

// Command Types
const int CMD_MOVE = 0;
const int CMD_WAIT = 1;

struct GCommand {
  int type;       // 0 = Move, 1 = Wait (M0)
  long armSteps;
  long baseSteps;
  int speed;
};

GCommand queue[MAX_QUEUE_SIZE];
int queueHead = 0;
int queueTail = 0;
int queueCount = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  // 1/32 Microstepping Setup (from your original file)
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, HIGH); // Start Disabled
  Serial.println(F("READY"));
}

void loop() {
  // Always check for new data
  if (Serial.available()) handleSerial();

  // Execute queue if not paused by user
  if (!paused && queueCount > 0) {
    executeNextCommand();
  }
}

void handleSerial() {
  while (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) continue;

    // --- IMMEDIATE COMMANDS ---
    if (cmd.equalsIgnoreCase("PAUSE")) {
      paused = true;
      digitalWrite(enPin, HIGH);
      Serial.println(F("Paused"));
    }
    else if (cmd.equalsIgnoreCase("RESUME") || cmd.equalsIgnoreCase("R")) {
      paused = false;
      digitalWrite(enPin, LOW);
      Serial.println(F("Resumed"));
    }
    else if (cmd.equalsIgnoreCase("CLEAR")) {
      queueHead = queueTail = queueCount = 0;
      Serial.println(F("Cleared"));
    }
    // --- QUEUED COMMANDS ---
    else if (cmd.startsWith("G1")) {
      parseAndEnqueueMove(cmd);
    }
    else if (cmd.startsWith("M0") || cmd.startsWith("m0")) {
      enqueuePause();
    }
  }
}

void enqueuePause() {
  if (queueCount < MAX_QUEUE_SIZE) {
    queue[queueTail].type = CMD_WAIT;
    queue[queueTail].armSteps = 0;
    queue[queueTail].baseSteps = 0;
    queue[queueTail].speed = 0;
    
    queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
    queueCount++;
  } else {
    Serial.println(F("ERROR:OVERFLOW"));
  }
}

void parseAndEnqueueMove(String cmd) {
  // Expected Format: G1 <Elbow> <Base> <Speed>
  long armSteps = 0;
  long baseSteps = 0;
  int speed = 1000;

  int s1 = cmd.indexOf(' ');
  int s2 = cmd.indexOf(' ', s1 + 1);
  int s3 = cmd.indexOf(' ', s2 + 1);

  if (s1 > 0) {
    // Parse Elbow (Arm)
    armSteps = cmd.substring(s1 + 1, s2).toInt();
    
    if (s2 > 0) {
      if (s3 > 0) {
        // Parse Base & Speed
        baseSteps = cmd.substring(s2 + 1, s3).toInt();
        speed = cmd.substring(s3 + 1).toInt();
      } else {
        // Parse Base only (default speed)
        baseSteps = cmd.substring(s2 + 1).toInt();
      }
    }
  }

  if (queueCount < MAX_QUEUE_SIZE) {
    queue[queueTail].type = CMD_MOVE;
    queue[queueTail].armSteps = armSteps;
    queue[queueTail].baseSteps = baseSteps;
    queue[queueTail].speed = speed;
    
    queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
    queueCount++;
  } else {
    Serial.println(F("ERROR:OVERFLOW"));
  }
}

void executeNextCommand() {
  GCommand cmd = queue[queueHead];
  queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
  queueCount--;
  
  if (cmd.type == CMD_MOVE) {
    moveBresenham(cmd.armSteps, cmd.baseSteps, cmd.speed);
    Serial.println(F("Done")); // Signal to Python that we are ready for more
  } 
  else if (cmd.type == CMD_WAIT) {
    performProgrammedPause();
  }
}

// Blocks everything until "R" or "RESUME" is received
void performProgrammedPause() {
  Serial.println(F("Done")); // Tell Python the "M0" command is processed so it doesn't timeout
  // NOTE: Depending on your Python script, you might want to send a specific status here.
  // But usually sending "Done" satisfies the script to wait.
  
  digitalWrite(enPin, HIGH); // Disable motors (Pause state)
  
  bool waiting = true;
  while (waiting) {
    if (Serial.available()) {
      String input = Serial.readStringUntil('\n');
      input.trim();
      if (input.equalsIgnoreCase("R") || input.equalsIgnoreCase("RESUME")) {
        waiting = false;
        digitalWrite(enPin, LOW); // Re-enable motors
        // Serial.println(F("Resumed")); // Optional debug
      }
      // Handle immediate stop even during M0
      if (input.equalsIgnoreCase("PAUSE")) {
        paused = true; 
      }
    }
    delay(50);
  }
}

void moveBresenham(long da, long db, int delayUs) {
  digitalWrite(enPin, LOW); // Enable motors

  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);

  long ad = abs(da);
  long bd = abs(db);
  
  long steps = max(ad, bd); 

  long accA = steps / 2;
  long accB = steps / 2;

  for (long i = 0; i < steps; i++) {
    // Check Serial frequently to prevent buffer overflow
    if (Serial.available()) handleSerial();
    
    // Immediate pause check
    if (paused) break;

    accA -= ad;
    if (accA < 0) {
      pulsePin(stepArm);
      accA += steps;
    }

    accB -= bd;
    if (accB < 0) {
      pulsePin(stepBase);
      accB += steps;
    }
    
    delayMicroseconds(delayUs);
  }
}

void pulsePin(int pin) {
  digitalWrite(pin, HIGH);
  delayMicroseconds(2); 
  digitalWrite(pin, LOW);
}
