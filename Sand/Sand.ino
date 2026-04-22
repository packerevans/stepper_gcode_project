#include <Arduino.h>

// --- PIN DEFINITIONS ---
const int stepBase = 7;
const int dirBase  = 8;
const int stepArm  = 5;
const int dirArm   = 16; 
const int enPin    = 15; 

void setup() {
  Serial.begin(250000);
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  digitalWrite(enPin, LOW); // Energize motors
  Serial.println(F("READY: Enter 'ArmSteps BaseSteps' (e.g. 3200 3200)"));
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
    delayMicroseconds(max(1, delayUs));
  }
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    int spaceIndex = input.indexOf(' ');
    
    if (spaceIndex != -1) {
      long arm = input.substring(0, spaceIndex).toInt();
      long base = input.substring(spaceIndex + 1).toInt();
      
      moveBresenham(arm, base, 800); // 800us delay = medium speed
      Serial.println(F("DONE"));
    }
  }
}
