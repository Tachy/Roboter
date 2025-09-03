/*
  Energiewächter – Arduino Pro Mini (5V)
  --------------------------------------
  • Nur PV- und Batterie-Spannungen über hochohmige Spannungsteiler (keine Strommessung).
  • Entscheidung „Victron + PV trennen/verbinden“ erfolgt AUSSCHLIESSLICH über die PV-Spannung
    mit Hysterese + Zeitfilter.
  • Hauptsystem (XL4015) wird abhängig von LiFePO₄-SoC (per Ruhespannungs-Näherung) geschaltet.
  • 4x bistabile 12V-Relais (je 2 Spulen) über 2x ULN2003:
      R1: PV -> Victron (SET=verbinden, RESET=trennen)
      R2: BAT -> Victron (SET=verbinden, RESET=trennen)
      R3: Precharge -> XL4015 (SET=ein,  RESET=aus)
      R4: Hauptpfad  -> XL4015 (SET=ein,  RESET=aus)

  WICHTIG:
  - Schwellen **auf DEIN System kalibrieren!**
  - ULN2003: COM-Pin an +12 V (Freilaufdioden), gemeinsame Masse zum Arduino.
  - Bistabile Relais nur kurz pulsweise ansteuern (RELAY_PULSE_MS).
  - Pi-Shutdown-Signal beachten (PI_SHDN_PIN), optionales ACK (PI_ACK_PIN).

  Reihenfolgen:
  - Einschalten Victron: zuerst BAT verbinden (R2_SET), dann PV verbinden (R1_SET)
  - Ausschalten Victron: zuerst PV trennen (R1_RST), dann BAT trennen (R2_RST)
  - Hauptsystem EIN: Precharge (R3_SET) → warten → Hauptpfad (R4_SET) → Precharge AUS (R3_RST)
  - Hauptsystem AUS: Hauptpfad AUS (R4_RST) → Precharge AUS (R3_RST)

  HINWEISE ZUR KALIBRIERUNG:
  - PV_DISCONNECT_V / PV_RECONNECT_V: An realer Anlage „einmessen“ (Morgendämmerung/Abend/Wolken).
    Ziel: Keine Schaltspiele, aber zügiges Verbinden bei brauchbarer Sonne.
  - LiFePO₄-Schwellen BAT_SOC10_V / BAT_SOC50_V:
    Bei Last/unter Ladung verschiebt sich die Klemmenspannung → großzügige Hysterese nutzen.
  - PRECHARGE_MS nach Eingangskapazität des XL4015 und R_pre dimensionieren.
  - PI_ACK_PIN: Wenn nicht verdrahtet, optional ignorieren und nur via Timeout abschalten.

  ACHTUNG:
  - Die Spannungswerte sind Richtwerte und müssen an dein System angepasst werden!
  - Die Relais sollten nur kurz angesteuert werden, um sie nicht zu beschädigen.
  - Achte auf die Freilaufdioden der Relais, wenn du ULN2003 oder ähnliche Treiber verwendest.
  - Teste das System gründlich, bevor du es im Dauerbetrieb einsetzt!
*/

#include <Arduino.h> // type: ignore
#include <LowPower.h>

// ========================= Pins (anpassen, falls nötig) =========================
const uint8_t R1_RST = 2; // PV verbinden
const uint8_t R1_SET = 3; // PV trennen
const uint8_t R2_RST = 4; // Batterie verbinden
const uint8_t R2_SET = 5; // Batterie trennen
const uint8_t R3_RST = 6; // Precharge ein
const uint8_t R3_SET = 7; // Precharge aus
const uint8_t R4_RST = 8; // Hauptpfad XL4015 ein
const uint8_t R4_SET = 9; // Hauptpfad XL4015 aus

const uint8_t LED_RED = 10;    // LED rot
const uint8_t LED_YELLOW = 11; // LED gelb
const uint8_t LED_GREEN = 12;  // LED grün

const uint8_t PI_SHDN_PIN = 13; // Ausgang: HIGH → Pi soll Shutdown starten
const uint8_t PI_ACK_PIN = 14;  // Eingang: HIGH → Pi heruntergefahren (optional, sonst Pullup)

const uint8_t ADC_BAT = A1;
const uint8_t ADC_PV = A2;

