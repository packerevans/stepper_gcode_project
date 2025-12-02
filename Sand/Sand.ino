// ==========================================
//      ARDUINO SAND TABLE FIRMWARE v6
//      Center-Safe & Buffer Fix
// ==========================================

const int stepBase = 8;
const int dirBase  = 9;
const int stepArm  = 10;
const int dirArm   = 11;
const int enPin    = 3;

// Settings
#define BAUD_RATE 115200 // Ensure your Raspberry Pi uses 115200 too!

bool paused = true; 
const int MAX_QUEUE_SIZE = 50;

struct GCommand {
  int type; // 0=Move, 1=Wait
  long arm;
  long base;
  int speed;
};

GCommand queue[MAX_QUEUE_SIZE];
int head = 0;
int tail = 0;
int count = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  pinMode(stepBase, OUTPUT); pinMode(dirBase, OUTPUT);
  pinMode(stepArm, OUTPUT);  pinMode(dirArm, OUTPUT);
  pinMode(enPin, OUTPUT);
  digitalWrite(enPin, HIGH); // Start Disabled
  Serial.println("READY");
}

void loop() {
  if (Serial.available()) handleSerial();
  if (!paused && count > 0) executeNext();
}

void handleSerial() {
  while (Serial.available() > 0) {
    String c = Serial.readStringUntil('\n');
    c.trim();
    if (c.length() == 0) continue;

    if (c.equalsIgnoreCase("PAUSE")) { paused = true; digitalWrite(enPin, HIGH); Serial.println("Paused"); }
    else if (c.equalsIgnoreCase("RESUME") || c.equalsIgnoreCase("R")) { paused = false; digitalWrite(enPin, LOW); Serial.println("Resumed"); }
    else if (c.equalsIgnoreCase("CLEAR")) { head = tail = count = 0; Serial.println("Cleared"); }
    else if (c.startsWith("G1")) parseMove(c);
    else if (c.startsWith("M0")) parseWait();
  }
}

void parseWait() {
  if (count < MAX_QUEUE_SIZE) {
    queue[tail] = {1, 0, 0, 0};
    tail = (tail + 1) % MAX_QUEUE_SIZE;
    count++;
  } else Serial.println("ERR:FULL");
}

void parseMove(String c) {
  // G1 <Elbow> <Base> <Speed>
  int s1 = c.indexOf(' ');
  int s2 = c.indexOf(' ', s1 + 1);
  int s3 = c.indexOf(' ', s2 + 1);
  
  if (s1 > 0 && s2 > 0) {
    long arm = c.substring(s1 + 1, s2).toInt();
    long base = (s3 > 0) ? c.substring(s2 + 1, s3).toInt() : c.substring(s2 + 1).toInt();
    int spd = (s3 > 0) ? c.substring(s3 + 1).toInt() : 1000;

    if (count < MAX_QUEUE_SIZE) {
      queue[tail] = {0, arm, base, spd};
      tail = (tail + 1) % MAX_QUEUE_SIZE;
      count++;
    } else Serial.println("ERR:FULL");
  }
}

void executeNext() {
  GCommand cmd = queue[head];
  head = (head + 1) % MAX_QUEUE_SIZE;
  count--;

  if (cmd.type == 0) {
    move(cmd.arm, cmd.base, cmd.speed);
    Serial.println("Done"); 
  } else {
    // BLOCKING WAIT
    Serial.println("PAUSED_AT_M0");
    digitalWrite(enPin, HIGH); 
    while (true) {
      if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        if (s.equalsIgnoreCase("R") || s.equalsIgnoreCase("RESUME")) break;
      }
      delay(50);
    }
    digitalWrite(enPin, LOW);
    Serial.println("RESUMED_FROM_M0");
  }
}

void move(long da, long db, int dly) {
  digitalWrite(dirArm, (da >= 0) ? HIGH : LOW);
  digitalWrite(dirBase, (db >= 0) ? HIGH : LOW);
  long ad = abs(da), bd = abs(db);
  long steps = max(ad, bd);
  long aa = steps/2, ab = steps/2;

  for (long i=0; i<steps; i++) {
    if (Serial.available()) handleSerial();
    if (paused) break;
    
    aa -= ad; if (aa < 0) { digitalWrite(10, HIGH); delayMicroseconds(2); digitalWrite(10, LOW); aa += steps; }
    ab -= bd; if (ab < 0) { digitalWrite(8, HIGH); delayMicroseconds(2); digitalWrite(8, LOW); ab += steps; }
    
    delayMicroseconds(dly);
  }
}
