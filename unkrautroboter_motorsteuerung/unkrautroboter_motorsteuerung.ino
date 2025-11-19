#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <Wire.h>

// OLED configuration (AZ-Delivery 1.3" 128x64 I2C)
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
// U8g2 SH1106 driver (we only support SH1106 now)
U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2_sh(U8G2_R0, /* reset=*/U8X8_PIN_NONE);

// Simple 21x8 text buffer (6x8 font => 21 cols x 8 rows)
static char oled_lines[8][22]; // 21 chars + NUL
static uint8_t oled_line_count = 0;
static char oled_cur[22];
static uint8_t oled_cur_len = 0;
// Flag whether OLED init succeeded
static bool oled_present = false;

// === KONSTANTEN ===
#define PWM_MIN 60
#define MAX_KOORDINATEN 50
#define PWM_MAX 255
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

Mode currentMode = MANUAL; // Startet im Wartezustand

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
#define PWM_BRUSH 46

// Enable-Pin für alle Brücken
#define EN_BRIDGE 47

// Brush encoder (A/B)
#define ENCODER_BRUSH_A 26 // Polling (kein Interrupt)

// Endschalter: true = NC (Normally Closed) wiring (pressed == HIGH),
// false = NO (Normally Open) wiring (pressed == LOW)
#define END_SWITCH_NC true

// Pins für Serial 0 sind fest: 0 (RX), 1 (TX)

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
unsigned long lastBrushCheck = 0;
long lastBrushTicks = 0;

// --- 18 kHz PWM-Initialisierung für alle 16-Bit-Timer (1,3,4,5) inkl. Bürste ---
const uint16_t MOTOR_PWM_TOP = 110; // ~18 kHz bei 16 MHz, N=1

// Helper: returns true when the end switch is currently pressed (according to wiring)
static inline bool endPressed(uint8_t pin) {
    if (END_SWITCH_NC)
        return (digitalRead(pin) == HIGH);
    else
        return (digitalRead(pin) == LOW);
}

// Push a full line into the circular buffer (scrolling)
void oledPushLine(const char *s) {
    if (!oled_present) {
        // Fallback: print to Serial so debug output remains visible
        if (s)
            Serial.println(s);
        return;
    }
    // truncate to 21 chars
    char tmp[22];
    strncpy(tmp, s, 21);
    tmp[21] = '\0';

    if (oled_line_count < 8) {
        strncpy(oled_lines[oled_line_count++], tmp, 22);
    } else {
        // shift up
        for (int i = 0; i < 7; i++)
            strncpy(oled_lines[i], oled_lines[i + 1], 22);
        strncpy(oled_lines[7], tmp, 22);
    }

    // redraw using u8g2_sh only
    const uint8_t row_h = 8;
    u8g2_sh.clearBuffer();
    u8g2_sh.setFont(u8g2_font_ncenB08_tr);
    for (uint8_t i = 0; i < oled_line_count; i++) {
        uint8_t y = (i + 1) * row_h; // baseline offset for u8g2
        if (y >= SCREEN_HEIGHT)
            break;
        u8g2_sh.drawStr(0, y, oled_lines[i]);
    }
    u8g2_sh.sendBuffer();
}

// Append text to current line (no newline)
void debug(const char *s) {
    if (!s)
        return;
    if (!oled_present) {
        // OLED not present, forward directly to Serial
        Serial.print(s);
        return;
    }
    while (*s) {
        if (oled_cur_len >= 21)
            break;
        oled_cur[oled_cur_len++] = *s++;
    }
    oled_cur[oled_cur_len] = '\0';
}
void debugln(const char *s) {
    if (!oled_present) {
        if (s)
            Serial.println(s);
        else
            Serial.println();
        return;
    }
    if (s && *s)
        debug(s);
    // push current line
    if (oled_cur_len == 0) {
        // empty, push empty or given string
        if (s && *s)
            oledPushLine(s);
        else
            oledPushLine("");
    } else {
        oledPushLine(oled_cur);
    }
    // reset current buffer
    oled_cur_len = 0;
    oled_cur[0] = '\0';
}

