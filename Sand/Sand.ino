/**
 * Sand Table Firmware - Theta-Rho Processor
 * Optimized for LGT8F328P (32MHz) & TMC2209 Stepper Drivers
 * Refactored for Smooth, Non-Blocking Movement
 */

#include <Arduino.h>

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
const float gearRatio = 1.125;
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 0.5; // Finer interpolation for smoothness

// --- SPEED & ACCEL SETTINGS ---
int centerDelay = 500;    
int perimeterDelay = 3000;
float SPEED_MULTIPLIER = 1.0;

// Acceleration parameters
const int minDelayUs = 400;     // Absolute fastest step (lower = faster)
const int maxDelayUs = 3000;    // Starting/Stopping step (higher = slower)
const float accelRate = 0.5;    // Step delay decrement per step

// --- LED MODE STATE ---
int ledMode = 0; 
unsigned long ledInterval = 1000;
unsigned long lastLedUpdate = 0;
int currentModeStep = 0;
int numModeColors = 0;
struct { byte r, g, b; } modeColors[12];

// --- STATE VARIABLES ---
float curTheta = 0;
float curRho = 0;
long curBaseSteps = 0;
long curElbowSteps = 0;

bool paused = false;
bool isExecutingThr = false;
bool shouldAbort = false;

// Movement State Machine
struct {
  bool active = false;
  float targetTheta;
  float targetRho;
  float distTheta;
  float distRho;
  int totalSubSteps;
  int currentSubStep;
  
  // Bresenham variables for current sub-step
  long da, db;
  long stepsRemaining;
  long ad, bd;
  long accA, accB;
  int currentDelay;
} moveState;

#define BAUD_RATE 250000
char serialBuf[64];
int bufIdx = 0;

// --- FUNCTION PROTOTYPES ---
void processSerialQueue();
void handleCommand(char* cmd);
void calibrate();
void processRgbLine(char* line);
void startMove(float tTheta, float tRho);
void updateMovement();
void updateLedMode();
IKResult calculateIK(float x, float y);

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);
 
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  pinMode(elbowStopPin, INPUT_PULLUP);
  pinMode(baseStopPin, INPUT_PULLUP);

  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);

  // Microstepping Setup (1/16th)
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH);
  digitalWrite(ms2, HIGH);
  digitalWrite(ms3, LOW); 

  digitalWrite(enPin, LOW); 
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
  processSerialQueue();
  updateMovement();
  updateLedMode();
}

