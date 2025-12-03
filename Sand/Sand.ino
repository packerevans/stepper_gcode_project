// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v7
//      G0 Homing + Debug Prints
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
const float ARM_BASE_RATIO = 1.125;

// Homing Speed for G0
const int HOMING_SPEED = 1000;

// --- STATE VARIABLES ---
bool paused = true; 
const int MAX_QUEUE_SIZE = 50;

// Track the theoretical physical position of the arm
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
  
  currentArmAngleSteps = 0.0;
  
  Serial.println(F("READY"));
}

void loop() {
  if (Serial.available()) handleSerial();

  if (!paused && queueCount > 0) {
    executeNextCommand();
  }
}

void handleSerial() {
  while (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) continue;

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
      currentArmAngleSteps = 0.0; 
      Serial.println(F("Cleared"));
    }
    else if (cmd.startsWith("G1") || cmd.startsWith("g1")) {
      parseAndEnqueueMove(cmd);
    }
    else if (cmd.startsWith("G0") || cmd.startsWith("g0")) {
      Serial.println(F("DEBUG: G0 Received")); // <--- DEBUG 1
      enqueueHome();
    }
  }
}

void enqueueHome() {
  if (queueCount < MAX_QUEUE_SIZE) {
    // Print current tracking info
    Serial.print(F("DEBUG: Current Arm Pos: ")); 
    Serial.println(currentArmAngleSteps);

    // Calculate correction
    long correctionSteps = (long)round(-currentArmAngleSteps);
    
    Serial.print(F("DEBUG: Correcting by: ")); 
    Serial.println(correctionSteps);

    // Add to queue
    queue[queueTail].armSteps = correctionSteps;
    queue[queueTail].baseSteps = 0; 
    queue[queueTail].speed = HOMING_SPEED;
    
    // Update tracking (should now be 0)
    currentArmAngleSteps += correctionSteps;
    
    queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
    queueCount++;
  } else {
    Serial.println(F("ERROR:OVERFLOW"));
  }
}

void parseAndEnqueueMove(String cmd) {
  long armSteps = 0;
  long baseSteps = 0;
  int speed = 1000;

  int s1 = cmd.indexOf(' ');
  int s2 = cmd.indexOf(' ', s1 + 1);
  int s3 = cmd.indexOf(' ', s2 + 1);

  if (s1 > 0) {
    String armStr = cmd.substring(s1 + 1, s2 > 0 ? s2 : cmd.length());
    armSteps = armStr.toInt();
    
    if (s2 > 0) {
      if (s3 > 0) {
        baseSteps = cmd.substring(s2 + 1, s3).toInt();
        speed = cmd.substring(s3 + 1).toInt();
      } else {
        baseSteps = cmd.substring(s2 + 1).toInt();
      }
    }
  }

  if (queueCount < MAX_QUEUE_SIZE) {
    queue[queueTail].armSteps = armSteps;
    queue[queueTail].baseSteps = baseSteps;
    queue[queueTail].speed = speed;
    
    // --- TRACKING LOGIC ---
    // Update tracking BEFORE move execution so we know where we are going
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
  
  moveBresenham(cmd.armSteps, cmd.baseSteps, cmd.speed);
  Serial.println(F("Done")); 
}

void moveBresenham(long da, long db, int delayUs) {
  digitalWrite(enPin, LOW); 

  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);

  long ad = abs(da);
  long bd = abs(db);
  
  long steps = max(ad, bd); 

  long accA = steps / 2;
  long accB = steps / 2;

  for (long i = 0; i < steps; i++) {
    if (Serial.available()) handleSerial();
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