// Move cursor one line up so the next debug/debugln overwrites the previous line.
// Implementation: copy last displayed line into the current-line buffer and remove
// it from the displayed lines, then redraw. The next debug()/debugln() will
// append/replace that line.
void debugCursorUp() {
    if (!oled_present) {
        // no OLED -> nothing to do
        return;
    }

    if (oled_line_count == 0) {
        // nothing to move up to -> clear current buffer only
        oled_cur_len = 0;
        oled_cur[0] = '\0';
        return;
    }

    // copy last displayed line into current buffer (no visual change)
    strncpy(oled_cur, oled_lines[oled_line_count - 1], 21);
    oled_cur[21] = '\0';
    oled_cur_len = strlen(oled_cur);

    // remove last line from buffer so next debug/debugln will overwrite that row
    oled_line_count--;
}

// Motoren
void motorAnalogWrite(uint8_t pin, uint8_t pwm) {
    // PWM schreiben (0..255 auf 0..TOP abbilden)
    uint16_t val = (uint32_t)pwm * MOTOR_PWM_TOP / 255u;
    switch (pin) {
    case RPWM_L:
        OCR3A = val;
        break; // Timer3, OC3A, Rad links
    case LPWM_L:
        OCR4A = val;
        break; // Timer4, OC4A, Rad links
    case LPWM_R:
        OCR4B = val;
        break; // Timer4, OC4B, Rad rechts
    case RPWM_R:
        OCR4C = val;
        break; // Timer4, OC4C
    case RPWM_Z:
        OCR1A = val;
        break; // Timer1, OC1A, Z-Achse
    case LPWM_Z:
        OCR1B = val;
        break; // Timer1, OC1B, Z-Achse
    case RPWM_X:
        OCR5C = val;
        break; // Timer5, OC5C, X-Achse
    case LPWM_X:
        OCR5B = val;
        break; // Timer5, OC5B, X-Achse
    case PWM_BRUSH:
        OCR5A = val;
        break; // Timer5, OC5A, Bürste
    default:
        return; // Unbekannter Pin
    }
}
void initMotorPWM18kHz() {
    // Timer1: Pins 11 (OC1A), 12 (OC1B)
    TCCR1A = 0;
    TCCR1B = 0;
    TCNT1 = 0;
    ICR1 = MOTOR_PWM_TOP;
    TCCR1A = (1 << WGM11) | (1 << COM1A1) | (1 << COM1A0) // OC1A invertiert
             | (1 << COM1B1) | (1 << COM1B0);             // OC1B invertiert
    TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS11);   // Prescaler 8
    OCR1A = 0;
    OCR1B = 0;

    // Timer3: Pin 5 (OC3A)
    TCCR3A = 0;
    TCCR3B = 0;
    TCNT3 = 0;
    ICR3 = MOTOR_PWM_TOP;
    TCCR3A = (1 << WGM31) | (1 << COM3A1) | (1 << COM3A0); // OC3A invertiert (Pin 5)
    TCCR3B = (1 << WGM33) | (1 << WGM32) | (1 << CS31);    // Prescaler 8
    OCR3A = 0;

    // Timer4: Pins 6 (OC4A), 7 (OC4B), 8 (OC4C)
    TCCR4A = 0;
    TCCR4B = 0;
    TCNT4 = 0;
    ICR4 = MOTOR_PWM_TOP;
    TCCR4A = (1 << WGM41) | (1 << COM4A1) | (1 << COM4A0) // OC4A invertiert (Pin 6)
             | (1 << COM4B1) | (1 << COM4B0)              // OC4B invertiert (Pin 7)
             | (1 << COM4C1) | (1 << COM4C0);             // OC4C invertiert (Pin 8)
    TCCR4B = (1 << WGM43) | (1 << WGM42) | (1 << CS41);   // Prescaler 8
    OCR4A = 0;
    OCR4B = 0;
    OCR4C = 0;

    // Timer5: Pins 44 (OC5C), 45 (OC5B), 46 (OC5A)
    TCCR5A = 0;
    TCCR5B = 0;
    TCNT5 = 0;
    ICR5 = MOTOR_PWM_TOP;
    TCCR5A = (1 << WGM51) | (1 << COM5A1) | (1 << COM5A0) // OC5A invertiert (Pin 46)
             | (1 << COM5B1) | (1 << COM5B0)              // OC5B invertiert (Pin 45)
             | (1 << COM5C1) | (1 << COM5C0);             // OC5C invertiert (Pin 44)
    TCCR5B = (1 << WGM53) | (1 << WGM52) | (1 << CS51);   // Prescaler 8
    OCR5A = 0;
    OCR5B = 0;
    OCR5C = 0;

    pinMode(RPWM_L, OUTPUT);
    pinMode(LPWM_L, OUTPUT);
    pinMode(RPWM_R, OUTPUT);
    pinMode(LPWM_R, OUTPUT);
    pinMode(RPWM_X, OUTPUT);
    pinMode(LPWM_X, OUTPUT);
    pinMode(RPWM_Z, OUTPUT);
    pinMode(LPWM_Z, OUTPUT);
    pinMode(PWM_BRUSH, OUTPUT);

    // Ensure motors start in stopped state using the same inverted mapping
    motorAnalogWrite(RPWM_L, 0);
    motorAnalogWrite(LPWM_L, 0);
    motorAnalogWrite(RPWM_R, 0);
    motorAnalogWrite(LPWM_R, 0);
    motorAnalogWrite(RPWM_X, 0);
    motorAnalogWrite(LPWM_X, 0);
    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);
    motorAnalogWrite(PWM_BRUSH, 0);

    attachInterrupt(digitalPinToInterrupt(ENC_L_A), isrEncoderLinks, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_R_A), isrEncoderRechts, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_X_A), isrEncoderX, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_Z_A), isrEncoderZ, CHANGE);

    // Enable all bridges (if needed, otherwise remove this line)
    pinMode(EN_BRIDGE, OUTPUT);
    digitalWrite(EN_BRIDGE, HIGH);
}

