#include <Arduino.h>

// --- PIN DEFINITIONS ---
const int stepBase = 7;
const int dirBase  = 8;
const int stepArm  = 5;
const int dirArm   = 16; 
const int enPin    = 15; 

// --- MOVEMENT SETTINGS ---
// Higher delay = Slower movement. 
// 1000 is medium, 3000 is slow, 400 is very fast.
int stepDelayMicros = 2000; 

void setup() {
  Serial.begin(250000);
  
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  
  digitalWrite(enPin, LOW); // Energize motors
  
  Serial.println(F("--- STEPPER CONTROL READY ---"));
  Serial.println(F("Usage: 'ArmSteps BaseSteps' (e.g., 3200 1600)"));
  Serial.println(F("Speed: 'SPEED Value' (e.g., SPEED 1000)"));
}

void moveBresenham(long da, long db, int delayUs) {
  // Set Directions
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
    
    // Pulse width
    delayMicroseconds(2); 
    digitalWrite(stepArm, LOW); 
    digitalWrite(stepBase, LOW);
    
    // Speed control delay
    delayMicroseconds(max(2, delayUs));
  }
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();

    // Handle SPEED Command
    if (input.startsWith("SPEED")) {
      int newSpeed = input.substring(6).toInt();
      if (newSpeed > 0) {
        stepDelayMicros = newSpeed;
        Serial.print(F("Step delay set to: "));
        Serial.print(stepDelayMicros);
        Serial.println(F(" us"));
      }
    } 
    // Handle Step Movement (two numbers)
    else {
      int spaceIndex = input.indexOf(' ');
      if (spaceIndex != -1) {
        long arm = input.substring(0, spaceIndex).toInt();
        long base = input.substring(spaceIndex + 1).toInt();
        
        Serial.print(F("Moving Arm: ")); Serial.print(arm);
        Serial.print(F(" | Base: ")); Serial.println(base);
        
        moveBresenham(arm, base, stepDelayMicros);
        Serial.println(F("OK"));
      }
    }
  }
}
