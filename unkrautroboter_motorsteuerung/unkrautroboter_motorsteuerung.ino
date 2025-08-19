#include <ArduinoJson.h>
// === KONSTANTEN ===
#define PWM_MIN 60
#define MAX_KOORDINATEN 50
#define PWM_MAX 200
#define RAMP_UP_TIME_MS 1000
#define RAMP_DOWN_TIME_MS 1000

#define RAD_DURCHMESSER_MM 96.0
#define ENCODER_IMPULSE_UMD 9600
#define RAD_UMFANG_MM (PI * RAD_DURCHMESSER_MM)
#define IMPULSE_PRO_MM (ENCODER_IMPULSE_UMD / RAD_UMFANG_MM)

// Betriebsmodus
enum Mode {
    WAITING_FOR_START, // Initialer Zustand
    MANUAL,
    AUTO
};

Mode currentMode = WAITING_FOR_START; // Startet im Wartezustand

// Rad links
#define RPWM_L 5
#define LPWM_L 6

#define ENC_L_A 2 // Interrupt
#define ENC_L_B 22

// Rad rechts
#define RPWM_R 7
#define LPWM_R 8

#define ENC_R_A 3 // Interrupt
#define ENC_R_B 23

// Arduino-Pins Schlitten-Motor X-Achse (Mikro-Motor)
#define RPWM_X 44
#define LPWM_X 45

#define ENC_X_A 18 // Interrupt
#define ENC_X_B 24

#define END_X_L 27
#define END_X_R 28

// Arduino-Pins Schlitten-Motor Z-Achse (Mikro-Motor)
#define RPWM_Z 11
#define LPWM_Z 12

#define ENC_Z_A 19 // Interrupt
#define ENC_Z_B 25

#define END_Z_O 29
#define END_Z_U 30

// Arduino-Pins Bürstenmotor
#define PWM_BRUSH 44

#define ENCODER_BRUSH_A 20 // Interrupt
#define ENCODER_BRUSH_B 26

// Bürstenmotor
#define BRUSH_CPR 211.2
#define BRUSH_TARGET_RPM 2200

#define ENCODER_X_CPR 1200
#define ENCODER_Z_CPR 1200
#define SCHRAUBEN_STEIGUNG_MM 8.0
#define IMPULSE_X_PRO_MM (ENCODER_X_CPR / SCHRAUBEN_STEIGUNG_MM)
#define IMPULSE_Z_PRO_MM (ENCODER_Z_CPR / SCHRAUBEN_STEIGUNG_MM)

#define MITTEX 300

volatile long encoderLinks = 0;
volatile long encoderRechts = 0;
volatile long encoderX = 0;
volatile long encoderZ = 0;

// Maximale Länge für einen Befehl über die serielle Schnittstelle
#define MAX_CMD_LENGTH 50

#define MAX_KOORDINATEN 50
struct Zielpunkt {
    float x_mm;
    float y_mm;
};

Zielpunkt ziele[MAX_KOORDINATEN];
int zielCount = 0;
float aktuelleY_mm = 0;

volatile long encoderBrush = 0;

// --- 18 kHz PWM-Initialisierung für alle 16-Bit-Timer (1,3,4,5) inkl. Bürste ---
const uint16_t MOTOR_PWM_TOP = 888; // ~18 kHz bei 16 MHz, N=1

