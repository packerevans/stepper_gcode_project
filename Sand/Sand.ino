// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v6
//      M0 Removed + G0 (Homing/Rapid) Added
// ==========================================

// --- YOUR SPECIFIC MOTOR PINS ---
const int stepBase = 8;
const int dirBase  = 9;
const int stepArm  = 10;
const int dirArm   = 11;
const int enPin    = 3;

// Microstepping Pins
const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

// --- SETTINGS ---
#define BAUD_RATE 9600 

// --- STATE VARIABLES ---
bool paused = true;
const int MAX_QUEUE_SIZE = 50;

// Command Types
const int CMD_MOVE = 0;
// CMD_WAIT (M0) removed

struct GCommand {
  int type;       // Always 0 (Move) now, kept for structure
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

  // 1/32 Microstepping Setup
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH);
  digitalWrite(ms3, HIGH);

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
    // ADDED: G0 Command Handling
    // Interprets G0 as a move to the parameters provided (e.g., G0 0 0)
    else if (cmd.startsWith("G0") || cmd.startsWith("g0")) {
      parseAndEnqueueMove(cmd); 
    }
    // REMOVED: M0 / m0 handler
  }
}

// NOTE: enqueuePause() removed as M0 feature is deleted.

void parseAndEnqueueMove(String cmd) {
  // Expected Format: G1/G0 <Elbow> <Base> <Speed>
  long armSteps = 0;
  long baseSteps = 0;
  int speed = 1000; // Default delay (lower is faster)

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
        // Parse Base only (use default speed)
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
    Serial.println(F("Done")); // Signal to Python
  } 
  // REMOVED: CMD_WAIT (M0) handling
}

// NOTE: performProgrammedPause() removed.

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
    // Check Serial frequently
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