// --- Polling-Funktion für ENCODER_BRUSH_A (Pin 31) ---
void pollBrushEncoder() {
    static int lastBrushState = HIGH;
    int brushState = digitalRead(ENCODER_BRUSH_A);
    if (brushState == LOW && lastBrushState == HIGH) {
        encoderBrush++;
    }
    lastBrushState = brushState;
}
void isrEncoderLinks() {
    if (digitalRead(ENC_L_A) == digitalRead(ENC_L_B))
        encoderLinks++;
    else
        encoderLinks--;
}
void isrEncoderRechts() {
    if (digitalRead(ENC_R_A) == digitalRead(ENC_R_B))
        encoderRechts--;
    else
        encoderRechts++;
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
void isrEncoderBrush() {
    encoderBrush++;
}

void kalibriereX() {
    debug("Kalibriere X...");
    // run until the left end switch is pressed
    while (!endPressed(END_X_L)) {
        motorAnalogWrite(RPWM_X, PWM_MIN + 20);
        motorAnalogWrite(LPWM_X, 0);
        delay(10);
    }
    motorAnalogWrite(RPWM_X, 0);
    motorAnalogWrite(LPWM_X, 0);
    delay(10);
    encoderX = 0;
    debugln("OK.");
}
void kalibriereZ() {
    debug("Kalibriere Z...");
    // run until the top end switch is pressed
    while (!endPressed(END_Z_O)) {
        motorAnalogWrite(RPWM_Z, PWM_MIN + 20);
        motorAnalogWrite(LPWM_Z, 0);
        delay(10);
    }
    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);
    delay(10);
    encoderZ = 0;
    debugln("OK.");
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

    while ((vorwaerts && encoderX < zielAbsolut && !endPressed(END_X_R)) ||
           (!vorwaerts && encoderX > zielAbsolut && !endPressed(END_X_L))) {
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
    if (vorwaerts && endPressed(END_X_R)) {
        debugln("X: Ende rechts");
    } else if (!vorwaerts && endPressed(END_X_L)) {
        debugln("X: Ende links");
    }
    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "X gesetzt auf: %.1f mm", zielPos_mm);
        debugln(tmp);
        snprintf(tmp, sizeof(tmp), "Impuls Ist: %ld / Soll: %ld", encoderX, zielAbsolut);
        debugln(tmp);
    }
}
void setzeZPosition(float zielPos_mm) {
    // Z-Achse absolut auf Zielposition in mm verfahren (analog zu setzeXPosition)
    // vorwaerts=true bedeutet Absenken (nach unten), false Anheben (nach oben)
    // Beim Anheben wird der obere Endschalter END_Z_O respektiert
    long zielImpulse = zielPos_mm * IMPULSE_Z_PRO_MM;
    long deltaImpulse = zielImpulse - encoderZ;
    bool vorwaerts = (deltaImpulse > 0);
    long zielAbsolut = encoderZ + deltaImpulse;

    unsigned long startZeit = millis();
    unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
    unsigned long totalZeit = (unsigned long)(abs(deltaImpulse) / IMPULSE_Z_PRO_MM * 12.0) + RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS;
    unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

    while ((vorwaerts && encoderZ < zielAbsolut && !endPressed(END_Z_U)) ||
           (!vorwaerts && encoderZ > zielAbsolut && !endPressed(END_Z_O))) {
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
    if (vorwaerts && endPressed(END_Z_U)) {
        debugln("Z: Ende unten");
    } else if (!vorwaerts && endPressed(END_Z_O)) {
        debugln("Z: Ende oben");
    }
    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "Z gesetzt auf: %.1f mm", zielPos_mm);
        debugln(tmp);
        snprintf(tmp, sizeof(tmp), "Impuls Ist: %ld / Soll: %ld", encoderZ, zielAbsolut);
        debugln(tmp);
    }
}

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
            pollBrushEncoder();
            delay(10);
        }

        // Schlitten absenken
        unsigned long startZeit = millis();
        unsigned long rampUpEnd = startZeit + RAMP_UP_TIME_MS;
        unsigned long totalZeit = RAMP_UP_TIME_MS + RAMP_DOWN_TIME_MS + 3000;
        unsigned long rampDownStart = totalZeit - RAMP_DOWN_TIME_MS;

        bool drehzahlAbfall = false;

        while (encoderZ < zielImpulseZ && !endPressed(END_Z_U)) {
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

            pollBrushEncoder();

            float rpm = getBrushRPM();
            if (rpm > 0 && rpm < MIN_RPM) {
                {
                    char tmp[64];
                    snprintf(tmp, sizeof(tmp), "RPM < %.0f.", MIN_RPM);
                    debugln(tmp);
                }

                drehzahlAbfall = true;

                // 10 mm nach oben (relativ zur aktuellen Position)
                long rueckZiel = encoderZ - rueckImpulse10mm;
                while (encoderZ > rueckZiel && !endPressed(END_Z_O)) {
                    motorAnalogWrite(RPWM_Z, PWM_MIN + 40);
                    motorAnalogWrite(LPWM_Z, 0);
                    pollBrushEncoder();
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
    debug("Fahre hoch...");
    setzeZPosition(3); // 10 mm + Sicherheitsabstand
    debugln("OK.");
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
    debugln("Ziel erreicht.");
}

void anfrageUndAbarbeiten() {

    // Setze Kamera oben in die Mitte
    setzeXPosition(25);
    setzeZPosition(10);

    aktuelleY_mm = 0;
    Serial.println("GETXY");
    // Debug: mirror to OLED display
    debugln("Sende GETXY");

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

    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "Empfangen: %d Koordinaten.", zielCount);
        debugln(tmp);
    }

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
    while (Serial.available()) {
        char c = Serial.read();

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
            while (Serial.available() && Serial.read() != '\n')
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

        // Befehl auswerten basierend auf Keywords (indexOf >= 0 bedeutet "gefunden")
        if (cmdBuffer.indexOf("START") >= 0) {
            if (currentMode == WAITING_FOR_START) {
                currentMode = MANUAL;
                debugln("RCD: START -> MANUAL");
            }
        }

        if (cmdBuffer.indexOf("MODE:AUTO") >= 0) {
            currentMode = AUTO;
            debugln("RCD: AUTO");
        } else if (cmdBuffer.indexOf("MODE:MANUAL") >= 0) {
            currentMode = MANUAL;
            debugln("RCD: MANUAL");
        }

        // Format: JOYSTICK:X=-48,Y=-54[,B=3]   (optionales Button-Feld B=)
        if (cmdBuffer.indexOf("JOYSTICK:") >= 0 && currentMode == MANUAL) {
            int xStart = cmdBuffer.indexOf("X=");
            int xEnd = cmdBuffer.indexOf(",Y=");
            int yEnd = cmdBuffer.indexOf(",B=");
            if (yEnd < 0)
                yEnd = cmdBuffer.length();

            if (xStart >= 0 && xEnd >= 0 && xEnd > xStart) {
                int x = cmdBuffer.substring(xStart + 2, xEnd).toInt();
                int y = cmdBuffer.substring(xEnd + 3, yEnd).toInt();

                int button = -1;
                if (yEnd < (int)cmdBuffer.length()) {
                    // parse button if present (only B= supported)
                    int bIdx = cmdBuffer.indexOf("B=", yEnd);
                    if (bIdx >= 0) {
                        button = cmdBuffer.substring(bIdx + 2).toInt();
                    }
                }

                // Prüfe Wertebereich
                if (x >= -100 && x <= 100 && y >= -100 && y <= 100) {
                    if (button == 3) {
                        // Button 3: steuere X- und Z-Motor manuell
                        processJoystickManualXZ(x, y);
                    } else {
                        // Standard: Fahrssteuerung
                        processJoystickCommand(x, y);
                    }
                }
            }
        }

        // Buffer und Status zurücksetzen
        cmdBuffer = "";
        lineComplete = false;
    }
}

