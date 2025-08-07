// === KONSTANTEN ===
#define PWM_MIN          60
#define MAX_KOORDINATEN 50
#define PWM_MAX          200
#define RAMP_UP_TIME_MS   1000
#define RAMP_DOWN_TIME_MS 1000

#define RAD_DURCHMESSER_MM   96.0
#define ENCODER_IMPULSE_UMD  9600
#define RAD_UMFANG_MM        (PI * RAD_DURCHMESSER_MM)
#define IMPULSE_PRO_MM       (ENCODER_IMPULSE_UMD / RAD_UMFANG_MM)

// BTS7960 Pins (Räder)
#define RPWM_L 5
#define LPWM_L 6
#define RPWM_R 9
#define LPWM_R 10

// Encoder Pins (Räder)
#define ENC_L_A 2
#define ENC_L_B 3
#define ENC_R_A 18
#define ENC_R_B 19

// Arduino-Pins Schlitten-Motor X-Achse (Mikro-Motor)
#define RPWM_X 7
#define LPWM_X 8
#define ENC_X_A 20
#define ENC_X_B 21
#define END_X_L 30

// Arduino-Pins Schlitten-Motor Z-Achse (Mikro-Motor)
#define RPWM_Z 11
#define LPWM_Z 12
#define ENC_Z_A 22
#define ENC_Z_B 23
#define END_Z_O 31

// Arduino-Pins Bürstenmotor
#define PWM_BRUSH 6
#define ENCODER_BRUSH_A 24
#define ENCODER_BRUSH_B 25

// Bürstenmotor
#define BRUSH_CPR 211.2
#define BRUSH_TARGET_RPM 2200

#define ENCODER_X_CPR         1200
#define ENCODER_Z_CPR         1200
#define SCHRAUBEN_STEIGUNG_MM 8.0
#define IMPULSE_X_PRO_MM      (ENCODER_X_CPR / SCHRAUBEN_STEIGUNG_MM)
#define IMPULSE_Z_PRO_MM      (ENCODER_Z_CPR / SCHRAUBEN_STEIGUNG_MM)

#define MITTEX                300


volatile long encoderLinks = 0;
volatile long encoderRechts = 0;
volatile long encoderX = 0;
volatile long encoderZ = 0;

#define MAX_KOORDINATEN 50

struct Zielpunkt {
  float x_mm;
  float y_mm;
};

Zielpunkt ziele[MAX_KOORDINATEN];
int zielCount = 0;
float aktuelleY_mm = 0;

volatile long encoderBrush = 0;

// === ENCODER ISR ===
void isrEncoderLinks() { if (digitalRead(ENC_L_A) == digitalRead(ENC_L_B)) encoderLinks++; else encoderLinks--; }
void isrEncoderRechts() { if (digitalRead(ENC_R_A) == digitalRead(ENC_R_B)) encoderRechts++; else encoderRechts--; }
void isrEncoderX() { if (digitalRead(ENC_X_A) == digitalRead(ENC_X_B)) encoderX++; else encoderX--; }
void isrEncoderZ() { if (digitalRead(ENC_Z_A) == digitalRead(ENC_Z_B)) encoderZ++; else encoderZ--; }
void isrEncoderBrush() { encoderBrush++; }

void setup() {
  Serial1.begin(115200);  // Verbindung zum Raspberry Pi
  pinMode(RPWM_L, OUTPUT); pinMode(LPWM_L, OUTPUT);
  pinMode(RPWM_R, OUTPUT); pinMode(LPWM_R, OUTPUT);
  pinMode(RPWM_X, OUTPUT); pinMode(LPWM_X, OUTPUT);
  pinMode(RPWM_Z, OUTPUT); pinMode(LPWM_Z, OUTPUT);

  pinMode(ENC_L_A, INPUT); pinMode(ENC_L_B, INPUT);
  pinMode(ENC_R_A, INPUT); pinMode(ENC_R_B, INPUT);
  pinMode(ENC_X_A, INPUT); pinMode(ENC_X_B, INPUT);
  pinMode(ENC_Z_A, INPUT); pinMode(ENC_Z_B, INPUT);
  pinMode(END_X_L, INPUT_PULLUP);
  pinMode(END_Z_O, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(ENC_L_A), isrEncoderLinks, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_R_A), isrEncoderRechts, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_X_A), isrEncoderX, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_Z_A), isrEncoderZ, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENCODER_BRUSH_A), isrEncoderBrush, RISING);

  Serial.begin(9600);

  kalibriereX();
}

