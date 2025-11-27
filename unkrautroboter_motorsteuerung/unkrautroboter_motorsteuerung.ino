#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <Wire.h>

// INA260 I2C address (default)
#define INA260_ADDR 0x40

// Flag whether INA260 is present on the I2C bus
static bool ina260_present = false;

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
// Flag whether the last oled_cur is already present in oled_lines (so debug() overwrites)
static bool oled_has_current_line = false;
// Flag whether OLED init succeeded
static bool oled_present = false;

// === KONSTANTEN ===
#define PWM_MIN 60
#define MAX_KOORDINATEN 50
#define PWM_MAX 255
#define RAMP_UP_TIME_MS 1000
#define RAMP_DOWN_TIME_MS 1000

#define RAD_DURCHMESSER_MM 96.0
#define ENCODER_IMPULSE_UMD 4800
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
#define BRUSH_CPR 106
#define BRUSH_TARGET_RPM 2200

#define ENCODER_X_CPR 600
#define ENCODER_Z_CPR 600
#define SCHRAUBEN_STEIGUNG_MM 8.0
#define IMPULSE_X_PRO_MM (ENCODER_X_CPR / SCHRAUBEN_STEIGUNG_MM)
#define IMPULSE_Z_PRO_MM (ENCODER_Z_CPR / SCHRAUBEN_STEIGUNG_MM)

#define RAMP_X_IMPULSE 2000 // Impulse bis zur Rampe
#define RAMP_Z_IMPULSE 2000
#define RAMP_RAD_IMPULSE 10000
#define MIN_RAMP_IMP 0

#define MAX_X 440
#define MAX_Z 58
#define MITTEX 220

volatile long encoderLinks = 0;
volatile long encoderRechts = 0;
volatile long encoderX = 0;
volatile long encoderZ = 0;
volatile long deltaEncoderRad = 0;

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
const uint16_t MOTOR_PWM_TOP = 110; // ~18 kHz bei 16 MHz, N=1

// Helper: returns true when the end switch is currently pressed (according to wiring)
static inline bool endPressed(uint8_t pin) {
    if (END_SWITCH_NC)
        return (digitalRead(pin) == HIGH);
    else
        return (digitalRead(pin) == LOW);
}

// Build and send the persistent 8-line buffer to the display.
// This function uses the global `oled_cur` contents. If `oled_has_current_line`
// is true the current contents overwrite the last displayed line, otherwise
// the contents are appended as a new line (with scrolling when full).
void oledPush() {
    if (!oled_present)
        return;

    // Make a local copy of the current line (may be empty)
    char tmp[22];
    if (oled_cur_len > 21)
        oled_cur_len = 21;
    memcpy(tmp, oled_cur, oled_cur_len);
    tmp[oled_cur_len] = '\0';

    if (oled_has_current_line) {
        // overwrite last displayed line
        strncpy(oled_lines[oled_line_count - 1], tmp, 22);
        oled_lines[oled_line_count - 1][21] = '\0';
    } else {
        // append as new line (scroll if needed)
        if (oled_line_count < 8) {
            strncpy(oled_lines[oled_line_count++], tmp, 22);
            oled_lines[oled_line_count - 1][21] = '\0';
        } else {
            for (int i = 0; i < 7; i++)
                strncpy(oled_lines[i], oled_lines[i + 1], 22);
            strncpy(oled_lines[7], tmp, 22);
            oled_lines[7][21] = '\0';
        }
        oled_has_current_line = true;
    }

    // redraw using u8g2_sh only
    const uint8_t row_h = 7;
    u8g2_sh.clearBuffer();
    u8g2_sh.setFont(u8g2_font_5x7_tr);
    for (uint8_t i = 0; i < oled_line_count; i++) {
        uint8_t y = (i + 1) * row_h; // baseline offset for u8g2
        if (y >= SCREEN_HEIGHT)
            break;
        u8g2_sh.drawStr(0, y, oled_lines[i]);
    }
    u8g2_sh.sendBuffer();
    // small pause to give the display time to update physically
    delay(5);
}
// Append text to current line (no newline)
void debug(const char *s) {
    if (!oled_present) {
        // no OLED -> nothing to do
        return;
    }
    if (!s)
        return;
    while (*s) {
        if (oled_cur_len >= 21)
            break;
        oled_cur[oled_cur_len++] = *s++;
    }
    oled_cur[oled_cur_len] = '\0';
    // After every append, update the persistent buffer and redraw
    oledPush();
}
void debugln(const char *s) {
    if (!oled_present) {
        // no OLED -> nothing to do
        return;
    }
    if (s && *s)
        debug(s);
    // Ensure the current line has been pushed, then advance to a fresh line
    // (debug already called oledPush()). Clear current buffer so next debug
    // will create a new line.
    oled_cur_len = 0;
    oled_cur[0] = '\0';
    oled_has_current_line = false;
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

// Note: brush encoder is sampled when needed inside getBrushRPM().
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
        encoderZ--;
    else
        encoderZ++;
}
void isrEncoderBrush() {
    encoderBrush++;
}

