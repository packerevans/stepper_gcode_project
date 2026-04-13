/*
 * Sand Table Firmware - Theta-Rho Processor
 * Optimized for NEMA 17 Stepper Motors (SCARA Arm)
 */

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
const float interpolationRes = 1.0; 

// --- SPEED SETTINGS ---
int centerDelay = 500;    
int perimeterDelay = 3000; 
float SPEED_MULTIPLIER = 1.0;

// --- STATE VARIABLES ---
float curTheta = 0;
float curRho = 0;
long curBaseSteps = 0;
long curElbowSteps = 0;
bool paused = false;

#define BAUD_RATE 250000
char serialBuf[64];
int bufIdx = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);

  pinMode(elbowStopPin, INPUT_PULLUP);
  pinMode(baseStopPin, INPUT_PULLUP);

  pinMode(redPin, OUTPUT); pinMode(greenPin, OUTPUT); pinMode(bluePin, OUTPUT);

  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, LOW); 
  Serial.println(F("SAND_TABLE_READY"));
}

void loop() {
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
  String line = String(cmd);
  line.trim();
  if (line.length() == 0 || line.startsWith("#")) return;

  if (line.equalsIgnoreCase("PAUSE")) {
    paused = true; digitalWrite(enPin, HIGH);
    Serial.println(F("PAUSED"));
  }
  else if (line.equalsIgnoreCase("RESUME") || line.equalsIgnoreCase("R")) {
    paused = false; digitalWrite(enPin, LOW);
    Serial.println(F("RESUMED"));
  }
  else if (line.equalsIgnoreCase("CLEAR")) {
    paused = true; digitalWrite(enPin, HIGH);
    Serial.println(F("CLEARED"));
  }
  else if (line.equalsIgnoreCase("CALIBRATE")) {
    calibrate();
  }
  else if (line.startsWith("SPEED ")) {
    float newMult = line.substring(6).toFloat();
    if (newMult > 0.1 && newMult < 10.0) {
      SPEED_MULTIPLIER = newMult;
      centerDelay = 500 / SPEED_MULTIPLIER;
      perimeterDelay = 3000 / SPEED_MULTIPLIER;
      Serial.print(F("SPEED_SET:")); Serial.println(SPEED_MULTIPLIER);
    }
  }
  else if (line.indexOf(',') != -1) {
    processRgbLine(line);
    Serial.println(F("RGB_OK"));
  }
  else {
    if (processThrLine(line)) Serial.println(F("OK"));
  }
}

void calibrate() {
  paused = false; digitalWrite(enPin, LOW);
  Serial.println(F("STATUS:CALIBRATING"));
  digitalWrite(dirBase, HIGH); 
  while (digitalRead(baseStopPin) == HIGH) {
    digitalWrite(stepBase, HIGH); delayMicroseconds(2);
    digitalWrite(stepBase, LOW);  delayMicroseconds(2000); 
  }
  curBaseSteps = 0; 
  digitalWrite(dirArm, HIGH);
  while (digitalRead(elbowStopPin) == HIGH) {
    digitalWrite(stepArm, HIGH); delayMicroseconds(2);
    digitalWrite(stepArm, LOW);  delayMicroseconds(2000);
  }
  curElbowSteps = 0; curTheta = 0; curRho = 0;
  Serial.println(F("CALIBRATION_COMPLETE"));
}

void processRgbLine(String line) {
  int firstComma = line.indexOf(',');
  int secondComma = line.indexOf(',', firstComma + 1);
  if (firstComma != -1 && secondComma != -1) {
    analogWrite(redPin, line.substring(0, firstComma).toInt());
    analogWrite(greenPin, line.substring(firstComma + 1, secondComma).toInt());
    analogWrite(bluePin, line.substring(secondComma + 1).toInt());
  }
}

bool processThrLine(String line) {
  int spaceIdx = line.indexOf(' ');
  if (spaceIdx == -1) return false;

  float targetTheta = line.substring(0, spaceIdx).toFloat();
  float targetRho = line.substring(spaceIdx + 1).toFloat();

  float distTheta = targetTheta - curTheta;
  float distRho = targetRho - curRho;
  
  float avgR = ((curRho + targetRho) / 2.0) * tableRadius;
  float totalDist = sqrt(pow(avgR * abs(distTheta), 2) + pow(abs(distRho * tableRadius), 2));
  
  int steps = ceil(totalDist / interpolationRes);
  if (steps < 1) steps = 1;

  for (int i = 1; i <= steps; i++) {
    // Check for EMERGENCY commands only without consuming the buffer
    if (Serial.available()) {
       char peek = Serial.peek();
       if (peek == 'P' || peek == 'p' || peek == 'C' || peek == 'c' || peek == 'R' || peek == 'r') {
          // A command is starting, exit movement to handle it
          return false; 
       }
    }

    while (paused) {
      delay(10);
      if (Serial.available()) return false;
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
  moveBresenham(target.elbowSteps - curElbowSteps, target.baseSteps - curBaseSteps, centerDelay + ((perimeterDelay - centerDelay) * (rho > 1.0 ? 1.0 : rho)));
  curBaseSteps = target.baseSteps; curElbowSteps = target.elbowSteps;
}

IKResult calculateIK(float x, float y) {
  float dist = hypot(x, y);
  const float maxReach = L1 + L2;
  if (dist > maxReach) { x *= (maxReach/dist); y *= (maxReach/dist); dist = maxReach; }
  if (dist < 1.0) return { 0, (long)(-PI * stepsPerRad) }; 
  float lastT1 = -(float)curBaseSteps / stepsPerRad;
  float cosBend = (dist * dist - L1 * L1 - L2 * L2) / (2.0 * L1 * L2);
  float bend = acos(max(-1.0f, min(1.0f, cosBend)));
  float t1 = atan2(y, x) - atan2(L2 * sin(bend), L1 + L2 * cos(bend));
  t1 = t1 - (round((t1 - lastT1) / (2.0 * PI)) * 2.0 * PI);
  return { (long)round(-t1 * stepsPerRad), (long)round(-(bend + gearRatio * t1) * stepsPerRad) };
}

void moveBresenham(long da, long db, int delayUs) {
  if (da == 0 && db == 0) return;
  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);
  long ad = abs(da), bd = abs(db), steps = max(ad, bd), accA = steps/2, accB = steps/2;
  for (long i = 0; i < steps; i++) {
    accA -= ad; if (accA < 0) { digitalWrite(stepArm, HIGH); accA += steps; }
    accB -= bd; if (accB < 0) { digitalWrite(stepBase, HIGH); accB += steps; }
    delayMicroseconds(2); digitalWrite(stepArm, LOW); digitalWrite(stepBase, LOW);
    delayMicroseconds(max(1, delayUs));
  }
}
