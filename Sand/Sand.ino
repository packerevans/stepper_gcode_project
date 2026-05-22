/*
 * Sand Table Firmware - Constant Cartesian Feedrate Profiling
 * Bresenham Drift Patch, Zero-Drift Spiral Fix & Auto-Centering Magnetic Homing
 * Optimized for LGT8F328P (32MHz) & TMC2209 Stepper Drivers
 */

#include <Arduino.h>
#include <EEPROM.h>

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
const float gearRatio = 1.209; // 1.209 base coupling, 1:1 isolated arm
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 0.5; // Micro-segmentation in mm

// --- KINEMATIC SPEED PROFILING ---
float targetCartesianSpeed = 35.0;  // Baseline target speed of the ball (mm/second)
float SPEED_MULTIPLIER = 1.0;       // Dynamic modifier (changed via GCODE/Commands)
const unsigned int MIN_STEP_DELAY = 400; // Hardware limit: Minimum microseconds between steps to prevent motor stall

// --- QUEUE 1: THE INBOX (Theta/Rho) ---
#define CMD_QUEUE_SIZE 32 
float cmdTheta[CMD_QUEUE_SIZE];
float cmdRho[CMD_QUEUE_SIZE];
volatile int cmdHead = 0;
volatile int cmdTail = 0;
bool owesSyncOK = false;

// --- QUEUE 2: THE MOTORS (Raw Steps & Delays) ---
#define STEP_QUEUE_SIZE 128 
long stepDa[STEP_QUEUE_SIZE];
long stepDb[STEP_QUEUE_SIZE];
unsigned int stepDelay[STEP_QUEUE_SIZE]; // NEW: Custom microsecond delay for every segment
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
unsigned int currentStepDelay = 1000;
long stepsRemaining = 0;
long currentMaxSteps = 0; // Locks the Bresenham math
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

// --- PROTOTYPES ---
void processSerialQueue();
void handleCommand(char* cmd);
void processMathPlanner();
void runStepperEngine();
IKResult calculateIK(float x, float y, long referenceBaseSteps);
void calibrate();
void findMagneticCenter(int stepPin, int dirPin, int stopPin, int delayUs);
void pulseStepper(int stepPin, int delayUs);
void updateLedMode();
void processRgbLine(char* line);
void processModeCommand(char* data);
void handleStepCommand(char* dir);

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);

  int eeAddr = 0;
  EEPROM.get(eeAddr, curBaseSteps); eeAddr += sizeof(long);
  EEPROM.get(eeAddr, curElbowSteps); eeAddr += sizeof(long);
  EEPROM.get(eeAddr, planTheta); eeAddr += sizeof(float);
  EEPROM.get(eeAddr, planRho);

  if (curBaseSteps == -1 && curElbowSteps == -1) {
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
  
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, LOW);
  digitalWrite(enPin, LOW); 
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
  runStepperEngine();
  processSerialQueue();

  if (hasPendingCmd && (((cmdHead + 1) % CMD_QUEUE_SIZE) != cmdTail)) {
    cmdTheta[cmdHead] = pendingTheta;
    cmdRho[cmdHead] = pendingRho;
    cmdHead = (cmdHead + 1) % CMD_QUEUE_SIZE;
    hasPendingCmd = false;
    Serial.println(F("OK")); 
  }

  processMathPlanner();
  updateLedMode();

  if (baseCalRotating && stepsRemaining == 0) {
    digitalWrite(dirBase, LOW);
    digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW); delayMicroseconds(1000);
  }

  if (isSoftPausing && !hasPendingCmd && (cmdHead == cmdTail) && (stepHead == stepTail) && stepsRemaining == 0) {
    isSoftPausing = false;
    paused = true;
    Serial.println(F("PAUSED"));
  }

  if (!isDrawingLine && !hasPendingCmd && (cmdHead == cmdTail) && (stepHead == stepTail) && stepsRemaining == 0 && owesSyncOK) {
    Serial.println(F("OK"));
    owesSyncOK = false;
  }
}