// clamp-Hilfsfunktion
static int16_t clamp_int16(int16_t val, int16_t minVal, int16_t maxVal) {
    if (val < minVal)
        return minVal;
    if (val > maxVal)
        return maxVal;
    return val;
}

void joystickToPwm(int16_t x, int16_t y, int16_t *pwmL, int16_t *pwmR) {
    // Rohwerte im "Joystickraum"
    // v = -y  (vorwärts = positiv)
    // r = -x  (rechtsdrehung = positiv rechts, negativ links)
    // raw_L = v - r = x - y
    // raw_R = v + r = -(x + y)

    int16_t rawL = x - y;
    int16_t rawR = -(x + y);

    // Auf Bereich -100 .. 100 begrenzen (Clamping)
    rawL = clamp_int16(rawL, -100, 100);
    rawR = clamp_int16(rawR, -100, 100);

    // Auf PWM-Bereich -255 .. 255 skalieren
    // int32_t als Zwischentyp, um Überläufe zu vermeiden
    int32_t tmpL = (int32_t)rawL * 255 / 100;
    int32_t tmpR = (int32_t)rawR * 255 / 100;

    // Ergebnis zurückgeben
    *pwmL = (int16_t)tmpL;
    *pwmR = (int16_t)tmpR;
}

void processJoystickCommand(int x, int y) {

    int16_t pwmLeft = 0;
    int16_t pwmRight = 0;
    joystickToPwm(x, y, &pwmLeft, &pwmRight);

    // Blocking linear ramp-up / ramp-down over 400 ms from last applied PWM to target PWM
    static int lastPwmLeft = 0;
    static int lastPwmRight = 0;
    const unsigned long ramp_ms = 400;
    const unsigned int step_ms = 20; // 20 ms per step -> 20 steps
    const int steps = ramp_ms / step_ms;

    if (lastPwmLeft == pwmLeft && lastPwmRight == pwmRight) {
        // no change -> just write
        if (pwmLeft > 0) {
            motorAnalogWrite(RPWM_L, pwmLeft);
            motorAnalogWrite(LPWM_L, 0);
        } else {
            motorAnalogWrite(RPWM_L, 0);
            motorAnalogWrite(LPWM_L, -pwmLeft);
        }

        if (pwmRight > 0) {
            motorAnalogWrite(RPWM_R, pwmRight);
            motorAnalogWrite(LPWM_R, 0);
        } else {
            motorAnalogWrite(RPWM_R, 0);
            motorAnalogWrite(LPWM_R, -pwmRight);
        }
    } else {
        for (int s = 1; s <= steps; s++) {
            int interpL = lastPwmLeft + ((pwmLeft - lastPwmLeft) * s) / steps;
            int interpR = lastPwmRight + ((pwmRight - lastPwmRight) * s) / steps;

            if (interpL > 0) {
                motorAnalogWrite(RPWM_L, interpL);
                motorAnalogWrite(LPWM_L, 0);
            } else {
                motorAnalogWrite(RPWM_L, 0);
                motorAnalogWrite(LPWM_L, -interpL);
            }

            if (interpR > 0) {
                motorAnalogWrite(RPWM_R, interpR);
                motorAnalogWrite(LPWM_R, 0);
            } else {
                motorAnalogWrite(RPWM_R, 0);
                motorAnalogWrite(LPWM_R, -interpR);
            }

            delay(step_ms);
        }
        // ensure exact final values
        if (pwmLeft > 0) {
            motorAnalogWrite(RPWM_L, pwmLeft);
            motorAnalogWrite(LPWM_L, 0);
        } else {
            motorAnalogWrite(RPWM_L, 0);
            motorAnalogWrite(LPWM_L, -pwmLeft);
        }

        if (pwmRight > 0) {
            motorAnalogWrite(RPWM_R, pwmRight);
            motorAnalogWrite(LPWM_R, 0);
        } else {
            motorAnalogWrite(RPWM_R, 0);
            motorAnalogWrite(LPWM_R, -pwmRight);
        }

        lastPwmLeft = pwmLeft;
        lastPwmRight = pwmRight;
    }

    // Debug-Ausgabe der Motorwerte
    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "Motor L: %d R: %d", pwmLeft, pwmRight);
        debugln(tmp);
    }
}

