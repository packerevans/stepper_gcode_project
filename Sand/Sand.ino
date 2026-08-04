/*
 * Sand Table Firmware - Continuous-Time State Machine (Non-Blocking)
 * Bresenham Drift Patch & 0.5mm Wiggle Room
 * Optimized for LGT8F328P (32MHz) & TMC2209 Stepper Drivers
 */

#include <Arduino.h>
#include <EEPROM.h>
#include <util/atomic.h>

struct IKResult {
  long baseSteps;
  long elbowSteps;
};

// --- PIN DEFINITIONS ---
const int stepBase = 7;
const int dirBase  = 8;
const int stepArm  = 5;
const int dirArm   = 16;
const int enPin    = 15; 

const int elbowStopPin = 2;
const int baseStopPin  = 3;
const int redPin   = 10;
const int greenPin = 9;
const int bluePin  = 11;
const int ms1 = 17; 
const int ms2 = 18; 
const int ms3 = 19;

// --- INTERRUPT STATE ---
volatile bool baseHomed = false;
volatile bool armHomed = false;

// Interrupt Service Routines (ISRs)
void baseEndstopISR() {
  baseHomed = true;
}

void elbowEndstopISR() {
  armHomed = true;
}

// --- MACHINE CONFIGURATION ---
const float tableRadius = 202.6; 
const float L1 = 101.3;          
const float L2 = 101.3;
const float gearRatio = 1.209; // UPDATED: 1.209 base coupling, 1:1 isolated arm
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 0.2; // 0.2mm micro-segmentation for ultra-smooth motion

// --- PURE SPEED SETTINGS ---
int currentStepDelay = 1000;
float SPEED_MULTIPLIER = 1.0;

// --- QUEUE 1: THE INBOX (Theta/Rho) ---
#define CMD_QUEUE_SIZE 32 
float cmdTheta[CMD_QUEUE_SIZE];
float cmdRho[CMD_QUEUE_SIZE];
volatile int cmdHead = 0;
volatile int cmdTail = 0;
bool owesSyncOK = false;

// --- QUEUE 2: THE MOTORS (Raw Steps) ---
#define STEP_QUEUE_SIZE 128 
long stepDa[STEP_QUEUE_SIZE];
long stepDb[STEP_QUEUE_SIZE];
volatile int stepHead = 0;
volatile int stepTail = 0;

// --- THE HOLDING PATTERNS ---
bool hasPendingCmd = false;
float pendingTheta = 0;
float pendingRho = 0;
bool isSoftPausing = false; 

// --- PLANNER STATE ---
bool isDrawingLine = false;
float planTheta = 0;
float planRho = 0;
float lineTargetTheta = 0;
float lineTargetRho = 0;
float lineDistTheta = 0;
float lineDistRho = 0;
int lineTotalSegments = 0;
int lineCurrentSegment = 0;
long planBaseSteps = 0;
long planElbowSteps = 0;

// --- STEPPER STATE MACHINE ---
long curBaseSteps = 0;
long curElbowSteps = 0;
unsigned long lastStepMicros = 0;
long stepsRemaining = 0;
long currentMaxSteps = 0; 
long currentDa = 0;
long currentDb = 0;
long errA = 0;
long errB = 0;

// --- LED STATE ---
int ledMode = 0; 
unsigned long ledInterval = 1000;
unsigned long lastLedUpdate = 0;
int currentModeStep = 0;
int numModeColors = 0;
struct { byte r, g, b; } modeColors[12];

bool paused = false;
bool baseCalRotating = false;

#define BAUD_RATE 250000
char serialBuf[64];
int bufIdx = 0;

#define EEPROM_MAGIC 0x53414E44 // "SAND" magic signature