void runStepperEngine() {
  if (paused) return;

  if (stepsRemaining == 0) {
    if (stepHead != stepTail) { 
      // Pull the next segment AND its dynamically calculated speed profile
      currentDa = stepDa[stepTail];
      currentDb = stepDb[stepTail];
      currentStepDelay = stepDelay[stepTail]; 
      
      stepTail = (stepTail + 1) % STEP_QUEUE_SIZE;

      digitalWrite(dirArm, (currentDa >= 0) ? HIGH : LOW);
      digitalWrite(dirBase, (currentDb >= 0) ? HIGH : LOW);
      
      stepsRemaining = max(abs(currentDa), abs(currentDb));
      currentMaxSteps = stepsRemaining;

      // Lock in the total steps for this segment to eliminate tracking drift
      errA = currentMaxSteps / 2;
      errB = currentMaxSteps / 2;
    } else {
      return;
    }
  }

  if (stepsRemaining > 0) {
    unsigned long currentMicros = micros();
    if (currentMicros - lastStepMicros >= currentStepDelay) {
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
  if (!isDrawingLine && (cmdHead != cmdTail)) {
    lineTargetTheta = cmdTheta[cmdTail];
    lineTargetRho = cmdRho[cmdTail];
    cmdTail = (cmdTail + 1) % CMD_QUEUE_SIZE;

    lineDistTheta = lineTargetTheta - planTheta;
    lineDistRho = lineTargetRho - planRho;

    while(lineDistTheta > PI) lineDistTheta -= (2.0 * PI);
    while(lineDistTheta < -PI) lineDistTheta += (2.0 * PI);

    float avgR = ((planRho + lineTargetRho) / 2.0) * tableRadius;
    float totalDist = sqrt(pow(avgR * fabs(lineDistTheta), 2) + pow(fabs(lineDistRho * tableRadius), 2));
    
    lineTotalSegments = ceil(totalDist / interpolationRes);
    if (lineTotalSegments < 1) lineTotalSegments = 1;
    lineCurrentSegment = 1;
    isDrawingLine = true;
  }

  if (isDrawingLine) {
    if (((stepHead + 1) % STEP_QUEUE_SIZE) != stepTail) { 
      float nextTheta = planTheta + (lineDistTheta * (float)lineCurrentSegment / lineTotalSegments);
      float nextRho = planRho + (lineDistRho * (float)lineCurrentSegment / lineTotalSegments);

      float r_mm = nextRho * tableRadius;
      float x = r_mm * cos(nextTheta);
      float y = r_mm * sin(nextTheta);

      IKResult target = calculateIK(x, y, planBaseSteps);

      long da = target.elbowSteps - planElbowSteps;
      long db = target.baseSteps - planBaseSteps;
      long maxStepsForSegment = max(abs(da), abs(db));

      if (maxStepsForSegment > 0) {
        
        // --- CONSTANT FEEDRATE MATH ---
        float actualSpeed = targetCartesianSpeed * SPEED_MULTIPLIER;
        
        // Calculate the physical distance of this specific segment
        float avgR = ((planRho + nextRho) / 2.0) * tableRadius;
        float distTheta = nextTheta - planTheta;
        float distRho = nextRho - planRho;
        float segmentDist_mm = sqrt(pow(avgR * fabs(distTheta), 2) + pow(fabs(distRho * tableRadius), 2));
        
        // Failsafe if segment calculation approaches zero
        if (segmentDist_mm < 0.01) segmentDist_mm = interpolationRes;

        // How many seconds this tiny move should take to match our target speed
        float segmentTime_sec = segmentDist_mm / actualSpeed;
        
        // Divide total microseconds by the amount of steps to get the delay per step
        long calculatedDelayUs = (segmentTime_sec * 1000000.0) / maxStepsForSegment;
        
        // Clamp to prevent hardware stalling (moving too fast) or integer overflows (moving too slow)
        if (calculatedDelayUs < MIN_STEP_DELAY) calculatedDelayUs = MIN_STEP_DELAY;
        if (calculatedDelayUs > 65000) calculatedDelayUs = 65000;

        stepDa[stepHead] = da;
        stepDb[stepHead] = db;
        stepDelay[stepHead] = (unsigned int)calculatedDelayUs; // Inject the dynamic speed!
        stepHead = (stepHead + 1) % STEP_QUEUE_SIZE;
        
        planBaseSteps = target.baseSteps;
        planElbowSteps = target.elbowSteps;
      }

      lineCurrentSegment++;

      if (lineCurrentSegment > lineTotalSegments) {
        planTheta = lineTargetTheta; planRho = lineTargetRho;
        const long baseRevSteps = round((2.0 * PI) * stepsPerRad);
        
        bool didWrap = false;

        while (planTheta > PI) { 
          planTheta -= (2.0 * PI);
          planBaseSteps += baseRevSteps; 
          didWrap = true; 
        }
        while (planTheta < -PI) { 
          planTheta += (2.0 * PI);
          planBaseSteps -= baseRevSteps; 
          didWrap = true; 
        }
        
        // Prevents gearRatio rounding error from accumulating during continuous spirals
        if (didWrap) {
          float r_mm = planRho * tableRadius;
          float x = r_mm * cos(planTheta);
          float y = r_mm * sin(planTheta);
          IKResult wrappedPos = calculateIK(x, y, planBaseSteps);
          planElbowSteps = wrappedPos.elbowSteps;
        }
        
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
    cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0; stepsRemaining = 0;
    isDrawingLine = false; owesSyncOK = false;
    hasPendingCmd = false; 
    planTheta = 0; planRho = 0;
    IKResult zeroPos = calculateIK(0, 0, 0);
    planBaseSteps = zeroPos.baseSteps; planElbowSteps = zeroPos.elbowSteps;
    curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
    Serial.println(F("CLEARED"));
  }
  else if (strcasecmp(start, "CALIBRATE") == 0) {
    if ((cmdHead == cmdTail) && (stepHead == stepTail) && stepsRemaining == 0 && !hasPendingCmd) calibrate();
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
    
    baseCalRotating = false; cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0; stepsRemaining = 0;
    isDrawingLine = false; owesSyncOK = false;
    hasPendingCmd = false; isSoftPausing = false; paused = false;
    
    int eeAddr = 0;
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
    if (newMult > 0.1 && newMult < 10.0) {
      SPEED_MULTIPLIER = newMult;
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

      if (((stepHead + 1) % STEP_QUEUE_SIZE) != stepTail) {
        stepDa[stepHead] = armRaw;  
        stepDb[stepHead] = baseRaw; 
        stepDelay[stepHead] = round(1000.0 / SPEED_MULTIPLIER); // Fallback for raw commands
        stepHead = (stepHead + 1) % STEP_QUEUE_SIZE;
        
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

      if (((cmdHead + 1) % CMD_QUEUE_SIZE) != cmdTail) {
        cmdTheta[cmdHead] = targetTheta;
        cmdRho[cmdHead] = targetRho;
        cmdHead = (cmdHead + 1) % CMD_QUEUE_SIZE;
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
    digitalWrite(dirBase, HIGH); 
    for(int i=0; i<10; i++){ digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW); delayMicroseconds(1000); }
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

// --- MAGNETIC AUTO-CENTERING CALIBRATION ---

void calibrate() {
  paused = false;
  digitalWrite(enPin, LOW); 
  Serial.println(F("STATUS:CALIBRATING"));
  
  detachInterrupt(digitalPinToInterrupt(baseStopPin));
  detachInterrupt(digitalPinToInterrupt(elbowStopPin));

  Serial.println(F("HOMING_BASE..."));
  findMagneticCenter(stepBase, dirBase, baseStopPin, 5000); 

  Serial.println(F("HOMING_ARM..."));
  findMagneticCenter(stepArm, dirArm, elbowStopPin, 3000); 

  // We are firmly in the true magnetic center of both switches at the edge of the table.
  planTheta = 0.0; 
  planRho = 1.0;
  
  float r_mm = planRho * tableRadius;
  float startX = r_mm * cos(planTheta);
  float startY = r_mm * sin(planTheta);
  
  IKResult perimeterPos = calculateIK(startX, startY, 0); 
  
  planBaseSteps = perimeterPos.baseSteps;
  planElbowSteps = perimeterPos.elbowSteps;
  curBaseSteps = perimeterPos.baseSteps;
  curElbowSteps = perimeterPos.elbowSteps;

  cmdHead = 0; cmdTail = 0; stepHead = 0; stepTail = 0; stepsRemaining = 0; 
  isDrawingLine = false; owesSyncOK = false; hasPendingCmd = false; isSoftPausing = false;
  
  int eeAddr = 0; 
  EEPROM.put(eeAddr, curBaseSteps); eeAddr += sizeof(long);
  EEPROM.put(eeAddr, curElbowSteps); eeAddr += sizeof(long);
  EEPROM.put(eeAddr, planTheta); eeAddr += sizeof(float);
  EEPROM.put(eeAddr, planRho);
  
  baseHomed = false;
  armHomed = false;
  attachInterrupt(digitalPinToInterrupt(baseStopPin), baseEndstopISR, FALLING);
  attachInterrupt(digitalPinToInterrupt(elbowStopPin), elbowEndstopISR, FALLING);

  Serial.println(F("CALIBRATION_COMPLETE"));
  
  delay(1500); 
  
  char zeroCmd[] = "0 0";
  handleCommand(zeroCmd);
}

void findMagneticCenter(int stepPin, int dirPin, int stopPin, int delayUs) {
  long maxHomingSteps = stepsPerRad * PI * 2.5; 
  long steps = 0;
  
  digitalWrite(dirPin, HIGH);
  while (digitalRead(stopPin) == HIGH && steps < maxHomingSteps) {
    pulseStepper(stepPin, delayUs);
    steps++;
  }

  if (steps >= maxHomingSteps) {
    Serial.println(F("ERR: HOMING_TIMEOUT"));
    return;
  }

  long magnetWidthSteps = 0;
  while (digitalRead(stopPin) == LOW && magnetWidthSteps < 5000) { 
    pulseStepper(stepPin, delayUs);
    magnetWidthSteps++;
  }

  delay(1000);

  digitalWrite(dirPin, LOW);
  
  long reverseTimeout = 0;
  while (digitalRead(stopPin) == HIGH && reverseTimeout < 5000) {
    pulseStepper(stepPin, delayUs);
    reverseTimeout++;
  }

  long centerSteps = magnetWidthSteps / 2;
  for (long i = 0; i < centerSteps; i++) {
    pulseStepper(stepPin, delayUs);
  }
  
  delay(500); 
}

void pulseStepper(int stepPin, int delayUs) {
  digitalWrite(stepPin, HIGH); 
  delayMicroseconds(2); 
  digitalWrite(stepPin, LOW);
  delayMicroseconds(delayUs); 
}

// --- LED CONTROLS ---

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