// Manuelle Steuerung für X- und Z-Motoren mittels Joystick (Button 3 aktiviert)
// x -> X-Achse (links/rechts), y -> Z-Achse (oben/unten)
void processJoystickManualXZ(int x, int y) {
    // Deadzone und Skalierung
    const int DEAD = 8; // kleine Deadzone in Joystick-Einheiten
    int xAdj = (abs(x) < DEAD) ? 0 : x;
    int yAdj = (abs(y) < DEAD) ? 0 : y;

    // Normiere auf PWM_MAX
    int pwmX = constrain((int)((xAdj / 100.0) * PWM_MAX), -PWM_MAX, PWM_MAX);
    int pwmZ = constrain((int)((yAdj / 100.0) * PWM_MAX), -PWM_MAX, PWM_MAX);

    // X-Achse: ENC_X_A/B und END_X_L / END_X_R
    if (pwmX > 0) {
        // vorwärts (rechts)
        // respektiere rechten Endschalter
        if (!endPressed(END_X_R)) {
            motorAnalogWrite(RPWM_X, pwmX);
            motorAnalogWrite(LPWM_X, 0);
        } else {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, 0);
        }
    } else if (pwmX < 0) {
        // rückwärts (links)
        if (!endPressed(END_X_L)) {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, -pwmX);
        } else {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, 0);
        }
    } else {
        motorAnalogWrite(RPWM_X, 0);
        motorAnalogWrite(LPWM_X, 0);
    }

    // Z-Achse: ENC_Z_A/B und END_Z_O (oben) / END_Z_U (unten)
    if (pwmZ > 0) {
        // Absenken (nach unten) -> respektiere unteren Endschalter END_Z_U
        if (!endPressed(END_Z_U)) {
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, pwmZ);
        } else {
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, 0);
        }
    } else if (pwmZ < 0) {
        // Anheben (nach oben) -> respektiere oberen Endschalter END_Z_O
        if (!endPressed(END_Z_O)) {
            motorAnalogWrite(RPWM_Z, -pwmZ);
            motorAnalogWrite(LPWM_Z, 0);
        } else {
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, 0);
        }
    } else {
        motorAnalogWrite(RPWM_Z, 0);
        motorAnalogWrite(LPWM_Z, 0);
    }

    // Debug
    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "Motor X/Z: X=%d Z=%d", pwmX, pwmZ);
        debugln(tmp);
    }
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
    Serial.println(buffer);
}