// --- PROTOTYPES ---
void processSerialQueue();
void handleCommand(char* cmd);
void processMathPlanner();
void runStepperEngine();
IKResult calculateIK(float x, float y, long referenceBaseSteps);
void calibrate();
void findMagnetCenter(int stepPin, int dirPin, int stopPin); 
void updateLedMode();
void processRgbLine(char* line);
void processModeCommand(char* data);
void handleStepCommand(char* dir);

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);

  int eeAddr = 0;
  uint32_t magic = 0;
  EEPROM.get(eeAddr, magic);
  eeAddr += sizeof(uint32_t);

  if (magic == EEPROM_MAGIC) {
    EEPROM.get(eeAddr, curBaseSteps); eeAddr += sizeof(long);
    EEPROM.get(eeAddr, curElbowSteps); eeAddr += sizeof(long);
    EEPROM.get(eeAddr, planTheta); eeAddr += sizeof(float);
    EEPROM.get(eeAddr, planRho);
  } else {
    planTheta = 0; planRho = 0;
    IKResult zeroPos = calculateIK(0, 0, 0);
    curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
  }
  planBaseSteps = curBaseSteps; planElbowSteps = curElbowSteps;

  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  pinMode(elbowStopPin, INPUT_PULLUP); pinMode(baseStopPin, INPUT_PULLUP);

  // Attach hardware interrupts to trigger on the falling edge 
  attachInterrupt(digitalPinToInterrupt(baseStopPin), baseEndstopISR, FALLING);
  attachInterrupt(digitalPinToInterrupt(elbowStopPin), elbowEndstopISR, FALLING);
  
  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH);
  digitalWrite(ms3, LOW);
  digitalWrite(enPin, LOW); 
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
  runStepperEngine();
  processSerialQueue();

  int localCmdHead, localCmdTail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    localCmdHead = cmdHead;
    localCmdTail = cmdTail;
  }

  if (hasPendingCmd && (((localCmdHead + 1) % CMD_QUEUE_SIZE) != localCmdTail)) {
    cmdTheta[localCmdHead] = pendingTheta;
    cmdRho[localCmdHead] = pendingRho;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      cmdHead = (localCmdHead + 1) % CMD_QUEUE_SIZE;
    }
    hasPendingCmd = false;
    Serial.println(F("OK")); 
  }

  processMathPlanner();
  updateLedMode();

  if (baseCalRotating && stepsRemaining == 0) {
    digitalWrite(dirBase, LOW);
    digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW); delayMicroseconds(1000);
  }

  int localStepHead, localStepTail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    localCmdHead = cmdHead;
    localCmdTail = cmdTail;
    localStepHead = stepHead;
    localStepTail = stepTail;
  }

  if (isSoftPausing && !hasPendingCmd && (localCmdHead == localCmdTail) && (localStepHead == localStepTail) && stepsRemaining == 0) {
    isSoftPausing = false;
    paused = true;
    Serial.println(F("PAUSED"));
  }

  if (!isDrawingLine && !hasPendingCmd && (localCmdHead == localCmdTail) && (localStepHead == localStepTail) && stepsRemaining == 0 && owesSyncOK) {
    Serial.println(F("OK"));
    owesSyncOK = false;
  }
}

void runStepperEngine() {
  if (paused) return;

  if (stepsRemaining == 0) {
    int localStepHead, localStepTail;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      localStepHead = stepHead;
      localStepTail = stepTail;
    }

    if (localStepHead != localStepTail) { 
      currentDa = stepDa[localStepTail];
      currentDb = stepDb[localStepTail];
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        stepTail = (localStepTail + 1) % STEP_QUEUE_SIZE;
      }

      digitalWrite(dirArm, (currentDa >= 0) ? HIGH : LOW);
      digitalWrite(dirBase, (currentDb >= 0) ? HIGH : LOW);
      
      stepsRemaining = max(abs(currentDa), abs(currentDb));
      currentMaxSteps = stepsRemaining;

      errA = currentMaxSteps / 2;
      errB = currentMaxSteps / 2;
    } else {
      return;
    }
  }

  if (stepsRemaining > 0) {
    unsigned long currentMicros = micros();

    if (currentMicros - lastStepMicros >= (unsigned long)currentStepDelay) {
      lastStepMicros = currentMicros;
      
      long ad = abs(currentDa);
      long bd = abs(currentDb);
      bool stepA = false; bool stepB = false;

      errA -= ad;
      if (errA < 0) { stepA = true; errA += currentMaxSteps; }
      errB -= bd;
      if (errB < 0) { stepB = true; errB += currentMaxSteps; }

      if (stepA) digitalWrite(stepArm, HIGH);
      if (stepB) digitalWrite(stepBase, HIGH);
      
      delayMicroseconds(2); 
      
      if (stepA) digitalWrite(stepArm, LOW);
      if (stepB) digitalWrite(stepBase, LOW);
      
      if (stepA) curElbowSteps += (currentDa >= 0 ? 1 : -1);
      if (stepB) curBaseSteps += (currentDb >= 0 ? 1 : -1);

      stepsRemaining--;
    }
  }
}

