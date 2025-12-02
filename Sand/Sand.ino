// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v2
//      Optimized with Bresenham's Algorithm
// ==========================================

// --- MOTOR PIN DEFINITIONS ---
const int dirBase = 9;
const int stepBase = 8;
const int dirArm = 11;
const int stepArm = 10;
const int enPin = 3;

// Microstepping Pins
const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

// --- STATE VARIABLES ---
bool paused = true;
const int MAX_QUEUE_SIZE = 50;

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
  Serial.begin(9600);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  
  // 1/32 Microstepping Setup
  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, HIGH); // Start Disabled
  Serial.println(F("READY"));
}

void loop() {
  handleSerial();
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
    else if (cmd.equalsIgnoreCase("RESUME")) {
      paused = false;
      digitalWrite(enPin, LOW);
      Serial.println(F("Resumed"));
    }
    else if (cmd.equalsIgnoreCase("CLEAR")) {
      queueHead = queueTail = queueCount = 0;
      Serial.println(F("Cleared"));
    }
    else if (cmd.startsWith("G1")) {
      parseAndEnqueue(cmd);
    }
  }
}

void parseAndEnqueue(String cmd) {
  // G1 <Elbow> <Base> <Speed>
  long armSteps = 0;
  long baseSteps = 0;
  int speed = 1000;

  int s1 = cmd.indexOf(' ');
  int s2 = cmd.indexOf(' ', s1 + 1);
  int s3 = cmd.indexOf(' ', s2 + 1);

  if (s1 > 0) {
    armSteps = cmd.substring(s1 + 1, s2).toInt();
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
    queue[queueTail] = {armSteps, baseSteps, speed};
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
  
  // Use pure integer Bresenham algorithm
  moveBresenham(cmd.armSteps, cmd.baseSteps, cmd.speed);
  
  Serial.println(F("Done"));
}

void moveBresenham(long da, long db, int delayUs) {
  digitalWrite(enPin, LOW); // Enable motors

  // Set Directions
  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);

  long ad = abs(da);
  long bd = abs(db);
  
  long steps = max(ad, bd); // Total steps for the dominant axis

  // Bresenham's accumulators (start at half threshold to distribute error evenly)
  long accA = steps / 2;
  long accB = steps / 2;

  for (long i = 0; i < steps; i++) {
    // Check Serial strictly every 200 steps to prevent stuttering on fast moves
    // but still allow pausing long moves
    if (i % 200 == 0 && Serial.available()) handleSerial();
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
  delayMicroseconds(2); // Short pulse is sufficient for drivers
  digitalWrite(pin, LOW);
}
