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
#include <EEPROM.h>
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

const uint8_t LED_RED = 12;    // LED rot
const uint8_t LED_YELLOW = 11; // LED gelb
const uint8_t LED_GREEN = 10;  // LED grün

const uint8_t PI_SHDN_PIN = 13; // Ausgang: HIGH → Pi soll Shutdown starten

const uint8_t ADC_BAT = A1;
const uint8_t ADC_PV = A2;

// ========================= ADC & Teiler (ANPASSEN!) =========================
// Tipp: Bei hochohmigen Teilern je 100 nF direkt am ADC-Pin gegen GND.
// Referenz hier: Vcc = 5.0 V (Standard Pro Mini 5V)
const float ADC_REF_V = 5.0;
const float ADC_MAX = 1023.0;

// U_in = U_adc * ((Rtop + Rbottom) / Rbottom)
// Angepasst: aus Kalibrierung mit Messpaaren (berechnet→gemessen)
// Faktor ≈ 1.04 → Rtop neu gewählt, damit (Rtop+Rbottom)/Rbottom ≈ 11.439
const float BAT_RTOP = 104400.0;   // angepasst ~104.4k
const float BAT_RBOTTOM = 10000.0; // z. B. 10k
// Nach Kalibrierung (zwei Messpunkte: 19.07/18.39 und 16.28/15.68)
// Faktor ≈ mean(19.07/18.39, 16.28/15.68) ≈ 1.0379 -> neuer Rtop ≈ 187.2k
const float PV_RTOP = 187200.0;   // angepasst ~187.2k
const float PV_RBOTTOM = 10000.0; // z. B. 10k

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
const float PV_DISCONNECT_V = 14.0;          // darunter: praktisch keine Ladeleistung → trennen
const uint32_t PV_DISCONNECT_HOLD_MS = 2000; // 60 s stabil unterhalb

const float PV_RECONNECT_V = 16.5;          // darüber: tagsüber genug Licht vorhanden → verbinden
const uint32_t PV_RECONNECT_HOLD_MS = 2000; // 120 s stabil oberhalb

// ========================= Zeiten & Pulse =========================
const uint16_t RELAY_PULSE_MS = 40;
const uint32_t PRECHARGE_MS = 2000;
const uint32_t PI_SHUTDOWN_HOLD_MS = 1500;
const uint32_t PI_SHUTDOWN_WAIT_MS = 1000; // 30 s warten

const uint32_t STABLE_REQ_MS = 3000; // für SoC-Bedingungen

// ========================= Zustände =========================
enum class VictronState { Disconnected,
                          Connected };
enum class MainState { Off,
                       On };

VictronState victron = VictronState::Disconnected;
MainState mainSys = MainState::Off;

// Glättung: gleitender Mittelwert aus den letzten N Messungen
const uint8_t VOLTAGE_AVG_N = 5; // Anzahl Samples im Fenster
float bat_hist[VOLTAGE_AVG_N] = {0};
float pv_hist[VOLTAGE_AVG_N] = {0};
uint8_t vol_hist_idx = 0;   // Einfügeindex (0..VOLTAGE_AVG_N-1)
uint8_t vol_hist_count = 0; // wie viele Werte aktuell vorhanden (bis VOLTAGE_AVG_N)
// Laufende Summen für effiziente Mittelwertberechnung
float sum_bat = 0.0f;
float sum_pv = 0.0f;

// Globales Flag: Setzt setup() nach Prüfung der seriellen Schnittstelle
bool serial_active = false;
const char *reset_cause = NULL;
// EEPROM-Marker zur Detektion von Abstürzen während kritischer Sequenzen
const uint8_t EEPROM_BOOT_MARK_ADDR = 0;
const uint8_t EEPROM_BOOT_MARK_VAL = 0x42;

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

// --------------------- Serielles Logging (nicht-blockierend) ---------------------
// Heuristische Prüfung, ob ein serieller Host empfängt. Bei Pro Mini (FTDI/USB-Serial)
// gibt es keine zuverlässige API, daher verwenden wir availableForWrite() als Heuristik.
inline bool serialPortOpen() {
    return (Serial.availableForWrite() > 16);
}

// Wrapper-Funktionen: Prüfen das vorher in setup() gesetzte Flag und
// schreiben nur, wenn `serial_active == true`.
inline void log(const char *s) {
    if (!serial_active)
        return;
    Serial.print(s);
    delay(5); // Kurzer Flush-Warteaufruf
}
inline void logln(const char *s) {
    if (!serial_active)
        return;
    Serial.println(s);
    delay(5); // Kurzer Flush-Warteaufruf
}