void initMotorPWM18kHz() {
    // Timer1: Pins 11 (OC1A), 12 (OC1B)
    TCCR1A = 0;
    TCCR1B = 0;
    TCNT1 = 0;
    TCCR1A |= (1 << WGM11);
    TCCR1B |= (1 << WGM12) | (1 << WGM13);
    TCCR1A |= (1 << COM1A1) | (1 << COM1B1); // Nicht-invertierend A/B
    ICR1 = MOTOR_PWM_TOP;
    TCCR1B |= (1 << CS10); // Prescaler 1
    OCR1A = 0;
    OCR1B = 0;

    // Timer3: Pin 5 (OC3A)
    TCCR3A = 0;
    TCCR3B = 0;
    TCNT3 = 0;
    TCCR3A |= (1 << WGM31);
    TCCR3B |= (1 << WGM32) | (1 << WGM33);
    TCCR3A |= (1 << COM3A1); // Nicht-invertierend A
    ICR3 = MOTOR_PWM_TOP;
    TCCR3B |= (1 << CS30);
    OCR3A = 0;

    // Timer4: Pins 6 (OC4A), 7 (OC4B), 8 (OC4C)
    TCCR4A = 0;
    TCCR4B = 0;
    TCNT4 = 0;
    TCCR4A |= (1 << WGM41);
    TCCR4B |= (1 << WGM42) | (1 << WGM43);
    TCCR4A |= (1 << COM4A1) | (1 << COM4B1) | (1 << COM4C1);
    ICR4 = MOTOR_PWM_TOP;
    TCCR4B |= (1 << CS40);
    OCR4A = 0;
    OCR4B = 0;
    OCR4C = 0;

    // Timer5: Pin 44 (OC5C) - Bürste
    pinMode(PWM_BRUSH, OUTPUT);
    TCCR5A = 0;
    TCCR5B = 0;
    TCNT5 = 0;
    TCCR5A |= (1 << WGM51);
    TCCR5B |= (1 << WGM52) | (1 << WGM53);
    TCCR5A |= (1 << COM5C1); // Nicht-invertierend C
    ICR5 = MOTOR_PWM_TOP;
    TCCR5B |= (1 << CS50);
    OCR5C = 0;
}

// PWM schreiben (0..255 auf 0..TOP abbilden)
void motorAnalogWrite(uint8_t pin, uint8_t pwm) {
    uint16_t val = (uint32_t)pwm * MOTOR_PWM_TOP / 255u;
    switch (pin) {
    case 5:
        OCR3A = val;
        break; // Timer3, OC3A
    case 6:
        OCR4A = val;
        break; // Timer4, OC4A
    case 7:
        OCR4B = val;
        break; // Timer4, OC4B
    case 8:
        OCR4C = val;
        break; // Timer4, OC4C
    case 11:
        OCR1A = val;
        break; // Timer1, OC1A
    case 12:
        OCR1B = val;
        break; // Timer1, OC1B
    case 44:
        OCR5C = val;
        break; // Timer5, OC5C (Bürste)
    case 45:
        OCR5B = val;
        break; // Timer5, OC5B
    default:
        analogWrite(pin, pwm); // Fallback
    }
}

// === ENCODER ISR ===
void isrEncoderLinks() {
    if (digitalRead(ENC_L_A) == digitalRead(ENC_L_B))
        encoderLinks++;
    else
        encoderLinks--;
}
void isrEncoderRechts() {
    if (digitalRead(ENC_R_A) == digitalRead(ENC_R_B))
        encoderRechts++;
    else
        encoderRechts--;
}
void isrEncoderX() {
    if (digitalRead(ENC_X_A) == digitalRead(ENC_X_B))
        encoderX++;
    else
        encoderX--;
}
void isrEncoderZ() {
    if (digitalRead(ENC_Z_A) == digitalRead(ENC_Z_B))
        encoderZ++;
    else
        encoderZ--;
}
void isrEncoderBrush() { encoderBrush++; }

void setup() {

    Serial.begin(115200);  // Debug-Ausgaben für die IDE
    Serial2.begin(115200); // Verbindung zum Raspberry Pi
    Serial.println("Programm gestartet");

    pinMode(RPWM_L, OUTPUT);
    pinMode(LPWM_L, OUTPUT);
    pinMode(RPWM_R, OUTPUT);
    pinMode(LPWM_R, OUTPUT);
    pinMode(RPWM_X, OUTPUT);
    pinMode(LPWM_X, OUTPUT);
    pinMode(RPWM_Z, OUTPUT);
    pinMode(LPWM_Z, OUTPUT);
    initMotorPWM18kHz();

    pinMode(ENC_L_A, INPUT);
    pinMode(ENC_L_B, INPUT);
    pinMode(ENC_R_A, INPUT);
    pinMode(ENC_R_B, INPUT);
    pinMode(ENC_X_A, INPUT);
    pinMode(ENC_X_B, INPUT);
    pinMode(ENC_Z_A, INPUT);
    pinMode(ENC_Z_B, INPUT);
    pinMode(END_X_L, INPUT_PULLUP);
    pinMode(END_Z_O, INPUT_PULLUP);
    pinMode(END_X_R, INPUT_PULLUP);
    pinMode(END_Z_U, INPUT_PULLUP);

    attachInterrupt(digitalPinToInterrupt(ENC_L_A), isrEncoderLinks, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_R_A), isrEncoderRechts, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_X_A), isrEncoderX, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_Z_A), isrEncoderZ, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENCODER_BRUSH_A), isrEncoderBrush, RISING);

    kalibriereX();
    kalibriereZ();
}