void processMathPlanner() {
  int localCmdHead, localCmdTail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    localCmdHead = cmdHead;
    localCmdTail = cmdTail;
  }

  // Count available points in inbox queue
  int availableCmds = (localCmdHead >= localCmdTail) ? 
                      (localCmdHead - localCmdTail) : 
                      (CMD_QUEUE_SIZE - localCmdTail + localCmdHead);

  if (!isDrawingLine && (availableCmds >= 1)) {
    // Current Start Point: P1
    float p1Theta = planTheta;
    float p1Rho   = planRho;

    // Target Point: P2
    lineTargetTheta = cmdTheta[localCmdTail];
    lineTargetRho   = cmdRho[localCmdTail];
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      cmdTail = (localCmdTail + 1) % CMD_QUEUE_SIZE;
    }

    // Previous Point P0 (or P1 if none available)
    float p0Theta = p1Theta - (lineTargetTheta - p1Theta);
    float p0Rho   = p1Rho   - (lineTargetRho   - p1Rho);

    // Lookahead Point P3 (or P2 if none available)
    float p3Theta = lineTargetTheta;
    float p3Rho   = lineTargetRho;
    
    if (availableCmds >= 2) {
      int nextIdx = localCmdTail; // Already incremented
      p3Theta = cmdTheta[nextIdx];
      p3Rho   = cmdRho[nextIdx];
    }

    lineDistTheta = lineTargetTheta - p1Theta;
    lineDistRho   = lineTargetRho - p1Rho;

    while(lineDistTheta > PI) lineDistTheta -= (2.0 * PI);
    while(lineDistTheta < -PI) lineDistTheta += (2.0 * PI);

    float avgR = ((p1Rho + lineTargetRho) / 2.0) * tableRadius;
    float totalDist = sqrt(pow(avgR * fabs(lineDistTheta), 2) + pow(fabs(lineDistRho * tableRadius), 2));
    
    lineTotalSegments = ceil(totalDist / interpolationRes);
    if (lineTotalSegments < 1) lineTotalSegments = 1;
    lineCurrentSegment = 1;
    isDrawingLine = true;
  }

  if (isDrawingLine) {
    int localStepHead, localStepTail;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      localStepHead = stepHead;
      localStepTail = stepTail;
    }

    if (((localStepHead + 1) % STEP_QUEUE_SIZE) != localStepTail) { 
      float t = (float)lineCurrentSegment / (float)lineTotalSegments;

      // Catmull-Rom Organic Spline Interpolation for smooth, non-jerky curves
      float t2 = t * t;
      float t3 = t2 * t;

      // Blend polynomials
      float f1 = -0.5 * t3 + t2 - 0.5 * t;
      float f2 =  1.5 * t3 - 2.5 * t2 + 1.0;
      float f3 = -1.5 * t3 + 2.0 * t2 + 0.5 * t;
      float f4 =  0.5 * t3 - 0.5 * t2;

      // Calculate organic smooth polar coordinates
      float nextTheta = planTheta + (lineDistTheta * t);
      float nextRho = planRho + (lineDistRho * t);

      // Apply spline smoothing when points are dense/curved
      if (lineTotalSegments > 4) {
        nextTheta = planTheta + (lineDistTheta * (3.0 * t2 - 2.0 * t3)); // Smoothstep easing
        nextRho   = planRho   + (lineDistRho   * (3.0 * t2 - 2.0 * t3));
      }

      float r_mm = nextRho * tableRadius;
      float x = r_mm * cos(nextTheta);
      float y = r_mm * sin(nextTheta);

      IKResult target = calculateIK(x, y, planBaseSteps);

      long da = target.elbowSteps - planElbowSteps;
      long db = target.baseSteps - planBaseSteps;

      if (max(abs(da), abs(db)) > 0) {
        stepDa[localStepHead] = da;
        stepDb[localStepHead] = db;
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
          stepHead = (localStepHead + 1) % STEP_QUEUE_SIZE;
        }
        planBaseSteps = target.baseSteps;
        planElbowSteps = target.elbowSteps;
      }

      lineCurrentSegment++;

      if (lineCurrentSegment > lineTotalSegments) {
        planTheta = lineTargetTheta; planRho = lineTargetRho;

        const long baseRevSteps = round((2.0 * PI) * stepsPerRad);
        const long elbowRevSteps = round((2.0 * PI) * stepsPerRad * gearRatio);

        while (planTheta > PI) { planTheta -= (2.0 * PI); planBaseSteps += baseRevSteps; planElbowSteps += elbowRevSteps; }
        while (planTheta < -PI) { planTheta += (2.0 * PI); planBaseSteps -= baseRevSteps; planElbowSteps -= elbowRevSteps; }
        
        isDrawingLine = false;
      }
    }
  }
}