// === SETUP und LOOP ===

void setup() {

    // Serial (USB) debug disabled for this module - use OLED display instead
    Serial.begin(115200); // Verbindung zum Raspberry Pi

    // Init OLED (Adafruit SSD1306). Probe I2C addresses to decide whether to use OLED.
    Wire.begin();

    bool found3C = (Wire.beginTransmission(0x3C), Wire.endTransmission() == 0);
    bool found3D = (Wire.beginTransmission(0x3D), Wire.endTransmission() == 0);
    if (found3C || found3D) {
        oled_present = true;
        Serial.println(found3C ? "OLED found @0x3C" : "OLED found @0x3D");
        // Set I2C clock lower for compatibility
        Wire.setClock(100000);
        // Initialize SH1106 display (U8g2)
        u8g2_sh.begin();
        u8g2_sh.clearBuffer();
        u8g2_sh.setFont(u8g2_font_ncenB08_tr);
        u8g2_sh.drawStr(0, 12, "OLED bereit (SH1106)");
        u8g2_sh.sendBuffer();
    } else {
        oled_present = false;
        Serial.println("OLED init failed (no 0x3C/0x3D)");
    }

    // oled already initialized above (if present)
    oled_line_count = 0;
    oled_cur_len = 0;
    debugln("Programm gestartet");

    initMotorPWM18kHz(); // Alle Motor-PWM Kanäle initialisieren

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

    kalibriereZ();
    kalibriereX();
}