void kalibriereX() {
    Serial.println("Kalibriere X");
    while (digitalRead(END_X_L) == HIGH) {
        motorAnalogWrite(RPWM_X, 0);
        motorAnalogWrite(LPWM_X, PWM_MIN + 20);
        delay(10);
    }
    motorAnalogWrite(RPWM_X, 0);
    motorAnalogWrite(LPWM_X, 0);
    encoderX = 0;
    Serial.println("X kalibriert auf 0 mm (links)");
}

void kalibriereZ() {
    Serial.println("Kalibriere Z");
    while (digitalRead(END_Z_O) == HIGH) {
        motorAnalogWrite(RPWM_Z, PWM_MIN + 20);
        motorAnalogWrite(LPWM_Z, 0);
        delay(10);
    }
    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);
    encoderZ = 0;
    Serial.println("Z kalibriert auf 0 mm (oben)");
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

    while ((vorwaerts && encoderX < zielAbsolut && digitalRead(END_X_R) == HIGH) ||
           (!vorwaerts && encoderX > zielAbsolut && digitalRead(END_X_L) == HIGH)) {
        unsigned long jetzt = millis();
        int pwm = PWM_MIN;

        if (jetzt < rampUpEnd)
            pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
        else if (jetzt > rampDownStart)
            pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
        else
            pwm = PWM_MAX;

        motorAnalogWrite(RPWM_X, vorwaerts ? pwm : 0);
        motorAnalogWrite(LPWM_X, vorwaerts ? 0 : pwm);
        delay(10);
    }
    motorAnalogWrite(RPWM_X, 0);
    motorAnalogWrite(LPWM_X, 0);
    // Hinweis, falls Endschalter erreicht wurde
    if (vorwaerts && digitalRead(END_X_R) == LOW) {
        Serial.println("X: Rechter Endschalter erreicht – vorzeitig gestoppt");
    } else if (!vorwaerts && digitalRead(END_X_L) == LOW) {
        Serial.println("X: Linker Endschalter erreicht – vorzeitig gestoppt");
    }
    Serial.print("X gesetzt auf: ");
    Serial.print(zielPos_mm);
    Serial.println(" mm");
    Serial.print("Impulsstand Ist: ");
    Serial.print(encoderX);
    Serial.print(" / Impulsstand Soll: ");
    Serial.println(zielAbsolut);
}

// Z-Achse absolut auf Zielposition in mm verfahren (analog zu setzeXPosition)
// vorwaerts=true bedeutet Absenken (nach unten), false Anheben (nach oben)
// Beim Anheben wird der obere Endschalter END_Z_O respektiert
void setzeZPosition(float zielPos_mm) {
    long zielImpulse = zielPos_mm * IMPULSE_Z_PRO_MM;
    long deltaImpulse = zielImpulse - encoderZ;
    bool vorwaerts = (deltaImpulse > 0);
    long zielAbsolut = encoderZ + deltaImpulse;

    unsigned long startZeit = millis();
    unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
    unsigned long totalZeit = (unsigned long)(abs(deltaImpulse) / IMPULSE_Z_PRO_MM * 12.0) + RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS;
    unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

    while ((vorwaerts && encoderZ < zielAbsolut && digitalRead(END_Z_U) == HIGH) ||
           (!vorwaerts && encoderZ > zielAbsolut && digitalRead(END_Z_O) == HIGH)) {
        unsigned long jetzt = millis();
        int pwm = PWM_MIN;

        if (jetzt < rampUpEnd)
            pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
        else if (jetzt > rampDownStart)
            pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
        else
            pwm = PWM_MAX;

        if (vorwaerts) {
            // nach unten
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, pwm);
        } else {
            // nach oben
            motorAnalogWrite(RPWM_Z, pwm);
            motorAnalogWrite(LPWM_Z, 0);
        }
        delay(10);
    }

    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);
    // Hinweis, falls Endschalter erreicht wurde
    if (vorwaerts && digitalRead(END_Z_U) == LOW) {
        Serial.println("Z: Unterer Endschalter erreicht – vorzeitig gestoppt");
    } else if (!vorwaerts && digitalRead(END_Z_O) == LOW) {
        Serial.println("Z: Oberer Endschalter erreicht – vorzeitig gestoppt");
    }
    Serial.print("Z gesetzt auf: ");
    Serial.print(zielPos_mm);
    Serial.println(" mm");
    Serial.print("Impulsstand Ist: ");
    Serial.print(encoderZ);
    Serial.print(" / Impulsstand Soll: ");
    Serial.println(zielAbsolut);
}