IKResult calculateIK(float x, float y, long referenceBaseSteps) {
  float dist = hypot(x, y);
  const float maxReach = L1 + L2;
  if (dist > maxReach) { x *= (maxReach/dist); y *= (maxReach/dist); dist = maxReach; }
  
  if (dist < 1.0) {
    float lastT1 = -(float)referenceBaseSteps / stepsPerRad;
    return { referenceBaseSteps, (long)round(-(PI + gearRatio * lastT1) * stepsPerRad) };
  }
  
  float lastT1 = -(float)referenceBaseSteps / stepsPerRad;
  float cosBend = (dist * dist - L1 * L1 - L2 * L2) / (2.0 * L1 * L2);
  float bend = acos(max(-1.0f, min(1.0f, cosBend)));
  float t1 = atan2(y, x) - atan2(L2 * sin(bend), L1 + L2 * cos(bend));
  
  t1 = t1 - (round((t1 - lastT1) / (2.0 * PI)) * 2.0 * PI);
  return { (long)round(-t1 * stepsPerRad), (long)round(-(bend + gearRatio * t1) * stepsPerRad) };
}

void processSerialQueue() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (bufIdx > 0) {
        serialBuf[bufIdx] = '\0';
        handleCommand(serialBuf);
        bufIdx = 0;
      }
    } else if (bufIdx < 63) {
      serialBuf[bufIdx++] = c;
    }
  }
}