void loop() {
    processSerialCommand(); // Prüfe auf neue Kommandos

    sendeStatus();

    // Sofortige Endschalter-Überprüfung im MANUAL-Modus, damit Endschalter
    // auch dann direkt reagieren, wenn Joystick-Nachrichten seltener eintreffen.
    // Reagiert mit der Loop-Frequenz (~10 ms).
    if (currentMode == MANUAL) {
        // X-Achse Endschalter (NC wiring: pressed == HIGH)
        if (endPressed(END_X_L) || endPressed(END_X_R)) {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, 0);
        }
        // Z-Achse Endschalter
        if (endPressed(END_Z_O) || endPressed(END_Z_U)) {
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, 0);
        }
    }

    if (currentMode == WAITING_FOR_START) {
        // Im Wartezustand blinken wir eine LED oder geben periodisch eine Nachricht aus
        static unsigned long lastBlink = 0;
        if (millis() - lastBlink > 1000) { // Jede Sekunde
            debugln("Warte auf START...");
            lastBlink = millis();
        }
    } else if (currentMode == AUTO) {
        // Im Auto-Modus kontinuierlich anfragen und abarbeiten
        anfrageUndAbarbeiten();
    }

    // Kleine Pause um CPU-Last zu reduzieren
    delay(10);
}