unsigned long lastBrushCheck = 0;
long lastBrushTicks = 0;
float getBrushRPM() {
    unsigned long now = millis();
    unsigned long dt = now - lastBrushCheck;
    if (dt < 200)
        return -1;
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
    const long zielImpulseZ = encoderZ + (zielPos_mm * IMPULSE_Z_PRO_MM); // relativ zur aktuellen Position

    bool erfolgreich = false;

    while (versuch < 3 && !erfolgreich) {
        versuch++;
        encoderBrush = 0;
        lastBrushCheck = millis();
        lastBrushTicks = 0;

        // Bürste starten (Ramp-up)
        for (int pwm = 0; pwm <= 255; pwm += 5) {
            motorAnalogWrite(PWM_BRUSH, pwm);
            delay(10);
        }

        // Schlitten absenken
        unsigned long startZeit = millis();
        unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
        unsigned long totalZeit = RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS + 3000;
        unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

        bool drehzahlAbfall = false;

        while (encoderZ < zielImpulseZ && digitalRead(END_Z_U) == HIGH) {
            unsigned long jetzt = millis();
            int pwm = PWM_MIN;

            if (jetzt < rampUpEnd) {
                pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
            } else if (jetzt > rampDownStart) {
                pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
            } else {
                pwm = PWM_MAX;
            }

            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, pwm);

            float rpm = getBrushRPM();
            if (rpm > 0 && rpm < MIN_RPM) {
                Serial.print("Drehzahl zu niedrig (");
                Serial.print(rpm);
                Serial.println("), fahre 10 mm nach oben");

                drehzahlAbfall = true;

                // 10 mm nach oben (relativ zur aktuellen Position)
                long rueckZiel = encoderZ - rueckImpulse10mm;
                while (encoderZ > rueckZiel && digitalRead(END_Z_O) == HIGH) {
                    motorAnalogWrite(RPWM_Z, PWM_MIN + 40);
                    motorAnalogWrite(LPWM_Z, 0);
                    delay(10);
                }

                motorAnalogWrite(RPWM_Z, 0);
                motorAnalogWrite(LPWM_Z, 0);
                break; // neuer Versuch
            }

            delay(10);
        }

        if (encoderZ >= zielImpulseZ && !drehzahlAbfall) {
            erfolgreich = true;
        }
    }

    // Bürste stoppen
    motorAnalogWrite(PWM_BRUSH, 0);
    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);

    // Fahre immer auf Position 10 mm von oben (Impulsziel = encoderZ - delta)
    Serial.println("Fahre auf Position 10 mm (von oben)");
    setzeZPosition(10);

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

        if (jetzt < rampUpEnd)
            pwm = PWM_MIN + ((jetzt - startZeit) * (PWM_MAX - PWM_MIN)) / RAMP_UP_TIME_MS;
        else if (jetzt > rampDownStart)
            pwm = PWM_MAX - ((jetzt - rampDownStart) * (PWM_MAX - PWM_MIN)) / RAMP_DOWN_TIME_MS;
        else
            pwm = PWM_MAX;

        long delta = encoderLinks - encoderRechts;
        const float kSync = 0.2;
        int pwmL = pwm - (delta * kSync);
        int pwmR = pwm + (delta * kSync);

        pwmL = constrain(pwmL, PWM_MIN, PWM_MAX);
        pwmR = constrain(pwmR, PWM_MIN, PWM_MAX);

        motorAnalogWrite(RPWM_L, vorLinks ? pwmL : 0);
        motorAnalogWrite(LPWM_L, vorLinks ? 0 : pwmL);
        motorAnalogWrite(RPWM_R, vorRechts ? pwmR : 0);
        motorAnalogWrite(LPWM_R, vorRechts ? 0 : pwmR);
        delay(10);
    }

    motorAnalogWrite(RPWM_L, 0);
    motorAnalogWrite(LPWM_L, 0);
    motorAnalogWrite(RPWM_R, 0);
    motorAnalogWrite(LPWM_R, 0);
    Serial.println("Fahrt beendet");
}

