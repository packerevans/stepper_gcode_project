/*
 * Sand Table Firmware - Theta-Rho Processor
 * Derived from script (1).html and Sand.ino
 * Optimized for NEMA 17 Stepper Motors (SCARA Arm)
 */

struct IKResult {
  double baseSteps;
  double elbowSteps;
};

// --- PIN DEFINITIONS ---
const int stepBase = 7;
const int dirBase  = 8;
const int stepArm  = 5;
const int dirArm   = 16; // A2
const int enPin    = 15; // A1

// Stopper Switches
const int elbowStopPin = 2;
const int baseStopPin  = 3;

// RGB LED Pins
const int redPin   = 10;
const int greenPin = 9;
const int bluePin  = 11;

// Microstepping Pins (Moved to avoid conflict with D5)
const int ms1 = 17; // A3
const int ms2 = 18; // A4
const int ms3 = 19; // A5

// --- MACHINE CONFIGURATION (from script (1).html) ---
const float tableRadius = 202.6; // mm
const float L1 = 101.3;          // mm (Arm 1)
const float L2 = 101.3;          // mm (Arm 2)
const float gearRatio = 1.125;
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 1.0; // mm

// --- SPEED SETTINGS ---
const int centerDelay = 500;    // us (Fastest)
const int perimeterDelay = 3000; // us (Slowest)
const float SPEED_MULTIPLIER = 1.0;

// --- STATE VARIABLES ---
float curTheta = 0;
float curRho = 0;
double curBaseSteps = 0;
double curElbowSteps = 0;
bool paused = false;

#define BAUD_RATE 115200

void setup() {
  Serial.begin(BAUD_RATE);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  // Stopper Switches Setup (Input with Pullup)
  pinMode(elbowStopPin, INPUT_PULLUP);
  pinMode(baseStopPin, INPUT_PULLUP);

  // RGB Setup
  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);

  // Set Microstepping (1/16 or 1/32 based on HIGH/HIGH/HIGH)
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, LOW); // Enable motors
  
  Serial.println(F("SAND_TABLE_READY"));
  Serial.println(F("Send Theta Rho lines (e.g. 0.123 0.456) or RGB (e.g. 255,255,255)"));
}

void loop() {
  checkSerial();
}

void checkSerial() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    
    if (line.length() == 0 || line.startsWith("#")) return;

    if (line.equalsIgnoreCase("PAUSE")) {
      paused = true;
      digitalWrite(enPin, HIGH);
      Serial.println(F("PAUSED"));
    }
    else if (line.equalsIgnoreCase("RESUME") || line.equalsIgnoreCase("R")) {
      paused = false;
      digitalWrite(enPin, LOW);
      Serial.println(F("RESUMED"));
    }
    else if (line.equalsIgnoreCase("CLEAR")) {
      paused = true; // Stop current movement
      digitalWrite(enPin, HIGH);
      Serial.println(F("CLEARED"));
    }
    else if (line.indexOf(',') != -1) {
      processRgbLine(line);
      Serial.println(F("RGB_OK"));
    }
    else {
      if (processThrLine(line)) {
        Serial.println(F("OK")); // Signal ready for next line
      }
    }
  }
}

void processRgbLine(String line) {
  int firstComma = line.indexOf(',');
  int secondComma = line.indexOf(',', firstComma + 1);
  
  if (firstComma != -1 && secondComma != -1) {
    int r = line.substring(0, firstComma).toInt();
    int g = line.substring(firstComma + 1, secondComma).toInt();
    int b = line.substring(secondComma + 1).toInt();
    
    analogWrite(redPin, r);
    analogWrite(greenPin, g);
    analogWrite(bluePin, b);
  }
}

