// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v6
//      G0 Homing + Arm Tracking
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

// Gear Ratio: "Arm moves 1.125 of base"
// Based on your example: G1 -700 800 -> Correction 1600
// Math: -700 - (800 * 1.125) = -1600. Correction = +1600.
const float ARM_BASE_RATIO = 1.125;

// Homing Speed for G0
const int HOMING_SPEED = 1000;

// --- STATE VARIABLES ---
bool paused = true; // Still used for manual "PAUSE" command, but not M0
const int MAX_QUEUE_SIZE = 50;

// Track the theoretical physical position of the arm
// Start at 0.0 assuming arm is homed on power up
double currentArmAngleSteps = 0.0;

struct GCommand {
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
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, HIGH); // Start Disabled
  
  // Initialize position tracking
  currentArmAngleSteps = 0.0;
  
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
      currentArmAngleSteps = 0.0; // Reset tracking on clear? Optional.
      Serial.println(F("Cleared"));
    }
    // --- QUEUED COMMANDS ---
    else if (cmd.startsWith("G1") || cmd.startsWith("g1")) {
      parseAndEnqueueMove(cmd);
    }
    else if (cmd.startsWith("G0") || cmd.startsWith("g0")) {
      // G0 is now the Homing Command
      enqueueHome();
    }
  }
}

void enqueueHome() {
  if (queueCount < MAX_QUEUE_SIZE) {
    // Calculate steps needed to return to 0
    // If current is -1600, we need +1600.
    long correctionSteps = (long)round(-currentArmAngleSteps);
    
    // Add to queue
    queue[queueTail].armSteps = correctionSteps;
    queue[queueTail].baseSteps = 0; // Base does not move during homing
    queue[queueTail].speed = HOMING_SPEED;
    
    // Update tracking: We are theoretically back at 0 after this move
    currentArmAngleSteps += correctionSteps;
    
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
    String armStr = cmd.substring(s1 + 1, s2 > 0 ? s2 : cmd.length());
    armSteps = armStr.toInt();
    
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
    queue[queueTail].armSteps = armSteps;
    queue[queueTail].baseSteps = baseSteps;
    queue[queueTail].speed = speed;
    
    // --- TRACKING LOGIC ---
    // Update the virtual arm position based on the move + gear ratio
    // Based on user data: Net Change = ArmSteps - (BaseSteps * 1.125)
    currentArmAngleSteps += armSteps;
    currentArmAngleSteps -= (baseSteps * ARM_BASE_RATIO);

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
  
  // Execute Move
  moveBresenham(cmd.armSteps, cmd.baseSteps, cmd.speed);
  Serial.println(F("Done")); 
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