// ========================= ADC & Teiler (ANPASSEN!) =========================
// Tipp: Bei hochohmigen Teilern je 100 nF direkt am ADC-Pin gegen GND.
// Referenz hier: Vcc = 5.0 V (Standard Pro Mini 5V)
const float ADC_REF_V = 5.0;
const float ADC_MAX = 1023.0;

// U_in = U_adc * ((Rtop + Rbottom) / Rbottom)
const float BAT_RTOP = 100000.0;   // z. B. 100k
const float BAT_RBOTTOM = 10000.0; // z. B. 10k
const float PV_RTOP = 180000.0;    // z. B. 180k
const float PV_RBOTTOM = 10000.0;  // z. B. 10k

// Oversampling (einfaches Mittel)
uint16_t readAdcAveraged(uint8_t pin, uint8_t samples = 16) {
    uint32_t acc = 0;
    for (uint8_t i = 0; i < samples; i++) {
        acc += analogRead(pin);
        delayMicroseconds(200);
    }
    return (uint16_t)(acc / samples);
}

inline float adcToVolt(uint16_t raw, float Rtop, float Rbottom) {
    const float u_adc = (raw * ADC_REF_V) / ADC_MAX;
    return u_adc * ((Rtop + Rbottom) / Rbottom);
}

// ========================= LiFePO₄-SoC-Schwellen (4S) =========================
// Richtwerte für *Ruhespannung* (Last weg, Temperatur ~20–25°C):
// ~10%: ≈12.9–13.0 V  |  ~50%: ≈13.2 V  |  ~100%: 13.5–13.6 V
// Hysterese großzügig, um Flattern zu vermeiden.
const float BAT_SOC10_V = 12.95;   // ~10% SoC → Hauptsystem herunterfahren
const float BAT_SOC10_HYST = 0.10; // 100 mV

const float BAT_SOC50_V = 13.20;   // ~50% SoC → Hauptsystem einschalten
const float BAT_SOC50_HYST = 0.08; // 80 mV

// ========================= PV-Entscheidung NUR über PV-Spannung =========================
// Reine PV-Heuristik mit Hysterese + Zeitfilter:
// - Wenn PV_V < PV_DISCONNECT für PV_DISCONNECT_HOLD_MS ⇒ Victron trennen
// - Wenn PV_V > PV_RECONNECT  für PV_RECONNECT_HOLD_MS  ⇒ Victron verbinden
// Richtwerte für 12V/36-Zeller (Vmp ~18 V, Voc ~21–23 V). Bitte einmessen!
const float PV_DISCONNECT_V = 14.0;           // darunter: praktisch keine Ladeleistung → trennen
const uint32_t PV_DISCONNECT_HOLD_MS = 60000; // 60 s stabil unterhalb

const float PV_RECONNECT_V = 16.5;            // darüber: tagsüber genug Licht vorhanden → verbinden
const uint32_t PV_RECONNECT_HOLD_MS = 120000; // 120 s stabil oberhalb

// ========================= Zeiten & Pulse =========================
const uint16_t RELAY_PULSE_MS = 40;
const uint32_t PRECHARGE_MS = 400;
const uint32_t PI_SHUTDOWN_HOLD_MS = 1000;
const uint32_t PI_SHUTDOWN_WAIT_MS = 60000; // 60 s auf Pi-ACK warten

const uint32_t STABLE_REQ_MS = 3000; // für SoC-Bedingungen

// ========================= Zustände =========================
enum class VictronState { Disconnected,
                          Connected };
enum class MainState { Off,
                       Precharging,
                       On,
                       ShuttingDown };

VictronState victron = VictronState::Disconnected;
MainState mainSys = MainState::Off;

bool pvConnected = false;  // gespiegelt aus R1
bool batConnected = false; // gespiegelt aus R2

// Timer / Marker
uint32_t t_precharge_start = 0;
uint32_t t_pi_shutdown = 0;
uint32_t t_cond_start_bat_low = 0;
uint32_t t_cond_start_bat_high = 0;
uint32_t t_pv_disconnect_start = 0;
uint32_t t_pv_reconnect_start = 0;

// ========================= Relais-Helfer =========================
void pulseHigh(uint8_t pin, uint16_t ms) {
    digitalWrite(pin, HIGH);
    delay(ms);
    digitalWrite(pin, LOW);
}