void anfrageUndAbarbeiten() {

    // Setze Kamera oben in die Mitte
    setzeXPosition(25);
    setzeZPosition(10);

    aktuelleY_mm = 0;
    Serial2.println("GETXY");
    // Debug: mirror to USB serial for IDE
    Serial.println("GETXY");

    zielCount = 0;
    unsigned long start = millis();
    String cmdBuffer = "";

    while (millis() - start < 5000) {
        sendeStatus();
        if (readSerialLine(cmdBuffer)) {
            // Prüfe auf Ende der Übertragung
            if (cmdBuffer == "DONE") {
                break;
            }
            // Verarbeite Koordinaten
            if (cmdBuffer.startsWith("XY:")) {
                int kommateil = cmdBuffer.indexOf(',');
                if (kommateil > 3 && zielCount < MAX_KOORDINATEN) {
                    float x = cmdBuffer.substring(3, kommateil).toFloat();
                    float y = cmdBuffer.substring(kommateil + 1).toFloat();
                    ziele[zielCount++] = {x, y};
                }
            }
            // Buffer zurücksetzen
            cmdBuffer = "";
        }
    }

    Serial.print("Empfangen: ");
    Serial.print(zielCount);
    Serial.println(" Koordinaten.");

    for (int i = 0; i < zielCount; i++) {
        float zielX = ziele[i].x_mm;
        float zielY = ziele[i].y_mm;
        float deltaY = zielY - aktuelleY_mm;

        setzeXPosition(zielX); // absolut
        sendeStatus();
        if (deltaY > 0.5) {
            fahreStrecke(deltaY, true, true);
            aktuelleY_mm += deltaY;
            sendeStatus();
        }
        senkeBuersteZuPosition(40);
        sendeStatus();
    }
}

// Liest eine Zeile von Serial1 und gibt true zurück, wenn eine vollständige Zeile gelesen wurde
bool readSerialLine(String &buffer) {
    while (Serial2.available()) {
        char c = Serial2.read();

        // Zeile vollständig wenn \n empfangen
        if (c == '\n') {
            return true;
        }
        // Ignoriere CR
        else if (c == '\r') {
            continue;
        }
        // Füge Zeichen zum Buffer hinzu wenn noch Platz
        else if (buffer.length() < MAX_CMD_LENGTH) {
            buffer += c;
        }

        // Buffer-Überlauf: Verwerfe alles bis zum nächsten Zeilenende
        if (buffer.length() >= MAX_CMD_LENGTH) {
            buffer = "";
            while (Serial2.available() && Serial2.read() != '\n')
                ;
            return false;
        }
    }
    return false;
}

void processSerialCommand() {
    static String cmdBuffer = "";
    bool lineComplete = false;

    lineComplete = readSerialLine(cmdBuffer);

    // Verarbeite nur vollständige Zeilen
    if (lineComplete) {
        // Debug-Ausgabe
        Serial.print("Empfangener Befehl: ");
        Serial.println(cmdBuffer);

        // Befehl auswerten basierend auf Keywords (indexOf >= 0 bedeutet "gefunden")
        if (cmdBuffer.indexOf("START") >= 0) {
            if (currentMode == WAITING_FOR_START) {
                currentMode = MANUAL;
                Serial.println("START empfangen - Wechsel zu MANUAL Modus");
            }
        }

        if (cmdBuffer.indexOf("MODE:AUTO") >= 0) {
            currentMode = AUTO;
            Serial.println("Modus gewechselt zu AUTO");
        } else if (cmdBuffer.indexOf("MODE:MANUAL") >= 0) {
            currentMode = MANUAL;
            Serial.println("Modus gewechselt zu MANUAL");
        }

        // Format: JOYSTICK:X=-48,Y=-54
        if (cmdBuffer.indexOf("JOYSTICK:") >= 0 && currentMode == MANUAL) {
            int xStart = cmdBuffer.indexOf("X=");
            int xEnd = cmdBuffer.indexOf(",Y=");
            int yEnd = cmdBuffer.length();

            if (xStart >= 0 && xEnd >= 0 && xEnd > xStart) {
                int x = cmdBuffer.substring(xStart + 2, xEnd).toInt();
                int y = cmdBuffer.substring(xEnd + 3).toInt();

                // Prüfe Wertebereich
                if (x >= -100 && x <= 100 && y >= -100 && y <= 100) {
                    processJoystickCommand(x, y);
                }
            }
        }

        // Buffer und Status zurücksetzen
        cmdBuffer = "";
        lineComplete = false;
    }
}