// ========================= Relais-Helfer =========================
void pulseHigh(uint8_t pin, uint16_t ms) {
    digitalWrite(pin, HIGH);
    delay(ms);
    digitalWrite(pin, LOW);
}

void R1_PV_CONNECT() { pulseHigh(R1_SET, RELAY_PULSE_MS); }
void R1_PV_DISCONN() { pulseHigh(R1_RST, RELAY_PULSE_MS); }
void R2_BAT_CONNECT() { pulseHigh(R2_SET, RELAY_PULSE_MS); }
void R2_BAT_DISCONN() { pulseHigh(R2_RST, RELAY_PULSE_MS); }
void R3_PRECH_ON() { pulseHigh(R3_SET, RELAY_PULSE_MS); }
void R3_PRECH_OFF() { pulseHigh(R3_RST, RELAY_PULSE_MS); }
void R4_MAIN_ON() { pulseHigh(R4_SET, RELAY_PULSE_MS); }
void R4_MAIN_OFF() { pulseHigh(R4_RST, RELAY_PULSE_MS); }

void connectVictronSafe() {
    if (victron == VictronState::Connected)
        return;

    log("MPPT connecting...");

    R2_BAT_CONNECT();
    delay(1000);
    R1_PV_CONNECT();

    victron = VictronState::Connected;
    logln("OK");
    delay(1000);
}

void disconnectVictronSafe(bool force = false) {

    if (victron == VictronState::Disconnected && !force)
        return;

    log("MPPT disconnecting...");

    R1_PV_DISCONN();
    delay(50);
    R2_BAT_DISCONN();

    victron = VictronState::Disconnected;
    logln("OK.");
}

void mainOnSequence() {
    // If main system already ON, do nothing
    if (mainSys == MainState::On)
        return;

    // EEPROM-Marker setzen: beginne kritische Sequenz (Precharge)
    EEPROM.update(EEPROM_BOOT_MARK_ADDR, EEPROM_BOOT_MARK_VAL);

    log("Precharge....");
    R3_PRECH_ON();
    delay(PRECHARGE_MS);
    R4_MAIN_ON();
    mainSys = MainState::On;
    logln("Hauptsystem an");

    // Erfolgreicher Abschluss: Marker löschen
    EEPROM.update(EEPROM_BOOT_MARK_ADDR, 0);
}

void mainOffSequence() {
    R4_MAIN_OFF();
    delay(80);
    R3_PRECH_OFF();
    mainSys = MainState::Off;
    logln("Hauptsystem aus");
}

void requestPiShutdownAndPowerOff() {
    // If already Off, nothing to do
    if (mainSys == MainState::Off)
        return;

    log("Pi shutdown...");
    digitalWrite(PI_SHDN_PIN, HIGH);
    // Kurzer Puls zum Starten des Pi-Shutdowns
    delay(PI_SHUTDOWN_HOLD_MS);
    digitalWrite(PI_SHDN_PIN, LOW);
    delay(PI_SHUTDOWN_WAIT_MS);
    logln("OK.");
    mainOffSequence();
}

// ========================= Setup =========================
void setup() {
    uint8_t mcusr = MCUSR;
    MCUSR = 0; // Flags zurücksetzen
    if (mcusr & (1 << PORF))
        reset_cause = "Reset Ursache: Power-On Reset (POR)";
    else if (mcusr & (1 << EXTRF))
        reset_cause = "Reset Ursache: Externer Reset (DTR/Seriell)";
    else if (mcusr & (1 << BORF))
        reset_cause = "Reset Ursache: Brown-out Reset (BOR)";
    else if (mcusr & (1 << WDRF))
        reset_cause = "Reset Ursache: Watchdog Reset";

    // Prüfe EEPROM-Marker vom vorherigen Lauf (z.B. Absturz in Precharge)
    uint8_t prev_mark = EEPROM.read(EEPROM_BOOT_MARK_ADDR);
    if (prev_mark == EEPROM_BOOT_MARK_VAL) {
        // Vorheriger Lauf hat während kritischer Sequenz (Precharge/main) abgebrochen
        // Log nur falls Serial aktiv wird später
        // Wir hängen diese Info an reset_cause an (oder loggen separat)
        reset_cause = "Vorheriger Lauf: Absturz waehrend Precharge/Hauptsequenz";
        // Marker löschen
        EEPROM.update(EEPROM_BOOT_MARK_ADDR, 0);
    }

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

    pinMode(ADC_BAT, INPUT);
    pinMode(ADC_PV, INPUT);

    // Sicherer Grundzustand nach Boot
    disconnectVictronSafe(true);
    mainOffSequence();

    Serial.begin(115200); // Baudrate frei wählen, muss zum Monitor passen
    digitalWrite(LED_YELLOW, HIGH);
    delay(1000); // (Tipp) kurzer Start-Delay, weil Öffnen des Monitors resetten kann
    digitalWrite(LED_YELLOW, LOW);
    // Prüfen, ob serieller Host tatsächlich offen ist (heuristisch)
    serial_active = serialPortOpen();

    logln("Hallo vom Pro Mini!");
    if (reset_cause) {
        logln(reset_cause);
        reset_cause = NULL;
    }

    // (Optional) Hier könnte man initial direkt Batterie->Victron verbinden,
    // wenn PV ausreichend ist – wir warten aber auf die PV-Reconnect-Bedingung.
}