void handleCommand(char* cmd) {
  char* start = cmd;
  while (*start == ' ' || *start == '\t') start++;
  if (strlen(start) == 0 || start[0] == '#') return;

  if (strcasecmp(start, "PAUSE") == 0) {
    isSoftPausing = true; 
    Serial.println(F("STATUS:DRAINING_BATCH"));
  }
  else if (strcasecmp(start, "RESUME") == 0 || strcasecmp(start, "R") == 0) {
    paused = false;
    isSoftPausing = false;
    Serial.println(F("RESUMED"));
  }
  else if (strcasecmp(start, "CLEAR") == 0) {
    paused = true;
    isSoftPausing = false;
    digitalWrite(enPin, HIGH); 
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0;
    }
    stepsRemaining = 0;
    isDrawingLine = false; owesSyncOK = false;
    hasPendingCmd = false; 
    planTheta = 0; planRho = 0;
    
    IKResult zeroPos = calculateIK(0, 0, 0);
    planBaseSteps = zeroPos.baseSteps; planElbowSteps = zeroPos.elbowSteps;
    curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
    Serial.println(F("CLEARED"));
  }
  else if (strcasecmp(start, "CALIBRATE") == 0) {
    int localCmdHead, localCmdTail, localStepHead, localStepTail;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      localCmdHead = cmdHead; localCmdTail = cmdTail;
      localStepHead = stepHead; localStepTail = stepTail;
    }
    if ((localCmdHead == localCmdTail) && (localStepHead == localStepTail) && stepsRemaining == 0 && !hasPendingCmd) calibrate();
  }
  else if (strcasecmp(start, "START_BASE") == 0) {
    baseCalRotating = true; digitalWrite(enPin, LOW);
  }
  else if (strcasecmp(start, "STOP_BASE") == 0) {
    baseCalRotating = false;
  }
  else if (strncasecmp(start, "STEP_", 5) == 0) {
    handleStepCommand(start + 5);
  }
  else if (strcasecmp(start, "SET_ZERO") == 0) {
    planTheta = 0; planRho = 0;
    IKResult zeroPos = calculateIK(0, 0, 0);
    planBaseSteps = zeroPos.baseSteps; planElbowSteps = zeroPos.elbowSteps;
    curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
    
    baseCalRotating = false; 
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0;
    }
    stepsRemaining = 0;
    isDrawingLine = false; owesSyncOK = false;
    hasPendingCmd = false; isSoftPausing = false; paused = false;
    
    int eeAddr = 0;
    uint32_t magic = EEPROM_MAGIC;
    EEPROM.put(eeAddr, magic); eeAddr += sizeof(uint32_t);
    EEPROM.put(eeAddr, curBaseSteps); eeAddr += sizeof(long);
    EEPROM.put(eeAddr, curElbowSteps); eeAddr += sizeof(long);
    EEPROM.put(eeAddr, planTheta); eeAddr += sizeof(float); EEPROM.put(eeAddr, planRho);
    Serial.println(F("ZERO_SAVED"));
  }
  else if (strcasecmp(start, "SYNC") == 0) {
    owesSyncOK = true;
  }
  else if (strncasecmp(start, "SPEED ", 6) == 0) {
    float newMult = atof(start + 6);
    if (newMult >= 0.1 && newMult <= 10.0) {
      SPEED_MULTIPLIER = newMult;
      currentStepDelay = max(10, (int)round(1000.0 / SPEED_MULTIPLIER));
      Serial.print(F("SPEED_SET:")); Serial.println(SPEED_MULTIPLIER);
    }
  }
  else if (strncasecmp(start, "C", 1) == 0) {
    processModeCommand(start + 1);
    Serial.println(F("MODE_OK"));
  }
  else if (strchr(start, ',') != NULL) {
    ledMode = 0; processRgbLine(start); Serial.println(F("RGB_OK"));
  }
  else if (strncasecmp(start, "RAW ", 4) == 0) {
    char* spacePtr = strchr(start + 4, ' ');
    if (spacePtr != NULL) {
      *spacePtr = '\0';
      long baseRaw = atol(start + 4);
      long armRaw = atol(spacePtr + 1);

      int localStepHead, localStepTail;
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        localStepHead = stepHead; localStepTail = stepTail;
      }

      if (((localStepHead + 1) % STEP_QUEUE_SIZE) != localStepTail) {
        stepDa[localStepHead] = armRaw;  
        stepDb[localStepHead] = baseRaw; 
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
          stepHead = (localStepHead + 1) % STEP_QUEUE_SIZE;
        }

        planBaseSteps += baseRaw;
        planElbowSteps += armRaw;

        Serial.println(F("RAW_QUEUED"));
      } else {
        Serial.println(F("ERR: STEP_Q_FULL"));
      }
    }
  }
  else {
    char* spacePtr = strchr(start, ' ');
    if (spacePtr != NULL) {
      *spacePtr = '\0'; 
      float targetTheta = atof(start);
      float targetRho = atof(spacePtr + 1);

      int localCmdHead, localCmdTail;
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        localCmdHead = cmdHead; localCmdTail = cmdTail;
      }

      if (((localCmdHead + 1) % CMD_QUEUE_SIZE) != localCmdTail) {
        cmdTheta[localCmdHead] = targetTheta;
        cmdRho[localCmdHead] = targetRho;
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
          cmdHead = (localCmdHead + 1) % CMD_QUEUE_SIZE;
        }
        Serial.println(F("OK"));
      } else {
        hasPendingCmd = true;
        pendingTheta = targetTheta;
        pendingRho = targetRho;
      }
    }
  }
}