void R1_PV_CONNECT() {
    pulseHigh(R1_SET, RELAY_PULSE_MS);
    pvConnected = true;
}
void R1_PV_DISCONN() {
    pulseHigh(R1_RST, RELAY_PULSE_MS);
    pvConnected = false;
}
void R2_BAT_CONNECT() {
    pulseHigh(R2_SET, RELAY_PULSE_MS);
    batConnected = true;
}
void R2_BAT_DISCONN() {
    pulseHigh(R2_RST, RELAY_PULSE_MS);
    batConnected = false;
}
void R3_PRECH_ON() { pulseHigh(R3_SET, RELAY_PULSE_MS); }
void R3_PRECH_OFF() { pulseHigh(R3_RST, RELAY_PULSE_MS); }
void R4_MAIN_ON() { pulseHigh(R4_SET, RELAY_PULSE_MS); }
void R4_MAIN_OFF() { pulseHigh(R4_RST, RELAY_PULSE_MS); }

void connectVictronSafe() {
    if (!batConnected) {
        R2_BAT_CONNECT();
        delay(50);
    }
    if (!pvConnected) {
        R1_PV_CONNECT();
        delay(50);
    }
    victron = VictronState::Connected;
}

void disconnectVictronSafe() {
    if (pvConnected) {
        R1_PV_DISCONN();
        delay(50);
    }
    if (batConnected) {
        R2_BAT_DISCONN();
        delay(50);
    }
    victron = VictronState::Disconnected;
}

void mainOnSequence() {
    if (mainSys != MainState::Off)
        return;
    R3_PRECH_ON();
    t_precharge_start = millis();
    mainSys = MainState::Precharging;
}

void tickPrecharge() {
    if (mainSys != MainState::Precharging)
        return;
    if (millis() - t_precharge_start >= PRECHARGE_MS) {
        R4_MAIN_ON();
        delay(50);
        R3_PRECH_OFF();
        mainSys = MainState::On;
    }
}

void mainOffSequence() {
    if (mainSys == MainState::Off)
        return;
    R4_MAIN_OFF();
    delay(80);
    R3_PRECH_OFF();
    mainSys = MainState::Off;
}

void requestPiShutdownAndPowerOff() {
    if (mainSys == MainState::ShuttingDown || mainSys == MainState::Off)
        return;
    digitalWrite(PI_SHDN_PIN, HIGH);
    t_pi_shutdown = millis();
    delay(PI_SHUTDOWN_HOLD_MS);
    digitalWrite(PI_SHDN_PIN, LOW);
    mainSys = MainState::ShuttingDown;
}

void tickPiShutdown() {
    if (mainSys != MainState::ShuttingDown)
        return;
    bool ack = (digitalRead(PI_ACK_PIN) == HIGH); // ggf. invertieren/ändern, je nach Pi-Schaltung
    bool timeout = (millis() - t_pi_shutdown) >= PI_SHUTDOWN_WAIT_MS;
    if (ack || timeout) {
        mainOffSequence();
    }
}

// ========================= Setup =========================
void setup() {
    pinMode(R1_SET, OUTPUT);
    pinMode(R1_RST, OUTPUT);
    pinMode(R2_SET, OUTPUT);
    pinMode(R2_RST, OUTPUT);
    pinMode(R3_SET, OUTPUT);
    pinMode(R3_RST, OUTPUT);
    pinMode(R4_SET, OUTPUT);
    pinMode(R4_RST, OUTPUT);

    pinMode(LED_RED, OUTPUT);
    pinMode(LED_YELLOW, OUTPUT);
    pinMode(LED_GREEN, OUTPUT);

    pinMode(PI_SHDN_PIN, OUTPUT);
    digitalWrite(PI_SHDN_PIN, LOW);
    pinMode(PI_ACK_PIN, INPUT_PULLUP); // optional; wenn ungenutzt bleibt HIGH

    pinMode(ADC_BAT, INPUT);
    pinMode(ADC_PV, INPUT);

    // Sicherer Grundzustand nach Boot
    disconnectVictronSafe();
    mainOffSequence();

    Serial.begin(115200); // Baudrate frei wählen, muss zum Monitor passen
    delay(2000);          // (Tipp) kurzer Start-Delay, weil Öffnen des Monitors resetten kann
    Serial.println("Hallo vom Pro Mini!");

    // (Optional) Hier könnte man initial direkt Batterie->Victron verbinden,
    // wenn PV ausreichend ist – wir warten aber auf die PV-Reconnect-Bedingung.
}