void updateLedMode() {
  unsigned long now = millis();
  
  if (ledMode == 0) return;
  if (numModeColors == 0) return;

  if (ledMode == 1) { // Flash
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now;
      currentModeStep = (currentModeStep + 1) % (numModeColors * 2);
      if (currentModeStep % 2 == 0) {
        int idx = currentModeStep / 2;
        analogWrite(redPin, modeColors[idx].r);
        analogWrite(greenPin, modeColors[idx].g);
        analogWrite(bluePin, modeColors[idx].b);
      } else {
        analogWrite(redPin, 0); analogWrite(greenPin, 0); analogWrite(bluePin, 0);
      }
    }
  }
  else if (ledMode == 2) { // Fade
    float t = (float)((now - lastLedUpdate) % ledInterval) / ledInterval;
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now;
      currentModeStep = (currentModeStep + 1) % numModeColors;
    }
    int nextStep = (currentModeStep + 1) % numModeColors;
    int r = modeColors[currentModeStep].r + (modeColors[nextStep].r - modeColors[currentModeStep].r) * t;
    int g = modeColors[currentModeStep].g + (modeColors[nextStep].g - modeColors[currentModeStep].g) * t;
    int b = modeColors[currentModeStep].b + (modeColors[nextStep].b - modeColors[currentModeStep].b) * t;
    analogWrite(redPin, r); analogWrite(greenPin, g); analogWrite(bluePin, b);
  }
  else if (ledMode == 3) { // Jump
    if (now - lastLedUpdate >= ledInterval) {
      lastLedUpdate = now;
      currentModeStep = (currentModeStep + 1) % numModeColors;
      analogWrite(redPin, modeColors[currentModeStep].r);
      analogWrite(greenPin, modeColors[currentModeStep].g);
      analogWrite(bluePin, modeColors[currentModeStep].b);
    }
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
    paused = true;
    Serial.println(F("PAUSED"));
  }
  else if (strcasecmp(start, "RESUME") == 0) {
    paused = false;
    digitalWrite(enPin, LOW);
    Serial.println(F("RESUMED"));
  }
  else if (strcasecmp(start, "CLEAR") == 0) {
    paused = false;
    moveState.active = false;
    isExecutingThr = false;
    shouldAbort = true;
    Serial.println(F("CLEARED"));
  }
  else if (strcasecmp(start, "CALIBRATE") == 0) {
    if (!moveState.active) calibrate();
  }
  else if (strncasecmp(start, "SPEED ", 6) == 0) {
    SPEED_MULTIPLIER = atof(start + 6);
    centerDelay = 500 / SPEED_MULTIPLIER;
    perimeterDelay = 3000 / SPEED_MULTIPLIER;
    Serial.print(F("SPEED_SET:")); Serial.println(SPEED_MULTIPLIER);
  }
  else if (strncasecmp(start, "C", 1) == 0) {
    // Mode settings
    char* ptr = start + 1;
    ledMode = atoi(ptr);
    ptr = strchr(ptr, ','); if (ptr) {
      ptr++; ledInterval = atol(ptr);
      ptr = strchr(ptr, ','); if (ptr) {
        ptr++; numModeColors = 0;
        while (ptr && numModeColors < 12) {
          modeColors[numModeColors].r = atoi(ptr);
          ptr = strchr(ptr, ','); if (!ptr) break; ptr++;
          modeColors[numModeColors].g = atoi(ptr);
          ptr = strchr(ptr, ','); if (!ptr) break; ptr++;
          modeColors[numModeColors].b = atoi(ptr);
          numModeColors++;
          ptr = strchr(ptr, ','); if (ptr) ptr++;
        }
      }
    }
    lastLedUpdate = millis();
    Serial.println(F("MODE_OK"));
  }
  else if (strchr(start, ',') != NULL) {
    ledMode = 0;
    processRgbLine(start);
    Serial.println(F("RGB_OK"));
  }
  else {
    // Polar Move
    if (moveState.active) {
      // We are already moving. Backend should manage buffer, but as safety:
      Serial.println(F("BUSY"));
    } else {
      char* spacePtr = strchr(start, ' ');
      if (spacePtr) {
        *spacePtr = '\0';
        startMove(atof(start), atof(spacePtr + 1));
      }
    }
  }
}

void startMove(float tTheta, float tRho) {
  moveState.targetTheta = tTheta;
  moveState.targetRho = tRho;
  
  float distTheta = tTheta - curTheta;
  while (distTheta > PI) distTheta -= (2.0 * PI);
  while (distTheta < -PI) distTheta += (2.0 * PI);
  moveState.distTheta = distTheta;
  moveState.distRho = tRho - curRho;

  float avgR = ((curRho + tRho) / 2.0) * tableRadius;
  float totalDist = sqrt(pow(avgR * abs(moveState.distTheta), 2) + pow(abs(moveState.distRho * tableRadius), 2));
 
  moveState.totalSubSteps = ceil(totalDist / interpolationRes);
  if (moveState.totalSubSteps < 1) moveState.totalSubSteps = 1;
  
  moveState.currentSubStep = 0;
  moveState.stepsRemaining = 0;
  moveState.active = true;
  isExecutingThr = true;
}