void kalibriereX() {
    debug("Kalibriere X...");
    // run until the left end switch is pressed
    while (!endPressed(END_X_L)) {
        motorAnalogWrite(RPWM_X, 150);
        motorAnalogWrite(LPWM_X, 0);
        delay(5);
    }
    motorAnalogWrite(RPWM_X, 0);
    motorAnalogWrite(LPWM_X, 0);
    delay(500);
    encoderX = 0;
    debugln("OK.");
}
void kalibriereZ() {
    debug("Kalibriere Z...");
    // run until the top end switch is pressed
    while (!endPressed(END_Z_O)) {
        motorAnalogWrite(RPWM_Z, 150);
        motorAnalogWrite(LPWM_Z, 0);
        delay(5);
    }
    motorAnalogWrite(RPWM_Z, 0);
    motorAnalogWrite(LPWM_Z, 0);
    delay(500);
    encoderZ = 0;
    debugln("OK.");
}

void setzeXPosition(float zielPos_mm) {
    // Ziel in Impulsen berechnen (absolute Position)
    long zielImpulse = (long)(zielPos_mm * IMPULSE_X_PRO_MM);
    long deltaImpulse = zielImpulse - encoderX;
    bool vorwaerts = (deltaImpulse > 0);
    long zielAbsolut = encoderX + deltaImpulse;

    long totalDistance = deltaImpulse >= 0 ? deltaImpulse : -deltaImpulse;
    if (totalDistance < 20) {
        return; // nichts zu tun
    }

    // Rampen-Parameter (Impuls-Größen)
    const long MAX_RAMP_IMP = RAMP_X_IMPULSE; // ab hier gilt PWM_MAX
    int minPWMLast = PWM_MIN;                 // minimale PWM unter Last (abhängig von Motor/Mechanik)
    long startEncoder = encoderX;
    long lastEnc;

    while ((vorwaerts && encoderX < zielAbsolut && !endPressed(END_X_R)) ||
           (!vorwaerts && encoderX > zielAbsolut && !endPressed(END_X_L))) {
        // Distanz vom Startpunkt und zum Ziel in Impulsen
        long distFromStart = encoderX >= startEncoder ? (encoderX - startEncoder) : (startEncoder - encoderX);
        long distToGoal = zielAbsolut >= encoderX ? (zielAbsolut - encoderX) : (encoderX - zielAbsolut);

        // Wir betrachten die näher liegende Grenze (Start oder Ziel)
        long minDistToEndpoint = distFromStart < distToGoal ? distFromStart : distToGoal;

        // Linearisiere Beschleunigung (von Start) und Abbremsen (zum Ziel) separat
        // und verwende das limitierende (kleinere) PWM, damit Abbremsen Vorrang hat.
        int pwmAccel;
        if (distFromStart <= MIN_RAMP_IMP) {
            pwmAccel = PWM_MIN;
        } else if (distFromStart >= MAX_RAMP_IMP) {
            pwmAccel = PWM_MAX;
        } else {
            long numerA = (long)(distFromStart - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomA = (MAX_RAMP_IMP - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP - MIN_RAMP_IMP) : 1;
            pwmAccel = PWM_MIN + (int)(numerA / denomA);
        }

        int pwmDecel;
        if (distToGoal <= MIN_RAMP_IMP) {
            pwmDecel = PWM_MIN;
        } else if (distToGoal >= MAX_RAMP_IMP) {
            pwmDecel = PWM_MAX;
        } else {
            long numerD = (long)(distToGoal - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomD = (MAX_RAMP_IMP - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP - MIN_RAMP_IMP) : 1;
            pwmDecel = PWM_MIN + (int)(numerD / denomD);
        }

        // Nutze das kleinere PWM, damit Abbremsen/Endpunktbegrenzung dominiert, aber mindestens minPWMLast
        int pwm;
        if (lastEnc == encoderX && minPWMLast < 150) {
            minPWMLast += 5; // langsam steigern
            // Unter Last mindestens minPWMLast verwenden
            pwm = minPWMLast;
        }
        lastEnc = encoderX;
        pwm = max((pwmAccel < pwmDecel) ? pwmAccel : pwmDecel, minPWMLast);

        // Motoransteuerung abhängig von Richtung
        if (vorwaerts) {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, pwm);
        } else {
            motorAnalogWrite(RPWM_X, pwm);
            motorAnalogWrite(LPWM_X, 0);
        }
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
        snprintf(tmp, sizeof(tmp), "X: %ld/%ld", encoderX, zielAbsolut);
        debugln(tmp);
    }
}
void setzeZPosition(float zielPos_mm) {
    // Z-Achse absolut auf Zielposition in mm verfahren (analog zu setzeXPosition)
    // vorwaerts=true bedeutet Absenken (nach unten), false Anheben (nach oben)
    // Beim Anheben wird der obere Endschalter END_Z_O respektiert
    long zielImpulse = (long)(zielPos_mm * IMPULSE_Z_PRO_MM);
    long deltaImpulse = zielImpulse - encoderZ;
    bool vorwaerts = (deltaImpulse > 0);
    long zielAbsolut = encoderZ + deltaImpulse;

    long totalDistance = deltaImpulse >= 0 ? deltaImpulse : -deltaImpulse;
    if (totalDistance < 20) {
        return; // nichts zu tun
    }

    const long MAX_RAMP_IMP_Z = RAMP_Z_IMPULSE; // ab hier gilt PWM_MAX
    int minPWMLast = PWM_MIN;                   // minimale PWM unter Last (abhängig von Motor/Mechanik)
    long startEncoder = encoderZ;
    long lastEnc;

    while ((vorwaerts && encoderZ < zielAbsolut && !endPressed(END_Z_U)) ||
           (!vorwaerts && encoderZ > zielAbsolut && !endPressed(END_Z_O))) {
        // Distanz vom Startpunkt und zum Ziel in Impulsen
        long distFromStart = encoderZ >= startEncoder ? (encoderZ - startEncoder) : (startEncoder - encoderZ);
        long distToGoal = zielAbsolut >= encoderZ ? (zielAbsolut - encoderZ) : (encoderZ - zielAbsolut);

        // Linearisiere Beschleunigung (von Start) und Abbremsen (zum Ziel) separat
        // und verwende das limitierende (kleinere) PWM, damit Abbremsen Vorrang hat.
        int pwmAccel;
        if (distFromStart <= MIN_RAMP_IMP) {
            pwmAccel = PWM_MIN;
        } else if (distFromStart >= MAX_RAMP_IMP_Z) {
            pwmAccel = PWM_MAX;
        } else {
            long numerA = (long)(distFromStart - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomA = (MAX_RAMP_IMP_Z - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP_Z - MIN_RAMP_IMP) : 1;
            pwmAccel = PWM_MIN + (int)(numerA / denomA);
        }

        int pwmDecel;
        if (distToGoal <= MIN_RAMP_IMP) {
            pwmDecel = PWM_MIN;
        } else if (distToGoal >= MAX_RAMP_IMP_Z) {
            pwmDecel = PWM_MAX;
        } else {
            long numerD = (long)(distToGoal - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomD = (MAX_RAMP_IMP_Z - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP_Z - MIN_RAMP_IMP) : 1;
            pwmDecel = PWM_MIN + (int)(numerD / denomD);
        }

        // Nutze das kleinere PWM, damit Abbremsen/Endpunktbegrenzung dominiert, aber mindestens minPWMLast
        int pwm;
        if (lastEnc == encoderZ && minPWMLast < 150) {
            minPWMLast += 5; // langsam steigern
            // Unter Last mindestens minPWMLast verwenden
            pwm = minPWMLast;
        }
        lastEnc = encoderZ;
        pwm = max((pwmAccel < pwmDecel) ? pwmAccel : pwmDecel, minPWMLast);

        // Motoransteuerung abhängig von Richtung (Absenken = vorwaerts)
        if (vorwaerts) {
            motorAnalogWrite(RPWM_Z, 0);
            motorAnalogWrite(LPWM_Z, pwm);
        } else {
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
        snprintf(tmp, sizeof(tmp), "Z: %ld/%ld", encoderZ, zielAbsolut);
        debugln(tmp);
    }
}

float getBrushRPM() {
    // Sample the brush encoder for 1 second and compute RPM.
    unsigned long start = millis();
    int lastState = digitalRead(ENCODER_BRUSH_A);
    long pulses = 0;

    // Poll for 1000 ms. We use a small delay to avoid hammering CPU.
    while (millis() - start < 1000) {
        int s = digitalRead(ENCODER_BRUSH_A);
        if (s == LOW && lastState == HIGH) {
            pulses++;
        }
        lastState = s;
    }

    // pulses counted in 1 second -> revolutions per minute = (pulses / CPR) * 60
    float rpm = (pulses * 60.0) / BRUSH_CPR;
    return rpm;
}

void aktiviereBuerste() {

    bool erfolgreich = false;

    const float start_mm = 30.0f;
    const float zielPos_mm = 50.0f; // Ziel: 50 mm
    const float step_mm = 5.0f;     // Schrittweite in mm pro setzeZPosition-Aufruf

    encoderBrush = 0;

    setzeZPosition(start_mm);

    // Bürste starten (Ramp-up)
    for (int pwm = 0; pwm <= 100; pwm += 5) {
        motorAnalogWrite(PWM_BRUSH, pwm);
        delay(10);
    }

    float target_rpm = getBrushRPM();
    const float MIN_RPM = target_rpm * 0.9;

    // Aktuelle und Ziel-Position in mm
    float next_mm = start_mm;

    while (next_mm < zielPos_mm) {

        next_mm += step_mm;

        // Bewege absolut auf next_mm (setzeZPosition behandelt Endschalter)
        setzeZPosition(next_mm);

        // Prüfe Drehzahl (getBrushRPM pollt intern 1s)
        float rpm = getBrushRPM();
        if (rpm > 0 && rpm < MIN_RPM) {
            char tmp[64];
            snprintf(tmp, sizeof(tmp), "RPM: %d < %d.", (int)rpm, (int)MIN_RPM);
            debugln(tmp);
            // 10 mm zurück (relativ zur aktuellen Position)
            break; // Am Boden angekommen
        }
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "RPM: %d > %d.", (int)rpm, (int)MIN_RPM);
        debugln(tmp);
    }

    // Bürste stoppen
    next_mm -= step_mm; // korrigiere aktuellen Stand
    setzeZPosition(next_mm);

    for (int pwm = 100; pwm >= 0; pwm -= 5) {
        motorAnalogWrite(PWM_BRUSH, pwm);
        delay(10);
    }

    debug("Fahre hoch...");
    setzeZPosition(start_mm);
    debugln("OK.");
}

void fahreStrecke(int strecke_mm, bool vorLinks, bool vorRechts) {
    // Reset wheel encoders and compute target in impulses
    encoderLinks = 0;
    encoderRechts = 0;

    long startAvg = 0;
    long zielImpulse = (long)strecke_mm * IMPULSE_PRO_MM - deltaEncoderRad;

    const long MAX_RAMP_IMP = RAMP_RAD_IMPULSE; // reuse X ramp constant
    const float kSync = 2;                      // synchronization gain (unchanged)

    // Bei Rückwärtsfahrt (false,false) werden Encoder negativ -> Ziel ist negativ
    // Schleife prüft Absolutwerte
    bool rueckwaerts = (!vorLinks && !vorRechts);
    long absZielImpulse = (zielImpulse >= 0) ? zielImpulse : -zielImpulse;

    while (true) {
        long absEncoderL = (encoderLinks >= 0) ? encoderLinks : -encoderLinks;
        long absEncoderR = (encoderRechts >= 0) ? encoderRechts : -encoderRechts;

        // Abbruch wenn beide Räder Ziel erreicht haben (Absolutwert)
        if (absEncoderL >= absZielImpulse && absEncoderR >= absZielImpulse)
            break;

        long currAvg = (encoderLinks + encoderRechts) / 2;
        long absCurrAvg = (currAvg >= 0) ? currAvg : -currAvg;

        long distFromStart = absCurrAvg;
        long distToGoal = absZielImpulse - absCurrAvg;
        if (distToGoal < 0)
            distToGoal = 0;

        // Linear accel from start and decel to goal (same mapping as setzeXPosition)
        int pwmAccel;
        if (distFromStart <= MIN_RAMP_IMP) {
            pwmAccel = PWM_MIN;
        } else if (distFromStart >= MAX_RAMP_IMP) {
            pwmAccel = PWM_MAX;
        } else {
            long numerA = (long)(distFromStart - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomA = (MAX_RAMP_IMP - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP - MIN_RAMP_IMP) : 1;
            pwmAccel = PWM_MIN + (int)(numerA / denomA);
        }

        int pwmDecel;
        if (distToGoal <= MIN_RAMP_IMP) {
            pwmDecel = PWM_MIN;
        } else if (distToGoal >= MAX_RAMP_IMP) {
            pwmDecel = PWM_MAX;
        } else {
            long numerD = (long)(distToGoal - MIN_RAMP_IMP) * (PWM_MAX - PWM_MIN);
            long denomD = (MAX_RAMP_IMP - MIN_RAMP_IMP > 0) ? (MAX_RAMP_IMP - MIN_RAMP_IMP) : 1;
            pwmDecel = PWM_MIN + (int)(numerD / denomD);
        }

        int pwm = (pwmAccel < pwmDecel) ? pwmAccel : pwmDecel;

        // Per-loop wheel synchronization: only reduce the faster wheel.
        // For backward motion, more negative encoder value = farther traveled.
        // We use absolute values to determine which wheel is ahead.
        long absDelta = absEncoderL - absEncoderR;
        int pwmL = pwm;
        int pwmR = pwm;

        if (absDelta > 0) {
            // left wheel ahead (has traveled farther) -> reduce left only
            int adjust = (int)(absDelta * kSync);
            pwmL = pwm - adjust;
        } else if (absDelta < 0) {
            // right wheel ahead (has traveled farther) -> reduce right only
            int adjust = (int)((-absDelta) * kSync);
            pwmR = pwm - adjust;
        }

        // Constrain to allowed PWM range (don't boost the slower wheel)
        pwmL = constrain(pwmL, PWM_MIN, PWM_MAX);
        pwmR = constrain(pwmR, PWM_MIN, PWM_MAX);

        // vorLinks/vorRechts: true = vorwärts (RPWM), false = rückwärts (LPWM)
        motorAnalogWrite(RPWM_L, vorLinks ? pwmL : 0);
        motorAnalogWrite(LPWM_L, vorLinks ? 0 : pwmL);
        motorAnalogWrite(RPWM_R, vorRechts ? pwmR : 0);
        motorAnalogWrite(LPWM_R, vorRechts ? 0 : pwmR);
    }

    motorAnalogWrite(RPWM_L, 0);
    motorAnalogWrite(LPWM_L, 0);
    motorAnalogWrite(RPWM_R, 0);
    motorAnalogWrite(LPWM_R, 0);

    delay(500);

    deltaEncoderRad = (encoderLinks + encoderRechts) / 2 - zielImpulse; // zu weit gefahren
    {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "R: %ld/%ld/%ld", encoderLinks, encoderRechts, zielImpulse);
        debugln(tmp);
    }
}

void anfrageUndAbarbeiten() {

    // Setze Kamera oben in die Mitte
    setzeZPosition(10);
    setzeXPosition(MITTEX);

    aktuelleY_mm = 0;
    Serial.println("GETXY");
    // Debug: mirror to OLED display
    debugln("Sende GETXY");

    zielCount = 0;
    unsigned long start = millis();
    String cmdBuffer = "";

    while (millis() - start < 60000) {
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

    // Sortiere empfangene Koordinaten nach Y aufsteigend, damit wir sie
    // in aufsteigender Y-Reihenfolge abarbeiten.
    if (zielCount > 1) {
        for (int i = 0; i < zielCount - 1; i++) {
            int best = i;
            for (int j = i + 1; j < zielCount; j++) {
                if (ziele[j].y_mm < ziele[best].y_mm) {
                    best = j;
                }
            }
            if (best != i) {
                Zielpunkt tmp = ziele[i];
                ziele[i] = ziele[best];
                ziele[best] = tmp;
            }
        }
        // Optional: kurze Debug-Ausgabe der ersten/letzten nach Sort
        char dbg[128];
        snprintf(dbg, sizeof(dbg), "Y[0]=%.2f Y[%d]=%.2f", ziele[0].y_mm, zielCount - 1, ziele[zielCount - 1].y_mm);
        debugln(dbg);
    }

    processSerialCommand();

    deltaEncoderRad = 0;

    if (zielCount == 0) {
        fahreStrecke(302, true, true);
        sendeStatus();
        return;
    }

    for (int i = 0; i < zielCount; i++) {
        float zielX = ziele[i].x_mm;
        float zielY = ziele[i].y_mm;
        float deltaY = zielY - aktuelleY_mm;

        if (deltaY > 0.5) {
            fahreStrecke(deltaY, true, true);
            aktuelleY_mm += deltaY;
            sendeStatus();
        }

        setzeXPosition(zielX); // absolut
        sendeStatus();

        aktiviereBuerste();
        sendeStatus();
    }

    setzeZPosition(10);              // Bürste wieder ganz hoch
    fahreStrecke(250, false, false); // für nächstes Bild zurücksetzen
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

    // Skaliere auf -255..255 (Zwischenschritt)
    int32_t tmpL = (int32_t)rawL * 255 / 100;
    int32_t tmpR = (int32_t)rawR * 255 / 100;

    // Mapping: 0 -> 0; magnitudes 1..255 -> PWM_MIN..255 linear
    auto mapMagnitude = [](int32_t mag) -> int16_t {
        if (mag <= 0)
            return 0;
        if (mag >= 255)
            return 255;
        // map 1..254..255 to PWM_MIN..255
        // Use integer math: mapped = PWM_MIN + (mag-1)*(255-PWM_MIN)/254
        int32_t numer = (int32_t)(mag - 1) * (255 - PWM_MIN);
        int32_t denom = 254; // 255-1
        int32_t mapped = PWM_MIN + (numer / denom);
        if (mapped > 255)
            mapped = 255;
        return (int16_t)mapped;
    };

    int16_t outL, outR;

    int32_t magL = (tmpL >= 0) ? tmpL : -tmpL;
    int32_t magR = (tmpR >= 0) ? tmpR : -tmpR;

    int16_t mappedL = mapMagnitude(magL);
    int16_t mappedR = mapMagnitude(magR);

    outL = (tmpL >= 0) ? mappedL : -mappedL;
    outR = (tmpR >= 0) ? mappedR : -mappedR;

    *pwmL = outL;
    *pwmR = outR;
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
        // respektiere linken Endschalter
        if (!endPressed(END_X_L)) {
            motorAnalogWrite(RPWM_X, pwmX);
            motorAnalogWrite(LPWM_X, 0);
        } else {
            motorAnalogWrite(RPWM_X, 0);
            motorAnalogWrite(LPWM_X, 0);
        }
    } else if (pwmX < 0) {
        // rückwärts (rechts)
        if (!endPressed(END_X_R)) {
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

    char buffer[255];
    size_t n = serializeJson(doc, buffer);
    buffer[n] = '\0';
    // If INA260 present, read registers and append values to the JSON
    if (ina260_present) {
        // Read INA260 registers: 0x01 = Current, 0x02 = Bus Voltage, 0x03 = Power
        int16_t rawCurrent = 0;
        uint16_t rawVoltage = 0;
        uint16_t rawPower = 0;

        // Helper lambda to read a 16-bit register
        auto readReg16 = [&](uint8_t reg, uint16_t &out) -> bool {
            Wire.beginTransmission(INA260_ADDR);
            Wire.write(reg);
            if (Wire.endTransmission() != 0)
                return false;
            Wire.requestFrom((uint8_t)INA260_ADDR, (uint8_t)2);
            if (Wire.available() < 2)
                return false;
            uint8_t hi = Wire.read();
            uint8_t lo = Wire.read();
            out = (uint16_t)((hi << 8) | lo);
            return true;
        };

        uint16_t tmp;
        if (readReg16(0x01, (uint16_t &)tmp)) {
            rawCurrent = (int16_t)tmp; // signed
        }
        if (readReg16(0x02, rawVoltage)) {
            // rawVoltage as unsigned
        }

        // Convert raw values to human units using typical INA260 LSBs:
        // current: 1.25 mA/LSB, bus voltage: 1.25 mV/LSB, power: 10 mW/LSB
        int32_t current_mA = (int32_t)rawCurrent * 1250 / 1000;     // mA
        int32_t voltage_mV = (int32_t)rawVoltage * 1250 / 1000;     // mV (raw * 1.25)
        int32_t power_mW = (int32_t)voltage_mV * current_mA / 1000; // mW

        // Append to JSON
        doc["ina_present"] = true;
        doc["ina_current_mA"] = current_mA;
        doc["ina_voltage_mV"] = voltage_mV;
        doc["ina_power_mW"] = power_mW;

        // Re-serialize with INA fields
        n = serializeJson(doc, buffer);
        buffer[n] = '\0';
    }

    Serial.println("STATUS:" + String(buffer));
}

void processSerialCommand() {
    static String cmdBuffer = "";
    bool lineComplete = false;

    lineComplete = readSerialLine(cmdBuffer);

    // Verarbeite nur vollständige Zeilen
    if (lineComplete) {

        // Befehl auswerten basierend auf Keywords (indexOf >= 0 bedeutet "gefunden")
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

// === SETUP und LOOP ===

void setup() {

    // Serial (USB) debug disabled for this module - use OLED display instead
    Serial.begin(115200); // Verbindung zum Raspberry Pi

    // Init OLED (Adafruit SSD1306). Probe I2C addresses to decide whether to use OLED.
    Wire.begin();

    // Probe INA260 at default address
    if (Wire.beginTransmission(INA260_ADDR), Wire.endTransmission() == 0) {
        ina260_present = true;
        debugln("INA260 gefunden");
    } else {
        ina260_present = false;
    }

    bool found3C = (Wire.beginTransmission(0x3C), Wire.endTransmission() == 0);
    if (found3C) {
        oled_present = true;
        // Set I2C clock lower for compatibility
        Wire.setClock(400000);
        // Initialize SH1106 display (U8g2)
        u8g2_sh.begin();
        u8g2_sh.clearBuffer();
        u8g2_sh.setFont(u8g2_font_ncenB08_tr);
    } else {
        oled_present = false;
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
    setzeXPosition(MITTEX); // X-Achse in Mittelstellung
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
            debugln("Warte auf Pi4...");
            Serial.println("WAITING");
            lastBlink = millis();
        }
    } else if (currentMode == AUTO) {
        // Im Auto-Modus kontinuierlich anfragen und abarbeiten
        anfrageUndAbarbeiten();
    }

    // Kleine Pause um CPU-Last zu reduzieren
    delay(10);
}