// ========================= Hauptlogik =========================
void loop() {
    const uint32_t now = millis();

    // Spannungen einlesen (je Messzyklus einmal) und ins Historien-Window schieben
    float u_bat_sample = adcToVolt(readAdcAveraged(ADC_BAT), BAT_RTOP, BAT_RBOTTOM);
    float u_pv_sample = adcToVolt(readAdcAveraged(ADC_PV), PV_RTOP, PV_RBOTTOM);

    // In zirkuläre Puffer schreiben mit laufender Summe
    if (vol_hist_count < VOLTAGE_AVG_N) {
        // Puffer noch nicht voll: einfach hinzufügen
        bat_hist[vol_hist_idx] = u_bat_sample;
        pv_hist[vol_hist_idx] = u_pv_sample;
        sum_bat += u_bat_sample;
        sum_pv += u_pv_sample;
        vol_hist_idx = (vol_hist_idx + 1) % VOLTAGE_AVG_N;
        vol_hist_count++;
    } else {
        // Puffer voll: ältestes Element entfernen und durch neues ersetzen
        sum_bat -= bat_hist[vol_hist_idx];
        sum_pv -= pv_hist[vol_hist_idx];
        bat_hist[vol_hist_idx] = u_bat_sample;
        pv_hist[vol_hist_idx] = u_pv_sample;
        sum_bat += u_bat_sample;
        sum_pv += u_pv_sample;
        vol_hist_idx = (vol_hist_idx + 1) % VOLTAGE_AVG_N;
    }

    // Gemittelten Wert aus dem vorhandenen Fenster berechnen
    float u_bat = 0.0f;
    float u_pv = 0.0f;
    if (vol_hist_count > 0) {
        u_bat = sum_bat / vol_hist_count;
        u_pv = sum_pv / vol_hist_count;
    }

    // Debug: gemittelte Batterie- und PV-Spannung seriell ausgeben
    log("u_bat(avg): ");
    {
        char _floatbuf[16];
        // dtostrf(value, width, precision, buf)
        dtostrf(u_bat, 5, 2, _floatbuf);
        log(_floatbuf);
    }
    log(" V, ");
    log("u_pv(avg): ");
    {
        char _floatbuf2[16];
        dtostrf(u_pv, 5, 2, _floatbuf2);
        log(_floatbuf2);
    }
    log(" V, Status: ");

    // Einfacher 3-Zeichen-Status: PV, Batterie, Hauptsystem
    // '_' = AUS, '*' = AN
    char sMPPT = (victron == VictronState::Connected ? '*' : '_');
    char sMAIN = (mainSys == MainState::On) ? '*' : '_';
    {
        char _sMPPT[2] = {sMPPT, '\0'};
        char _sMAIN2[2] = {sMAIN, '\0'};
        log(_sMPPT);
        logln(_sMAIN2);
    }

    // ---------------- PV-ONLY Entscheidung für Victron ----------------
    if (u_pv < PV_DISCONNECT_V) {
        disconnectVictronSafe(); // erst PV, dann Batterie
    }

    if (u_pv > PV_RECONNECT_V) {
        connectVictronSafe(); // erst Batterie, dann PV
    }

    // ---------------- Vereinfachte SoC-LOGIK (sofortige Reaktion) ----------------
    if (u_bat <= BAT_SOC10_V) {
        requestPiShutdownAndPowerOff();
    }

    if (u_bat >= BAT_SOC50_V) {
        mainOnSequence();
    }

    // Precharge- und Shutdown-Wartezeiten werden direkt in den Sequenzen per Sleep abgearbeitet

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
    // Flush serial to avoid truncation/corruption of outgoing bytes when MCU sleeps
    LowPower.powerDown(SLEEP_1S, ADC_OFF, BOD_OFF);
}