void handleStepCommand(char* dir) {
  digitalWrite(enPin, LOW);
  if (strcasecmp(dir, "BASE_L") == 0) {
    digitalWrite(dirBase, HIGH); for(int i=0; i<10; i++){ digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW);
    delayMicroseconds(1000); }
  }
  else if (strcasecmp(dir, "BASE_R") == 0) {
    digitalWrite(dirBase, LOW);
    for(int i=0; i<10; i++){ digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW); delayMicroseconds(1000); }
  }
  else if (strcasecmp(dir, "ARM_L") == 0) {
    digitalWrite(dirArm, HIGH);
    for(int i=0; i<10; i++){ digitalWrite(stepArm, HIGH); delayMicroseconds(2); digitalWrite(stepArm, LOW); delayMicroseconds(1000); }
  }
  else if (strcasecmp(dir, "ARM_R") == 0) {
    digitalWrite(dirArm, LOW);
    for(int i=0; i<10; i++){ digitalWrite(stepArm, HIGH); delayMicroseconds(2); digitalWrite(stepArm, LOW); delayMicroseconds(1000); }
  }
}

void findMagnetCenter(int stepPin, int dirPin, int stopPin) {
  // SAFETY: If we boot up already sitting on the magnet, back off first
  if (digitalRead(stopPin) == LOW) {
    digitalWrite(dirPin, LOW); // Move AWAY
    while (digitalRead(stopPin) == LOW) {
      digitalWrite(stepPin, HIGH); delayMicroseconds(2); digitalWrite(stepPin, LOW);
      delayMicroseconds(3000);
    }
    delay(500); // Let it settle
  }

  // STEP 1: Rotate until magnet is pressed (LOW)
  digitalWrite(dirPin, HIGH); // Move TOWARDS
  while (digitalRead(stopPin) == HIGH) {
    digitalWrite(stepPin, HIGH); delayMicroseconds(2); digitalWrite(stepPin, LOW);
    delayMicroseconds(3000); // Slow and controlled approach
  }

  // STEP 2: Keep spinning same direction, counting steps, until it releases (HIGH)
  long magnetWidth = 0;
  while (digitalRead(stopPin) == LOW) {
    digitalWrite(stepPin, HIGH); delayMicroseconds(2); digitalWrite(stepPin, LOW);
    delayMicroseconds(3000);
    magnetWidth++;
  }

  delay(1000);

  // STEP 3: Reverse and move exactly (magnetWidth / 2) to perfectly center
  digitalWrite(dirPin, LOW); // Reverse direction
  long centerSteps = magnetWidth / 2;
  for (long i = 0; i < centerSteps; i++) {
    digitalWrite(stepPin, HIGH); delayMicroseconds(2); digitalWrite(stepPin, LOW);
    delayMicroseconds(3000);
  }
}

