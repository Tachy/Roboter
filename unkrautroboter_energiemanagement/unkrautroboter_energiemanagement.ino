#include <Wire.h>
#include <Adafruit_INA260.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

Adafruit_INA260 ina260;

// OLED Display (I²C)
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// Relais- und LED-Pins
const int pinRelayMainSet    = 5;
const int pinRelayMainReset  = 6;
const int pinRelaySolarDisconnect = 7;
const int pinLEDGreen  = 10;
const int pinLEDYellow = 11;
const int pinLEDRed    = 13;

unsigned long lastCheck = 0;
bool systemOn = true;
bool displayAvailable = false;

float readBatteryVoltage() {
  return ina260.readBusVoltage() / 1000.0;
}

float readPower() {
  return ina260.readPower() / 1000.0;  // mW → W
}

void pulseRelay(int pin) {
  digitalWrite(pin, HIGH);
  delay(50);
  digitalWrite(pin, LOW);
}

void setup() {
  pinMode(pinRelayMainSet, OUTPUT);
  pinMode(pinRelayMainReset, OUTPUT);
  pinMode(pinRelaySolarDisconnect, OUTPUT);
  pinMode(pinLEDGreen, OUTPUT);
  pinMode(pinLEDYellow, OUTPUT);
  pinMode(pinLEDRed, OUTPUT);

  Serial.begin(9600);
  while (!Serial);

  if (!ina260.begin()) {
    Serial.println("INA260 nicht gefunden!");
    while (1);
  }

  digitalWrite(pinRelayMainReset, LOW);
  digitalWrite(pinRelayMainSet, LOW);
  digitalWrite(pinRelaySolarDisconnect, LOW);
  digitalWrite(pinLEDGreen, HIGH);
  digitalWrite(pinLEDYellow, LOW);
  digitalWrite(pinLEDRed, LOW);
}

void loop() {
  unsigned long now = millis();

  float voltage = readBatteryVoltage();
  float power = readPower();

  // Debug
  Serial.print("Spannung: "); Serial.print(voltage); Serial.print(" V  ");
  Serial.print("Leistung: "); Serial.print(power); Serial.println(" W");

  // OLED-Anzeige aktivieren, falls Display jetzt verfügbar und noch nicht initialisiert
  if (!displayAvailable) {
    if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
      displayAvailable = true;
      display.clearDisplay();
      display.setTextSize(1);
      display.setTextColor(SSD1306_WHITE);
    }
  }

  // OLED-Anzeige nur wenn Display aktiv (Strom liegt an)
  if (displayAvailable) {
    display.clearDisplay();
    display.setCursor(0, 0);
    display.print("Spannung: ");
    display.print(voltage, 2);
    display.println(" V");

    display.print("Leistung: ");
    display.print(power, 2);
    display.println(" W");

    display.print("Status: ");
    display.println(systemOn ? "AN" : "AUS");
    display.display();
  }

  // SYSTEM AKTIV
  if (systemOn && now - lastCheck > 30000UL) {
    lastCheck = now;
    if (voltage < 11.2) {
      pulseRelay(pinRelayMainReset);
      systemOn = false;

      digitalWrite(pinLEDGreen, LOW);
      digitalWrite(pinLEDRed, HIGH);
      digitalWrite(pinLEDYellow, LOW);
    }
  }

  // SYSTEM INAKTIV
  if (!systemOn && now - lastCheck > 900000UL) {
    lastCheck = now;
    digitalWrite(pinRelaySolarDisconnect, HIGH);
    delay(3000);

    voltage = readBatteryVoltage();
    power = readPower();
    digitalWrite(pinRelaySolarDisconnect, LOW);

    if (voltage > 13.0) {
      pulseRelay(pinRelayMainSet);
      systemOn = true;

      digitalWrite(pinLEDGreen, HIGH);
      digitalWrite(pinLEDRed, LOW);
      digitalWrite(pinLEDYellow, LOW);
    } else {
      digitalWrite(pinLEDYellow, (voltage > 11.5) ? HIGH : LOW);
    }
  }
}
