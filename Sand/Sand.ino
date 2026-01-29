// ==========================================
//      DEBUG MODE: SAND TABLE FIRMWARE
// ==========================================

const int stepBase = 8;
const int dirBase  = 9;
const int stepArm  = 10;
const int dirArm   = 11;
const int enPin    = 3;

// Microstepping Pins
const int ms1 = 4;
const int ms2 = 5;
const int ms3 = 6;

#define BAUD_RATE 250000  
const float SPEED_MULTIPLIER = 2.0;

bool paused = true;
const int MAX_QUEUE_SIZE = 50;

// Command Types
const int CMD_MOVE = 0;
const int CMD_WAIT = 1;

struct GCommand {
  int type;
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

  pinMode(ms1, OUTPUT); pinMode(ms2, OUTPUT); pinMode(ms3, OUTPUT);
  digitalWrite(ms1, HIGH); digitalWrite(ms2, HIGH); digitalWrite(ms3, HIGH);

  digitalWrite(enPin, HIGH);
  
  // --- DEBUG 1: PROVE STARTUP ---
  Serial.println(F("--- DEBUG: SYSTEM START ---"));
  Serial.println(F("LGT_READY"));
}

void loop() {
  // Check if data is arriving
  if (Serial.available()) {
    handleSerial();
  }

  if (!paused && queueCount > 0) {
    executeNextCommand();
  }
}

void handleSerial() {
  // --- DEBUG 2: PROVE DATA ARRIVAL ---
  Serial.println(F("DEBUG: Serial Data Detected!")); 
  
  while (Serial.available() > 0) {
    // Read the incoming line
    String cmd = Serial.readStringUntil('\n');
    
    // --- DEBUG 3: SHOW WHAT WE HEARD ---
    Serial.print(F("DEBUG: Raw Input: ["));
    Serial.print(cmd);
    Serial.println(F("]"));

    cmd.trim();
    
    // --- DEBUG 4: SHOW TRIMMED COMMAND ---
    Serial.print(F("DEBUG: Trimmed Input: ["));
    Serial.print(cmd);
    Serial.println(F("]"));

    if (cmd.length() == 0) {
        Serial.println(F("DEBUG: Ignoring empty line."));
        continue;
    }

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
      Serial.println(F("Cleared"));
    }
    else if (cmd.startsWith("G1")) {
      Serial.println(F("DEBUG: G1 Move Identified"));
      parseAndEnqueueMove(cmd);
    }
    else if (cmd.startsWith("M0") || cmd.startsWith("m0")) {
      enqueuePause();
    }
    else {
      // --- DEBUG 5: CATCH UNKNOWN COMMANDS ---
      Serial.println(F("DEBUG: Unknown Command!"));
    }
  }
}

void enqueuePause() {
  if (queueCount < MAX_QUEUE_SIZE) {
    queue[queueTail].type = CMD_WAIT;
    queue[queueTail].armSteps = 0;
    queue[queueTail].baseSteps = 0;
    queue[queueTail].speed = 0;
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

  Serial.print(F("DEBUG: Queuing Move -> Arm:"));
  Serial.print(armSteps);
  Serial.print(F(" Base:"));
  Serial.println(baseSteps);

  if (queueCount < MAX_QUEUE_SIZE) {
    queue[queueTail].type = CMD_MOVE;
    queue[queueTail].armSteps = armSteps;
    queue[queueTail].baseSteps = baseSteps;
    queue[queueTail].speed = speed;
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
  
  if (cmd.type == CMD_MOVE) {
    moveBresenham(cmd.armSteps, cmd.baseSteps, cmd.speed);
    Serial.println(F("Done"));
  } 
  else if (cmd.type == CMD_WAIT) {
    performProgrammedPause();
  }
}

void performProgrammedPause() {
  Serial.println(F("Done")); 
  digitalWrite(enPin, HIGH);
  bool waiting = true;
  while (waiting) {
    if (Serial.available()) {
      // READ AND RESPOND DURING PAUSE
      String input = Serial.readStringUntil('\n');
      input.trim();
      if (input.equalsIgnoreCase("R") || input.equalsIgnoreCase("RESUME")) {
        waiting = false;
        digitalWrite(enPin, LOW);
      }
      if (input.equalsIgnoreCase("PAUSE")) {
        paused = true;
      }
    }
    delay(50);
  }
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

  int finalDelay = delayUs * SPEED_MULTIPLIER; 

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
    
    delayMicroseconds(finalDelay);
  }
}

void pulsePin(int pin) {
  digitalWrite(pin, HIGH);
  delayMicroseconds(2);
  digitalWrite(pin, LOW);
}