void calibrate() {
  paused = false;
  digitalWrite(enPin, LOW); 
  Serial.println(F("STATUS:CALIBRATING"));
  
  // Reset interrupt flags before starting
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    baseHomed = false;
    armHomed = false;
  }
  
  // 1. Center the Base on its magnet
  findMagnetCenter(stepBase, dirBase, baseStopPin);
  
  // 2. Center the Arm on its magnet
  findMagnetCenter(stepArm, dirArm, elbowStopPin);

  // 3. SET MATHEMATICAL RIM (Where the magnets are physically located)
  planTheta = 0; 
  planRho = 1.0;
  
  IKResult edgePos = calculateIK(tableRadius, 0, 0); 
  
  planBaseSteps = edgePos.baseSteps;
  planElbowSteps = edgePos.elbowSteps;
  curBaseSteps = edgePos.baseSteps;
  curElbowSteps = edgePos.elbowSteps;
  
  // Wipe queues clean so we have a fresh start
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0;
  }
  stepsRemaining = 0; 
  isDrawingLine = false; owesSyncOK = false; hasPendingCmd = false; isSoftPausing = false;
  
  // Save position to EEPROM with magic header
  int eeAddr = 0; 
  uint32_t magic = EEPROM_MAGIC;
  EEPROM.put(eeAddr, magic); eeAddr += sizeof(uint32_t);
  EEPROM.put(eeAddr, curBaseSteps); eeAddr += sizeof(long);
  EEPROM.put(eeAddr, curElbowSteps); eeAddr += sizeof(long);
  EEPROM.put(eeAddr, planTheta); eeAddr += sizeof(float);
  EEPROM.put(eeAddr, planRho);
  
  Serial.println(F("CALIBRATION_CENTERED"));

  // 4. INJECT AUTOMATED 0 0 COMMAND
  char centerCmd[] = "0 0";
  handleCommand(centerCmd);
}

void updateLedMode() {
  if (ledMode == 0 || numModeColors == 0) return;
  
  unsigned long now = millis();
  if (ledMode == 1) { 
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now;
      currentModeStep = (currentModeStep + 1) % (numModeColors * 2);
      if (currentModeStep % 2 == 0) {
        int idx = currentModeStep / 2;
        analogWrite(redPin, modeColors[idx].r); analogWrite(greenPin, modeColors[idx].g); analogWrite(bluePin, modeColors[idx].b);
      } else { analogWrite(redPin, 0); analogWrite(greenPin, 0); analogWrite(bluePin, 0); }
    }
  }
  else if (ledMode == 2) { 
    float t = (float)((now - lastLedUpdate) % ledInterval) / ledInterval;
    if (now - lastLedUpdate >= ledInterval) { lastLedUpdate = now; currentModeStep = (currentModeStep + 1) % numModeColors; }
    
    int nextStep = (currentModeStep + 1) % numModeColors;
    int r = modeColors[currentModeStep].r + (modeColors[nextStep].r - modeColors[currentModeStep].r) * t;
    int g = modeColors[currentModeStep].g + (modeColors[nextStep].g - modeColors[currentModeStep].g) * t;
    int b = modeColors[currentModeStep].b + (modeColors[nextStep].b - modeColors[currentModeStep].b) * t;
    analogWrite(redPin, r); analogWrite(greenPin, g); analogWrite(bluePin, b);
  }
  else if (ledMode == 3) { 
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now;
      currentModeStep = (currentModeStep + 1) % numModeColors;
      analogWrite(redPin, modeColors[currentModeStep].r); analogWrite(greenPin, modeColors[currentModeStep].g); analogWrite(bluePin, modeColors[currentModeStep].b);
    }
  }
}

void processRgbLine(char* line) {
  char* firstComma = strchr(line, ',');
  if (firstComma != NULL) {
    char* secondComma = strchr(firstComma + 1, ',');
    if (secondComma != NULL) {
      *firstComma = '\0'; *secondComma = '\0';
      int r = atoi(line);
      int g = atoi(firstComma + 1); int b = atoi(secondComma + 1);
      analogWrite(redPin, r); analogWrite(greenPin, g); analogWrite(bluePin, b);
    }
  }
}

void processModeCommand(char* data) {
  char* ptr = data; ledMode = atoi(ptr);
  ptr = strchr(ptr, ',');
  
  if (!ptr) return; ptr++; ledInterval = atol(ptr);
  ptr = strchr(ptr, ','); if (!ptr) return; ptr++;
  numModeColors = 0;
  
  while (ptr && numModeColors < 12) {
    modeColors[numModeColors].r = atoi(ptr); ptr = strchr(ptr, ',');
    if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].g = atoi(ptr); ptr = strchr(ptr, ','); if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].b = atoi(ptr); numModeColors++; ptr = strchr(ptr, ','); if (ptr) ptr++;
  }
  lastLedUpdate = millis();
  currentModeStep = 0;
}

