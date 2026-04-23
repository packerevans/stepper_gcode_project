/*
 * Sand Table Firmware - Pure Motor RPM (Constant Step Rate)
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

// --- MACHINE CONFIGURATION ---
const float tableRadius = 202.6; 
const float L1 = 101.3;          
const float L2 = 101.3;          
const float gearRatio = 1.209; 
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 1.0; 

// --- PURE SPEED SETTINGS ---
// A constant, unchanging delay. The motors will hum at a perfect RPM.
int currentStepDelay = 1000; 
float SPEED_MULTIPLIER = 1.0;

// --- RAW STEP RESERVOIR ---
#define QUEUE_SIZE 128 
long qDa[QUEUE_SIZE];
long qDb[QUEUE_SIZE];
int qDelay[QUEUE_SIZE];
volatile int qHead = 0;
volatile int qTail = 0;
bool owesSyncOK = false;

bool isQueueFull() { return ((qHead + 1) % QUEUE_SIZE) == qTail; }
bool isQueueEmpty() { return qHead == qTail; }

// --- THE PLANNER TRACKERS ---
float planTheta = 0;
float planRho = 0;
long planBaseSteps = 0;
long planElbowSteps = 0;

// --- THE PHYSICAL TRACKERS ---
long curBaseSteps = 0;
long curElbowSteps = 0;

// --- LED MODE STATE ---
int ledMode = 0; 
unsigned long ledInterval = 1000;
unsigned long lastLedUpdate = 0;
int currentModeStep = 0;
int numModeColors = 0;
struct { byte r, g, b; } modeColors[12];

bool paused = false;
bool isExecutingThr = false;
bool shouldAbort = false;
bool baseCalRotating = false;

#define BAUD_RATE 250000
char serialBuf[64];
int bufIdx = 0;

// --- FUNCTION PROTOTYPES ---
void processSerialQueue();
void handleCommand(char* cmd);
void calibrate();
void planMove(float targetTheta, float targetRho);
void executeNextMove();
IKResult calculateIK(float x, float y, long referenceBaseSteps);
void moveBresenham(long da, long db, int delayUs);
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
    curBaseSteps = zeroPos.baseSteps; 
    curElbowSteps = zeroPos.elbowSteps;
  }
  
  planBaseSteps = curBaseSteps;
  planElbowSteps = curElbowSteps;
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  pinMode(elbowStopPin, INPUT_PULLUP);
  pinMode(baseStopPin, INPUT_PULLUP);

  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);

  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, LOW);

  digitalWrite(enPin, LOW); 
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
  processSerialQueue();
  updateLedMode();

  if (baseCalRotating && !isExecutingThr) {
    digitalWrite(dirBase, LOW);
    digitalWrite(stepBase, HIGH); delayMicroseconds(2);
    digitalWrite(stepBase, LOW);  delayMicroseconds(1000); 
  }

  // --- THE CONSUMER ENGINE ---
  if (!isQueueEmpty()) {
    executeNextMove();
  } else if (!isExecutingThr && owesSyncOK) {
    Serial.println(F("OK"));
    owesSyncOK = false;
  }
}

void executeNextMove() {
  if (paused || isQueueEmpty()) return;
  isExecutingThr = true;
  
  long da = qDa[qTail];
  long db = qDb[qTail];
  int delayUs = qDelay[qTail];
  qTail = (qTail + 1) % QUEUE_SIZE;

  moveBresenham(da, db, delayUs);
  
  curBaseSteps += db; 
  curElbowSteps += da; 

  isExecutingThr = false;
}

void planMove(float targetTheta, float targetRho) {
  float distTheta = targetTheta - planTheta;
  float distRho = targetRho - planRho;

  while (distTheta > PI) distTheta -= (2.0 * PI);
  while (distTheta < -PI) distTheta += (2.0 * PI);
  
  float avgR = ((planRho + targetRho) / 2.0) * tableRadius;
  float totalDist = sqrt(pow(avgR * fabs(distTheta), 2) + pow(fabs(distRho * tableRadius), 2));
  
  int steps = ceil(totalDist / interpolationRes);
  if (steps < 1) steps = 1;

  for (int i = 1; i <= steps; i++) {
    if (shouldAbort) break;

    float nextTheta = planTheta + (distTheta * (float)i/steps);
    float nextRho = planRho + (distRho * (float)i/steps);

    float r_mm = nextRho * tableRadius;
    float x = r_mm * cos(nextTheta);
    float y = r_mm * sin(nextTheta);

    IKResult target = calculateIK(x, y, planBaseSteps);

    long da = target.elbowSteps - planElbowSteps; 
    long db = target.baseSteps - planBaseSteps;   
    long maxSteps = max(abs(da), abs(db));

    if (maxSteps > 0) {
      // THE FIX: No more math. Just raw, pure, constant motor RPM.
      int finalDelay = currentStepDelay;

      while (isQueueFull()) {
        executeNextMove();
        processSerialQueue(); 
        if (shouldAbort) break;
      }

      qDa[qHead] = da;
      qDb[qHead] = db;
      qDelay[qHead] = finalDelay;
      qHead = (qHead + 1) % QUEUE_SIZE;

      planBaseSteps = target.baseSteps; 
      planElbowSteps = target.elbowSteps;
    }
  }

  planTheta = targetTheta; 
  planRho = targetRho;
  
  const long baseRevSteps = round((2.0 * PI) * stepsPerRad);
  const long elbowRevSteps = round((2.0 * PI) * stepsPerRad * gearRatio);

  while (planTheta > PI) { planTheta -= (2.0 * PI); planBaseSteps += baseRevSteps; planElbowSteps += elbowRevSteps; }
  while (planTheta < -PI) { planTheta += (2.0 * PI); planBaseSteps -= baseRevSteps; planElbowSteps -= elbowRevSteps; }
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

void moveBresenham(long da, long db, int delayUs) {
  if (da == 0 && db == 0) return;
  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW); digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);
  long ad = abs(da); long bd = abs(db); long steps = max(ad, bd);
  long accA = steps / 2; long accB = steps / 2;
  
  for (long i = 0; i < steps; i++) {
    accA -= ad; if (accA < 0) { digitalWrite(stepArm, HIGH); accA += steps; }
    accB -= bd; if (accB < 0) { digitalWrite(stepBase, HIGH); accB += steps; }
    delayMicroseconds(2); digitalWrite(stepArm, LOW); digitalWrite(stepBase, LOW);
    delayMicroseconds(max(1, delayUs));
  }
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
    paused = true; Serial.println(F("PAUSED"));
  }
  else if (strcasecmp(start, "RESUME") == 0 || strcasecmp(start, "R") == 0) {
    paused = false; digitalWrite(enPin, LOW); Serial.println(F("RESUMED"));
  }
  else if (strcasecmp(start, "CLEAR") == 0) {
    paused = true; digitalWrite(enPin, HIGH); shouldAbort = true;
    qHead = 0; qTail = 0; owesSyncOK = false;
    planTheta = 0; planRho = 0; 
    IKResult zeroPos = calculateIK(0, 0, 0);
    planBaseSteps = zeroPos.baseSteps; planElbowSteps = zeroPos.elbowSteps;
    curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
    Serial.println(F("CLEARED"));
  }
  else if (strcasecmp(start, "CALIBRATE") == 0) {
    if (isQueueEmpty() && !isExecutingThr) calibrate();
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
    
    baseCalRotating = false; qHead = 0; qTail = 0; owesSyncOK = false;
    
    int eeAddr = 0;
    EEPROM.put(eeAddr, curBaseSteps); eeAddr += sizeof(long);
    EEPROM.put(eeAddr, curElbowSteps); eeAddr += sizeof(long);
    EEPROM.put(eeAddr, planTheta); eeAddr += sizeof(float);
    EEPROM.put(eeAddr, planRho);
    Serial.println(F("ZERO_SAVED"));
  }
  else if (strcasecmp(start, "SYNC") == 0) {
    owesSyncOK = true; 
  }
  else if (strncasecmp(start, "SPEED ", 6) == 0) {
    float newMult = atof(start + 6);
    if (newMult > 0.1 && newMult < 10.0) {
      SPEED_MULTIPLIER = newMult;
      // THE FIX: Directly scale the 1000us baseline delay!
      currentStepDelay = round(1000.0 / SPEED_MULTIPLIER);
      Serial.print(F("SPEED_SET:")); Serial.println(SPEED_MULTIPLIER);
    }
  }
  else if (strncasecmp(start, "C", 1) == 0) {
    processModeCommand(start + 1); Serial.println(F("MODE_OK"));
  }
  else if (strchr(start, ',') != NULL) {
    ledMode = 0; processRgbLine(start); Serial.println(F("RGB_OK"));
  }
  else {
    char* spacePtr = strchr(start, ' ');
    if (spacePtr != NULL) {
      *spacePtr = '\0'; 
      float targetTheta = atof(start);
      float targetRho = atof(spacePtr + 1);

      shouldAbort = false;
      planMove(targetTheta, targetRho);
      Serial.println(F("OK")); 
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

void calibrate() {
  paused = false; digitalWrite(enPin, LOW); Serial.println(F("STATUS:CALIBRATING"));
  long maxHomingSteps = stepsPerRad * PI * 2.5; long currentSteps = 0;
  digitalWrite(dirBase, HIGH); 
  while (digitalRead(baseStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepBase, HIGH); delayMicroseconds(2); digitalWrite(stepBase, LOW);  delayMicroseconds(2000); currentSteps++;
  }
  if (currentSteps >= maxHomingSteps) { Serial.println(F("ERROR: BASE HOMING FAILED")); return; }
  currentSteps = 0; digitalWrite(dirArm, HIGH);
  while (digitalRead(elbowStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepArm, HIGH); delayMicroseconds(2); digitalWrite(stepArm, LOW);  delayMicroseconds(2000); currentSteps++;
  }
  if (currentSteps >= maxHomingSteps) { Serial.println(F("ERROR: ARM HOMING FAILED")); return; }

  planTheta = 0; planRho = 0;
  IKResult zeroPos = calculateIK(0, 0, 0); 
  planBaseSteps = zeroPos.baseSteps; planElbowSteps = zeroPos.elbowSteps;
  curBaseSteps = zeroPos.baseSteps; curElbowSteps = zeroPos.elbowSteps;
  qHead = 0; qTail = 0; owesSyncOK = false; Serial.println(F("CALIBRATION_COMPLETE"));
}

void updateLedMode() {
  if (ledMode == 0 || numModeColors == 0) return;
  unsigned long now = millis();
  if (ledMode == 1) { 
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now; currentModeStep = (currentModeStep + 1) % (numModeColors * 2);
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
      lastLedUpdate = now; currentModeStep = (currentModeStep + 1) % numModeColors;
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
      int r = atoi(line); int g = atoi(firstComma + 1); int b = atoi(secondComma + 1);
      analogWrite(redPin, r); analogWrite(greenPin, g); analogWrite(bluePin, b);
    }
  }
}

void processModeCommand(char* data) {
  char* ptr = data; ledMode = atoi(ptr);
  ptr = strchr(ptr, ','); if (!ptr) return; ptr++; ledInterval = atol(ptr);
  ptr = strchr(ptr, ','); if (!ptr) return; ptr++;
  numModeColors = 0;
  while (ptr && numModeColors < 12) {
    modeColors[numModeColors].r = atoi(ptr); ptr = strchr(ptr, ','); if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].g = atoi(ptr); ptr = strchr(ptr, ','); if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].b = atoi(ptr); numModeColors++; ptr = strchr(ptr, ','); if (ptr) ptr++;
  }
  lastLedUpdate = millis(); currentModeStep = 0;
}