void kalibriereX() {
  while (digitalRead(END_X_L) == HIGH) {
    analogWrite(RPWM_X, 0);
    analogWrite(LPWM_X, PWM_MIN + 20);
    delay(10);
  }
  analogWrite(RPWM_X, 0);
  analogWrite(LPWM_X, 0);
  encoderX = 0;
  Serial.println("X kalibriert auf 0 mm");
}

void setzeXPosition(float zielPos_mm) {
  long zielImpulse = zielPos_mm * IMPULSE_X_PRO_MM;
  long deltaImpulse = zielImpulse - encoderX;
  bool vorwaerts = (deltaImpulse > 0);
  long zielAbsolut = encoderX + deltaImpulse;

  unsigned long startZeit = millis();
  unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
  unsigned long totalZeit = (unsigned long)(abs(deltaImpulse) / IMPULSE_X_PRO_MM * 12.0) + RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS;
  unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

  while ((vorwaerts && encoderX < zielAbsolut) || (!vorwaerts && encoderX > zielAbsolut)) {
    unsigned long jetzt = millis();
    int pwm = PWM_MIN;

    if (jetzt < rampUpEnd) pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
    else if (jetzt > rampDownStart) pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
    else pwm = PWM_MAX;

    analogWrite(RPWM_X, vorwaerts ? pwm : 0);
    analogWrite(LPWM_X, vorwaerts ? 0 : pwm);
    delay(10);
  }
  analogWrite(RPWM_X, 0);
  analogWrite(LPWM_X, 0);
  Serial.print("X gesetzt auf: "); Serial.print(zielPos_mm); Serial.println(" mm");
}

void kalibriereZ() {
  while (digitalRead(END_Z_O) == HIGH) {
    analogWrite(RPWM_Z, PWM_MIN + 20);
    analogWrite(LPWM_Z, 0);
    delay(10);
  }
  analogWrite(RPWM_Z, 0);
  analogWrite(LPWM_Z, 0);
  encoderZ = 0;
  Serial.println("Z kalibriert auf 0 mm (oben)");
}

unsigned long lastBrushCheck = 0;
long lastBrushTicks = 0;
float getBrushRPM() {
  unsigned long now = millis();
  unsigned long dt = now - lastBrushCheck;
  if (dt < 200) return -1;
  long dTicks = encoderBrush - lastBrushTicks;
  lastBrushTicks = encoderBrush;
  lastBrushCheck = now;
  return (dTicks * 60000.0) / (BRUSH_CPR * dt);
}

void senkeBuersteZuPosition(float zielPos_mm) {
  int versuch = 0;
  const float MIN_RPM = BRUSH_TARGET_RPM * 0.5;
  const float RECOVERY_RPM = BRUSH_TARGET_RPM * 0.9;
  const long rueckImpulse10mm = 10 * IMPULSE_Z_PRO_MM;
  const long zielImpulseZ = encoderZ + (zielPos_mm * IMPULSE_Z_PRO_MM);  // relativ zur aktuellen Position

  bool erfolgreich = false;

  while (versuch < 3 && !erfolgreich) {
    versuch++;
    encoderBrush = 0;
    lastBrushCheck = millis();
    lastBrushTicks = 0;

    // Bürste starten (Ramp-up)
    for (int pwm = 0; pwm <= 255; pwm += 5) {
      analogWrite(PWM_BRUSH, pwm);
      delay(10);
    }

    // Schlitten absenken
    unsigned long startZeit = millis();
    unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
    unsigned long totalZeit = RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS + 3000;
    unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

    bool drehzahlAbfall = false;

    while (encoderZ < zielImpulseZ) {
      unsigned long jetzt = millis();
      int pwm = PWM_MIN;

      if (jetzt < rampUpEnd) {
        pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
      } else if (jetzt > rampDownStart) {
        pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
      } else {
        pwm = PWM_MAX;
      }

      analogWrite(RPWM_Z, 0);
      analogWrite(LPWM_Z, pwm);

      float rpm = getBrushRPM();
      if (rpm > 0 && rpm < MIN_RPM) {
        Serial.print("Drehzahl zu niedrig (");
        Serial.print(rpm);
        Serial.println("), fahre 10 mm nach oben");

        drehzahlAbfall = true;

        // 10 mm nach oben (relativ zur aktuellen Position)
        long rueckZiel = encoderZ - rueckImpulse10mm;
        while (encoderZ > rueckZiel && digitalRead(END_Z_O) == HIGH) {
          analogWrite(RPWM_Z, PWM_MIN + 40);
          analogWrite(LPWM_Z, 0);
          delay(10);
        }

        analogWrite(RPWM_Z, 0);
        analogWrite(LPWM_Z, 0);
        break;  // neuer Versuch
      }

      delay(10);
    }

    if (encoderZ >= zielImpulseZ && !drehzahlAbfall) {
      erfolgreich = true;
    }
  }

  // Bürste stoppen
  analogWrite(PWM_BRUSH, 0);
  analogWrite(RPWM_Z, 0);
  analogWrite(LPWM_Z, 0);

  // Fahre immer auf Position 10 mm von oben (Impulsziel = encoderZ - delta)
  Serial.println("Fahre auf Position 10 mm (von oben)");
  long zielZurueck10mm = encoderZ - (encoderZ - rueckImpulse10mm);
  while (encoderZ > zielZurueck10mm && digitalRead(END_Z_O) == HIGH) {
    analogWrite(RPWM_Z, PWM_MIN + 40);
    analogWrite(LPWM_Z, 0);
    delay(10);
  }

  analogWrite(RPWM_Z, 0);
  analogWrite(LPWM_Z, 0);

  if (erfolgreich) {
    Serial.println("Ziel erreicht und zurück auf 10 mm.");
  } else {
    Serial.println("Ziel nicht erreicht nach 3 Versuchen. Position 10 mm angefahren.");
  }
}