void setupSubStep() {
  moveState.currentSubStep++;
  if (moveState.currentSubStep > moveState.totalSubSteps) {
    moveState.active = false;
    isExecutingThr = false;
    curTheta = curTheta + moveState.distTheta;
    curRho = moveState.targetRho;
    Serial.println(F("OK"));
    return;
  }

  float progress = (float)moveState.currentSubStep / moveState.totalSubSteps;
  float t = curTheta + (moveState.distTheta * progress);
  float r = curRho + (moveState.distRho * progress);
  
  float x = r * tableRadius * cos(t);
  float y = r * tableRadius * sin(t);
  IKResult target = calculateIK(x, y);

  moveState.da = target.elbowSteps - curElbowSteps;
  moveState.db = target.baseSteps - curBaseSteps;
  
  curBaseSteps = target.baseSteps;
  curElbowSteps = target.elbowSteps;

  digitalWrite(dirArm, (moveState.da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (moveState.db >= 0) ? HIGH : LOW);

  moveState.ad = abs(moveState.da);
  moveState.bd = abs(moveState.db);
  moveState.stepsRemaining = max(moveState.ad, moveState.bd);
  moveState.accA = moveState.stepsRemaining / 2;
  moveState.accB = moveState.stepsRemaining / 2;

  float speedFactor = (r > 1.0) ? 1.0 : r;
  moveState.currentDelay = centerDelay + ((perimeterDelay - centerDelay) * speedFactor);
}

void updateMovement() {
  if (!moveState.active || paused) return;

  if (moveState.stepsRemaining <= 0) {
    setupSubStep();
    if (!moveState.active) return;
  }

  // Perform one step of Bresenham
  moveState.accA -= moveState.ad;
  if (moveState.accA < 0) {
    digitalWrite(stepArm, HIGH);
    moveState.accA += moveState.stepsRemaining;
  }
  moveState.accB -= moveState.bd;
  if (moveState.accB < 0) {
    digitalWrite(stepBase, HIGH);
    moveState.accB += moveState.stepsRemaining;
  }
  
  delayMicroseconds(2);
  digitalWrite(stepArm, LOW);
  digitalWrite(stepBase, LOW);
  
  delayMicroseconds(max(1, moveState.currentDelay));
  moveState.stepsRemaining--;
}

void processRgbLine(char* line) {
  char* firstComma = strchr(line, ',');
  if (firstComma) {
    char* secondComma = strchr(firstComma + 1, ',');
    if (secondComma) {
      *firstComma = '\0';
      *secondComma = '\0';
      analogWrite(redPin, atoi(line));
      analogWrite(greenPin, atoi(firstComma + 1));
      analogWrite(bluePin, atoi(secondComma + 1));
    }
  }
}

void calibrate() {
  paused = false;
  digitalWrite(enPin, LOW);
  Serial.println(F("STATUS:CALIBRATING"));

  long maxHomingSteps = stepsPerRad * PI * 2.5; 
  long currentSteps = 0;

  digitalWrite(dirBase, HIGH);
  while (digitalRead(baseStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepBase, HIGH); delayMicroseconds(2);
    digitalWrite(stepBase, LOW);  delayMicroseconds(2000);
    currentSteps++;
  }
  curBaseSteps = 0;

  currentSteps = 0;
  digitalWrite(dirArm, HIGH);
  while (digitalRead(elbowStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepArm, HIGH); delayMicroseconds(2);
    digitalWrite(stepArm, LOW);  delayMicroseconds(2000);
    currentSteps++;
  }

  curElbowSteps = 0;
  curTheta = 0;
  curRho = 0;
  Serial.println(F("CALIBRATION_COMPLETE"));
}

IKResult calculateIK(float x, float y) {
  float dist = hypot(x, y);
  const float maxReach = L1 + L2;
  if (dist > maxReach) {
    x *= (maxReach/dist);
    y *= (maxReach/dist);
    dist = maxReach;
  }
  if (dist < 1.0) return { 0, (long)(-PI * stepsPerRad) };
 
  float lastT1 = -(float)curBaseSteps / stepsPerRad;
  float cosBend = (dist * dist - L1 * L1 - L2 * L2) / (2.0 * L1 * L2);
  float bend = acos(max(-1.0f, min(1.0f, cosBend)));
  float t1 = atan2(y, x) - atan2(L2 * sin(bend), L1 + L2 * cos(bend));
  t1 = t1 - (round((t1 - lastT1) / (2.0 * PI)) * 2.0 * PI);
 
  return {
    (long)round(-t1 * stepsPerRad),
    (long)round(-(bend + gearRatio * t1) * stepsPerRad)
  };
}