// ========================= Hauptlogik =========================
void loop() {
    const uint32_t now = millis();

    // Spannungen einlesen
    float u_bat = adcToVolt(readAdcAveraged(ADC_BAT), BAT_RTOP, BAT_RBOTTOM);
    float u_pv = adcToVolt(readAdcAveraged(ADC_PV), PV_RTOP, PV_RBOTTOM);

    // ---------------- PV-ONLY Entscheidung für Victron ----------------
    // DISCONNECT-Pfad
    if (u_pv < PV_DISCONNECT_V) {
        if (t_pv_disconnect_start == 0)
            t_pv_disconnect_start = now;
        if ((now - t_pv_disconnect_start) >= PV_DISCONNECT_HOLD_MS) {
            if (victron == VictronState::Connected) {
                disconnectVictronSafe(); // erst PV, dann Batterie
            }
        }
    } else {
        t_pv_disconnect_start = 0; // Bedingung nicht mehr erfüllt → Timer zurücksetzen
    }

    // RECONNECT-Pfad
    if (u_pv > PV_RECONNECT_V) {
        if (t_pv_reconnect_start == 0)
            t_pv_reconnect_start = now;
        if ((now - t_pv_reconnect_start) >= PV_RECONNECT_HOLD_MS) {
            if (victron == VictronState::Disconnected) {
                connectVictronSafe(); // erst Batterie, dann PV
            }
        }
    } else {
        t_pv_reconnect_start = 0;
    }

    // ---------------- SoC-LOGIK (über Batterie-Ruhespannung) ----------------
    // 2) <~10% SoC → Pi sauber herunterfahren, dann XL4015 aus
    static bool soc_low_latched = false;
    static bool soc_high_latched = false;

    // Low-Bedingung
    if (u_bat <= BAT_SOC10_V) {
        if (t_cond_start_bat_low == 0)
            t_cond_start_bat_low = now;
        if ((now - t_cond_start_bat_low) >= STABLE_REQ_MS && !soc_low_latched) {
            soc_low_latched = true;
            // Pi Shutdown anstoßen; Power-Off folgt per tickPiShutdown()
            requestPiShutdownAndPowerOff();
        }
    } else if (u_bat >= (BAT_SOC10_V + BAT_SOC10_HYST)) {
        // Low-Latch wieder freigeben
        soc_low_latched = false;
        t_cond_start_bat_low = 0;
    }

    // Während Pi-Shutdown ggf. Hauptsystem aus schalten, sobald ACK/Timeout
    tickPiShutdown();

    // 3) >~50% SoC → Hauptsystem einschalten (nur wenn nicht gerade im Shutdown/Aus)
    if (u_bat >= BAT_SOC50_V && mainSys == MainState::Off && !soc_low_latched) {
        if (t_cond_start_bat_high == 0)
            t_cond_start_bat_high = now;
        if ((now - t_cond_start_bat_high) >= STABLE_REQ_MS && !soc_high_latched) {
            soc_high_latched = true;
            mainOnSequence();
        }
    } else if (u_bat <= (BAT_SOC50_V - BAT_SOC50_HYST)) {
        soc_high_latched = false;
        t_cond_start_bat_high = 0;
    }

    // Precharge-Phase abarbeiten
    tickPrecharge();

    // === LED-Statusanzeigen ===
    // Gelb: Victron/Solar (verbunden = HIGH)
    if (victron == VictronState::Connected)
        digitalWrite(LED_YELLOW, HIGH);
    else
        digitalWrite(LED_YELLOW, LOW);

    // Grün: Hauptsystem an
    if (mainSys == MainState::On)
        digitalWrite(LED_GREEN, HIGH);
    else
        digitalWrite(LED_GREEN, LOW);

    // Rot: Hauptsystem aus
    if (mainSys == MainState::Off)
        digitalWrite(LED_RED, HIGH);
    else
        digitalWrite(LED_RED, LOW);

    // 1 Sekunde schlafen
    LowPower.powerDown(SLEEP_1S, ADC_OFF, BOD_OFF);
}