bool processThrLine(String line) {
  int spaceIdx = line.indexOf(' ');
  if (spaceIdx == -1) return false;

  float targetTheta = line.substring(0, spaceIdx).toFloat();
  float targetRho = line.substring(spaceIdx + 1).toFloat();

  // Interpolate from current position to target
  float distTheta = targetTheta - curTheta;
  float distRho = targetRho - curRho;
  
  // Calculate approximate distance for interpolation steps
  float avgR = ((curRho + targetRho) / 2.0) * tableRadius;
  float arcDist = avgR * abs(distTheta);
  float linDist = abs(distRho * tableRadius);
  float totalDist = sqrt(arcDist * arcDist + linDist * linDist);
  
  int steps = ceil(totalDist / interpolationRes);
  if (steps < 1) steps = 1;

  for (int i = 1; i <= steps; i++) {
    // Check for urgent serial commands during movement
    if (Serial.available()) {
      String urgent = Serial.readStringUntil('\n');
      urgent.trim();
      if (urgent.equalsIgnoreCase("PAUSE")) {
        paused = true;
        digitalWrite(enPin, HIGH);
        Serial.println(F("PAUSED"));
      } else if (urgent.equalsIgnoreCase("RESUME") || urgent.equalsIgnoreCase("R")) {
        paused = false;
        digitalWrite(enPin, LOW);
        Serial.println(F("RESUMED"));
      } else if (urgent.equalsIgnoreCase("CLEAR")) {
        paused = true;
        digitalWrite(enPin, HIGH);
        Serial.println(F("CLEARED"));
        return false; // Abort this movement
      }
    }

    while (paused) {
      delay(10);
      if (Serial.available()) {
        String urgent = Serial.readStringUntil('\n');
        urgent.trim();
        if (urgent.equalsIgnoreCase("RESUME") || urgent.equalsIgnoreCase("R")) {
          paused = false;
          digitalWrite(enPin, LOW);
          Serial.println(F("RESUMED"));
        } else if (urgent.equalsIgnoreCase("CLEAR")) {
          Serial.println(F("CLEARED"));
          return false; // Abort
        }
      }
    }
    
    float t = (float)i / steps;
    float interpTheta = curTheta + (distTheta * t);
    float interpRho = curRho + (distRho * t);
    
    moveToPolar(interpTheta, interpRho);
  }

  curTheta = targetTheta;
  curRho = targetRho;
  return true;
}

void moveToPolar(float theta, float rho) {
  float r_mm = rho * tableRadius;
  float x = r_mm * cos(theta);
  float y = r_mm * sin(theta);

  IKResult target = calculateIK(x, y);

  long dBase = round(target.baseSteps - curBaseSteps);
  long dElbow = round(target.elbowSteps - curElbowSteps);

  if (dBase != 0 || dElbow != 0) {
    // Calculate delay based on radius (center is fast, rim is slow)
    float rFactor = rho;
    if (rFactor > 1.0) rFactor = 1.0;
    int delayUs = centerDelay + ((perimeterDelay - centerDelay) * rFactor);
    
    moveBresenham(dElbow, dBase, delayUs * SPEED_MULTIPLIER);
    
    curBaseSteps = target.baseSteps;
    curElbowSteps = target.elbowSteps;
  }
}

float getShortestRotation(float raw, float last) {
  float diff = (raw - last) / (2.0 * PI);
  int offset = round(diff);
  return raw - (offset * 2.0 * PI);
}

IKResult calculateIK(float x, float y) {
  float dist = hypot(x, y);
  const float maxReach = L1 + L2;
  
  if (dist > maxReach) {
    float factor = maxReach / dist;
    x *= factor;
    y *= factor;
    dist = maxReach;
  }

  // --- CENTER PASS-THROUGH LOGIC ---
  if (dist < 1.0) {
    double targetBaseSteps = curBaseSteps;
    float currentBaseAngle = -curBaseSteps / stepsPerRad;
    double targetElbowSteps = -(PI + gearRatio * currentBaseAngle) * stepsPerRad;
    return { targetBaseSteps, targetElbowSteps };
  }

  float lastT1 = -curBaseSteps / stepsPerRad;
  float cosBend = (dist * dist - L1 * L1 - L2 * L2) / (2.0 * L1 * L2);
  if (cosBend > 1.0) cosBend = 1.0;
  if (cosBend < -1.0) cosBend = -1.0;

  float bend1 = acos(cosBend);
  float bend2 = -acos(cosBend);

  // Candidate 1
  float k1_1 = L1 + L2 * cos(bend1);
  float k2_1 = L2 * sin(bend1);
  float t1_1 = atan2(y, x) - atan2(k2_1, k1_1);
  t1_1 = getShortestRotation(t1_1, lastT1);

  // Candidate 2
  float k1_2 = L1 + L2 * cos(bend2);
  float k2_2 = L2 * sin(bend2);
  float t1_2 = atan2(y, x) - atan2(k2_2, k1_2);
  t1_2 = getShortestRotation(t1_2, lastT1);

  double b1_steps = -t1_1 * stepsPerRad;
  double e1_steps = -(bend1 + gearRatio * t1_1) * stepsPerRad;
  double b2_steps = -t1_2 * stepsPerRad;
  double e2_steps = -(bend2 + gearRatio * t1_2) * stepsPerRad;

  float cost1 = abs(b1_steps - curBaseSteps) + abs(e1_steps - curElbowSteps);
  float cost2 = abs(b2_steps - curBaseSteps) + abs(e2_steps - curElbowSteps);

  if (cost1 <= cost2) {
    return { b1_steps, e1_steps };
  } else {
    return { b2_steps, e2_steps };
  }
}

void moveBresenham(long da, long db, int delayUs) {
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
    
    delayMicroseconds(2);
    digitalWrite(stepArm, LOW);
    digitalWrite(stepBase, LOW);
    
    delayMicroseconds(delayUs);
  }
}
