/*
 * Sand Table Firmware - Theta-Rho Processor
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
const float gearRatio = 1.125;
const float stepsPerDeg = 8.888888;
const float stepsPerRad = stepsPerDeg * (180.0 / PI);
const float interpolationRes = 1.0; // LGT8 @ 32MHz easily handles 1.0mm

// --- SPEED SETTINGS ---
int centerDelay = 800;     // INCREASED from 500 for hardware safety  
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
int lastDelayUs = 0; // Added to track acceleration intervals

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
void processRgbLine(char* line);
bool processThrLine(char* line);
void processModeCommand(char* data);
void moveToPolar(float theta, float rho);
IKResult calculateIK(float x, float y);
void moveBresenham(long da, long db, int delayUs);

void setup() {
  Serial.begin(BAUD_RATE);
  Serial.setTimeout(10);

  // Load ALL saved positions from EEPROM (Steps AND Coordinates)
  int eeAddr = 0;
  EEPROM.get(eeAddr, curBaseSteps); eeAddr += sizeof(long);
  EEPROM.get(eeAddr, curElbowSteps); eeAddr += sizeof(long);
  EEPROM.get(eeAddr, curTheta); eeAddr += sizeof(float);
  EEPROM.get(eeAddr, curRho);
  
  // Sanity check: if EEPROM is fresh (all 255/FF), initialize to perfect center
  if (curBaseSteps == -1 && curElbowSteps == -1) {