void fahreStrecke(int strecke_mm, bool vorLinks, bool vorRechts) {
  encoderLinks = 0;
  encoderRechts = 0;
  long zielImpulse = strecke_mm * IMPULSE_PRO_MM;
  unsigned long startZeit = millis();
  unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
  unsigned long totalZeit = (unsigned long)((float)strecke_mm / (PWM_MAX / 255.0 * 100.0)) + RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS;
  unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

  while ((encoderLinks < zielImpulse) && (encoderRechts < zielImpulse)) {
    unsigned long jetzt = millis();
    int pwm = PWM_MIN;

    if (jetzt < rampUpEnd) pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
    else if (jetzt > rampDownStart) pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
    else pwm = PWM_MAX;

    long delta = encoderLinks - encoderRechts;
    const float kSync = 0.2;
    int pwmL = pwm - (delta * kSync);
    int pwmR = pwm + (delta * kSync);

    pwmL = constrain(pwmL, PWM_MIN, PWM_MAX);
    pwmR = constrain(pwmR, PWM_MIN, PWM_MAX);

    analogWrite(RPWM_L, vorLinks ? pwmL : 0);
    analogWrite(LPWM_L, vorLinks ? 0 : pwmL);
    analogWrite(RPWM_R, vorRechts ? pwmR : 0);
    analogWrite(LPWM_R, vorRechts ? 0 : pwmR);
    delay(10);
  }

  analogWrite(RPWM_L, 0); analogWrite(LPWM_L, 0);
  analogWrite(RPWM_R, 0); analogWrite(LPWM_R, 0);
  Serial.println("Fahrt beendet");
}

void anfrageUndAbarbeiten() {
  aktuelleY_mm = 0;
  Serial1.println("GETXY");

  zielCount = 0;
  unsigned long start = millis();
  while (millis() - start < 5000) {
    if (Serial1.available()) {
      String zeile = Serial1.readStringUntil('\n');
      zeile.trim();

      if (zeile == "DONE") break;
      if (zeile.startsWith("XY:")) {
        int kommateil = zeile.indexOf(',');
        if (kommateil > 3 && zielCount < MAX_KOORDINATEN) {
          float x = zeile.substring(3, kommateil).toFloat();
          float y = zeile.substring(kommateil + 1).toFloat();
          ziele[zielCount++] = {x, y};
        }
      }
    }
  }

  Serial.print("Empfangen: "); Serial.print(zielCount); Serial.println(" Koordinaten.");

  for (int i = 0; i < zielCount; i++) {
    float zielX = ziele[i].x_mm;
    float zielY = ziele[i].y_mm;
    float deltaY = zielY - aktuelleY_mm;

    setzeXPosition(zielX);  // absolut
    if (deltaY > 0.5) {
      fahreStrecke(deltaY, true, true);
      aktuelleY_mm += deltaY;
    }
    senkeBuersteZuPosition(40);
  }
}


void loop() {

  if (Serial1.available()) {
    String cmd = Serial1.readStringUntil('\n');
    cmd.trim();
    if (cmd == "START") {
      anfrageUndAbarbeiten();
    }
  }

  // Beispiel-Ablauf
  delay(1000);
  setzeXPosition(100.0);
  delay(1000);
  kalibriereZ();
  delay(1000);
  senkeBuersteZuPosition(160 * IMPULSE_Z_PRO_MM);
  delay(1000);
  fahreStrecke(500, true, true);
  delay(3000);
  while (1);
}