void processJoystickCommand(int x, int y) {
    // Debug-Ausgabe
    Serial.print("Joystick: X=");
    Serial.print(x);
    Serial.print(" Y=");
    Serial.println(y);

    // Normalisiere x und y auf -1.0 bis 1.0
    float xNorm = x / 100.0;
    float yNorm = y / 100.0;

    // Berechne Basis-Geschwindigkeiten für beide Räder
    float leftSpeed = -yNorm;  // Negativ weil -Y = vorwärts
    float rightSpeed = -yNorm; // Negativ weil -Y = vorwärts

    // Füge Drehkomponente hinzu
    // Bei positivem X (rechts) muss links schneller als rechts
    // Bei negativem X (links) muss rechts schneller als links
    if (xNorm > 0) {
        // Rechtsdrehung
        rightSpeed *= (1.0 - xNorm); // Rechtes Rad wird langsamer
        leftSpeed *= 1.0;            // Linkes Rad behält Geschwindigkeit
    } else {
        // Linksdrehung
        leftSpeed *= (1.0 + xNorm); // Linkes Rad wird langsamer (xNorm ist negativ)
        rightSpeed *= 1.0;          // Rechtes Rad behält Geschwindigkeit
    }

    // Reine Drehung (wenn y = 0)
    if (abs(yNorm) < 0.1 && abs(xNorm) > 0.1) {
        leftSpeed = xNorm;   // Positives X = links vorwärts
        rightSpeed = -xNorm; // Positives X = rechts rückwärts
    }

    // Wandle in PWM-Werte um (-200 bis 200)
    int pwmLeft = constrain((int)(leftSpeed * PWM_MAX), -PWM_MAX, PWM_MAX);
    int pwmRight = constrain((int)(rightSpeed * PWM_MAX), -PWM_MAX, PWM_MAX);

    // Setze Motoren
    // Links
    if (pwmLeft > 0) {
        motorAnalogWrite(RPWM_L, pwmLeft);
        motorAnalogWrite(LPWM_L, 0);
    } else {
        motorAnalogWrite(RPWM_L, 0);
        motorAnalogWrite(LPWM_L, -pwmLeft);
    }

    // Rechts
    if (pwmRight > 0) {
        motorAnalogWrite(RPWM_R, pwmRight);
        motorAnalogWrite(LPWM_R, 0);
    } else {
        motorAnalogWrite(RPWM_R, 0);
        motorAnalogWrite(LPWM_R, -pwmRight);
    }

    // Debug-Ausgabe der Motorwerte
    Serial.print("Motor PWM - Links: ");
    Serial.print(pwmLeft);
    Serial.print(" Rechts: ");
    Serial.println(pwmRight);
}

// Status-JSON alle 5 Sekunden senden, egal wo im Code
void sendeStatus() {
    static unsigned long lastStatusSend = 0;
    if (millis() - lastStatusSend > 5000) {
        sendeStatusJson();
        lastStatusSend = millis();
    }
}

// --- Status-JSON alle 5 Sekunden senden ---
void sendeStatusJson() {
    // Modus als String
    const char *modeStr = "WAITING";
    if (currentMode == MANUAL)
        modeStr = "MANUAL";
    else if (currentMode == AUTO)
        modeStr = "AUTO";

    StaticJsonDocument<256> doc;
    doc["mode"] = modeStr;
    doc["encL"] = encoderLinks;
    doc["encR"] = encoderRechts;
    doc["encX"] = encoderX;
    doc["encZ"] = encoderZ;

    char buffer[128];
    size_t n = serializeJson(doc, buffer);
    buffer[n] = '\0';
    Serial2.println(buffer);
    // Debug: also print forwarded buffer to USB serial
    Serial.println(buffer);
}

void loop() {
    processSerialCommand(); // Prüfe auf neue Kommandos

    sendeStatus();

    if (currentMode == WAITING_FOR_START) {
        // Im Wartezustand blinken wir eine LED oder geben periodisch eine Nachricht aus
        static unsigned long lastBlink = 0;
        if (millis() - lastBlink > 1000) { // Jede Sekunde
            Serial.println("Warte auf START Signal...");
            lastBlink = millis();
        }
    } else if (currentMode == AUTO) {
        // Im Auto-Modus kontinuierlich anfragen und abarbeiten
        anfrageUndAbarbeiten();
    }

    // Kleine Pause um CPU-Last zu reduzieren
    delay(10);
}
