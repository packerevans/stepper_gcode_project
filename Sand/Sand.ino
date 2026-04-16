/*
 * Sand Table Firmware - Theta-Rho Processor
 * Optimized for LGT8F328P (32MHz) & TMC2209 Stepper Drivers
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
const float interpolationRes = 1.0; // LGT8 @ 32MHz can handle 1.0mm easily

// --- SPEED SETTINGS ---
int centerDelay = 500;    
int perimeterDelay = 3000; 
float SPEED_MULTIPLIER = 1.0;

// --- LED MODE STATE ---
int ledMode = 0; // 0: Static, 1: Flash, 2: Fade, 3: Jump
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

#define BAUD_RATE 250000
char serialBuf[64];
int bufIdx = 0;

// --- FUNCTION PROTOTYPES ---
void processSerialQueue();
void handleCommand(char* cmd);
void calibrate();
void processRgbLine(char* line);
bool processThrLine(char* line);
void processModeCommand(char* data);
void moveToPolar(float theta, float rho);
IKResult calculateIK(float x, float y);
void moveBresenham(long da, long db, int delayUs);

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  pinMode(elbowStopPin, INPUT_PULLUP);
  pinMode(baseStopPin, INPUT_PULLUP);

  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);

  // Microstepping Setup
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); 
  digitalWrite(ms2, HIGH); 
  digitalWrite(ms3, HIGH); // Note: On some TMC2209 boards, pulling MS3 HIGH enables SpreadCycle (noisy). Set LOW for StealthChop (silent) if needed.

  digitalWrite(enPin, LOW); // Energize motors
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
  processSerialQueue();
  updateLedMode();
}

void updateLedMode() {
  if (ledMode == 0 || numModeColors == 0) return;
  
  unsigned long now = millis();
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
  // Trim leading whitespace
  char* start = cmd;
  while (*start == ' ' || *start == '\t') start++;
  
  if (strlen(start) == 0 || start[0] == '#') return;

  if (strcasecmp(start, "PAUSE") == 0) {
    paused = true; 
    // enPin remains LOW so TMC2209 holds torque and microstep position
    Serial.println(F("PAUSED"));
  }
  else if (strcasecmp(start, "RESUME") == 0 || strcasecmp(start, "R") == 0) {
    paused = false; 
    digitalWrite(enPin, LOW);
    Serial.println(F("RESUMED"));
  }
  else if (strcasecmp(start, "CLEAR") == 0) {
    paused = true; 
    digitalWrite(enPin, HIGH); // Safe to cut power since we are dropping position tracking anyway
    shouldAbort = true;
    Serial.println(F("CLEARED"));
  }
  else if (strcasecmp(start, "CALIBRATE") == 0) {
    if (!isExecutingThr) calibrate();
  }
  else if (strncasecmp(start, "SPEED ", 6) == 0) {
    float newMult = atof(start + 6);
    if (newMult > 0.1 && newMult < 10.0) {
      SPEED_MULTIPLIER = newMult;
      centerDelay = 500 / SPEED_MULTIPLIER;
      perimeterDelay = 3000 / SPEED_MULTIPLIER;
      Serial.print(F("SPEED_SET:")); Serial.println(SPEED_MULTIPLIER);
    }
  }
  else if (strncasecmp(start, "C", 1) == 0) {
    processModeCommand(start + 1);
    Serial.println(F("MODE_OK"));
  }
  else if (strchr(start, ',') != NULL) {
    ledMode = 0; // Stop any animation on manual color change
    processRgbLine(start);
    Serial.println(F("RGB_OK"));
  }
  else {
    if (!isExecutingThr) {
      isExecutingThr = true;
      if (processThrLine(start)) {
        Serial.println(F("OK"));
      } else {
        Serial.println(F("ABORTED"));
      }
      isExecutingThr = false;
    }
  }
}

void calibrate() {
  paused = false; 
  digitalWrite(enPin, LOW);
  Serial.println(F("STATUS:CALIBRATING"));

  long maxHomingSteps = stepsPerRad * PI * 2.5; // Max steps before assuming hardware failure
  long currentSteps = 0;

  // Home Base
  digitalWrite(dirBase, HIGH); 
  while (digitalRead(baseStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepBase, HIGH); delayMicroseconds(2);
    digitalWrite(stepBase, LOW);  delayMicroseconds(2000); 
    currentSteps++;
  }
  if (currentSteps >= maxHomingSteps) {
    Serial.println(F("ERROR: BASE HOMING FAILED"));
    return;
  }
  curBaseSteps = 0; 

  // Home Arm
  currentSteps = 0;
  digitalWrite(dirArm, HIGH);
  while (digitalRead(elbowStopPin) == HIGH && currentSteps < maxHomingSteps) {
    digitalWrite(stepArm, HIGH); delayMicroseconds(2);
    digitalWrite(stepArm, LOW);  delayMicroseconds(2000);
    currentSteps++;
  }
  if (currentSteps >= maxHomingSteps) {
    Serial.println(F("ERROR: ARM HOMING FAILED"));
    return;
  }

  curElbowSteps = 0; 
  curTheta = 0; 
  curRho = 0;
  Serial.println(F("CALIBRATION_COMPLETE"));
}

void processRgbLine(char* line) {
  char* firstComma = strchr(line, ',');
  if (firstComma != NULL) {
    char* secondComma = strchr(firstComma + 1, ',');
    if (secondComma != NULL) {
      *firstComma = '\0';
      *secondComma = '\0';
      int r = atoi(line);
      int g = atoi(firstComma + 1);
      int b = atoi(secondComma + 1);
      analogWrite(redPin, r);
      analogWrite(greenPin, g);
      analogWrite(bluePin, b);
    }
  }
}

bool processThrLine(char* line) {
  char* spacePtr = strchr(line, ' ');
  if (spacePtr == NULL) return false;

  *spacePtr = '\0'; 
  float targetTheta = atof(line);
  float targetRho = atof(spacePtr + 1);

  float distTheta = targetTheta - curTheta;
  float distRho = targetRho - curRho;
  
  float avgR = ((curRho + targetRho) / 2.0) * tableRadius;
  float totalDist = sqrt(pow(avgR * abs(distTheta), 2) + pow(abs(distRho * tableRadius), 2));
  
  int steps = ceil(totalDist / interpolationRes);
  if (steps < 1) steps = 1;

  shouldAbort = false;

  for (int i = 1; i <= steps; i++) {
    // Poll the serial buffer during movement so PAUSE executes immediately
    processSerialQueue(); 

    while (paused) {
      delay(10);
      processSerialQueue(); // Keep polling while paused so we can RESUME
      if (shouldAbort) break;
    }
    
    if (shouldAbort) {
      // Save EXACT mathematical position so the next move starts cleanly
      curTheta = curTheta + (distTheta * (float)(i-1)/steps);
      curRho = curRho + (distRho * (float)(i-1)/steps);
      return false; 
    }

    moveToPolar(curTheta + (distTheta * (float)i/steps), curRho + (distRho * (float)i/steps));
  }

  moveToPolar(targetTheta, targetRho);
  curTheta = targetTheta; curRho = targetRho;
  return true;
}

void moveToPolar(float theta, float rho) {
  float r_mm = rho * tableRadius;
  float x = r_mm * cos(theta);
  float y = r_mm * sin(theta);
  IKResult target = calculateIK(x, y);
  
  float speedFactor = (rho > 1.0) ? 1.0 : rho;
  int delayUs = centerDelay + ((perimeterDelay - centerDelay) * speedFactor);
  
  moveBresenham(target.elbowSteps - curElbowSteps, target.baseSteps - curBaseSteps, delayUs);
  curBaseSteps = target.baseSteps; 
  curElbowSteps = target.elbowSteps;
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

void moveBresenham(long da, long db, int delayUs) {
  if (da == 0 && db == 0) return;
  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);
  
  long ad = abs(da);
  long bd = abs(db);
  long steps = max(ad, bd);
  long accA = steps / 2;
  long accB = steps / 2;
  
  for (long i = 0; i < steps; i++) {
    accA -= ad; 
    if (accA < 0) { 
      digitalWrite(stepArm, HIGH); 
      accA += steps; 
    }
    accB -= bd; 
    if (accB < 0) { 
      digitalWrite(stepBase, HIGH); 
      accB += steps; 
    }
    
    // TMC2209 handles 2us pulse easily
    delayMicroseconds(2); 
    digitalWrite(stepArm, LOW); 
    digitalWrite(stepBase, LOW);
    
    delayMicroseconds(max(1, delayUs));
  }
}

void processModeCommand(char* data) {
  // Format: <mode>,<duration>,<r1>,<g1>,<b1>,...
  char* ptr = data;
  ledMode = atoi(ptr);
  ptr = strchr(ptr, ','); if (!ptr) return; ptr++;
  ledInterval = atol(ptr);
  ptr = strchr(ptr, ','); if (!ptr) return; ptr++;
  
  numModeColors = 0;
  while (ptr && numModeColors < 12) {
    modeColors[numModeColors].r = atoi(ptr);
    ptr = strchr(ptr, ','); if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].g = atoi(ptr);
    ptr = strchr(ptr, ','); if (!ptr) { numModeColors++; break; } ptr++;
    modeColors[numModeColors].b = atoi(ptr);
    numModeColors++;
    ptr = strchr(ptr, ',');
    if (ptr) ptr++;
  }
  lastLedUpdate = millis();
  currentModeStep = 0;
}
