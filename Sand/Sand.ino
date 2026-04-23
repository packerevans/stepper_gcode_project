#include <Arduino.h>

// --- PIN DEFINITIONS ---
const int stepBase = 7;
const int dirBase  = 8;
const int stepArm  = 5;
const int dirArm   = 16; 
const int enPin    = 15; 

// --- MOTION BUFFER ---
struct Move {
  long da;
  long db;
  int delayUs;
};

const int BUFFER_SIZE = 32; // Store up to 32 movement segments
Move moveBuffer[BUFFER_SIZE];
int head = 0; // Where we write new moves
int tail = 0; // Where we read moves to execute
int count = 0;

// Default "slow & smooth" delay
int globalDelay = 1500; 

void setup() {
  Serial.begin(250000);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  
  digitalWrite(enPin, LOW); 
  
  Serial.println(F("SMOOTH_READY"));
}

// Optimized Bresenham that runs at a strict timing
void executeMove(Move m) {
  if (m.da == 0 && m.db == 0) return;

  digitalWrite(dirArm, (m.da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (m.db >= 0) ? HIGH : LOW);
  
  long ad = abs(m.da);
  long bd = abs(m.db);
  long totalSteps = max(ad, bd);
  long accA = totalSteps / 2;
  long accB = totalSteps / 2;
  
  for (long i = 0; i < totalSteps; i++) {
    accA -= ad; 
    if (accA < 0) { 
      digitalWrite(stepArm, HIGH); 
      accA += totalSteps; 
    }
    accB -= bd; 
    if (accB < 0) { 
      digitalWrite(stepBase, HIGH); 
      accB += totalSteps; 
    }
    
    // Tiny pulse width
    delayMicroseconds(2); 
    digitalWrite(stepArm, LOW); 
    digitalWrite(stepBase, LOW);
    
    // This is the "spacing" that makes it smooth
    delayMicroseconds(m.delayUs);
  }
}

void loop() {
  // 1. Read Serial while moving (Background Task)
  if (Serial.available() > 0 && count < BUFFER_SIZE) {
    String input = Serial.readStringUntil('\n');
    input.trim();

    if (input.startsWith("SPEED")) {
      globalDelay = input.substring(6).toInt();
      Serial.print(F("STRICT_DELAY:")); Serial.println(globalDelay);
    } 
    else {
      int spaceIndex = input.indexOf(' ');
      if (spaceIndex != -1) {
        // Add move to the buffer
        moveBuffer[head].da = input.substring(0, spaceIndex).toInt();
        moveBuffer[head].db = input.substring(spaceIndex + 1).toInt();
        moveBuffer[head].delayUs = globalDelay;
        
        head = (head + 1) % BUFFER_SIZE;
        count++;
        
        // Tell the sender we are ready for more immediately!
        Serial.println(F("OK")); 
      }
    }
  }

  // 2. Execute moves from the buffer
  if (count > 0) {
    executeMove(moveBuffer[tail]);
    tail = (tail + 1) % BUFFER_SIZE;
    count--;
  }
}
