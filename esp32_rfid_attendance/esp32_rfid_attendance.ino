#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <MFRC522.h>

const char* WIFI_SSID = "lab4";
const char* WIFI_PASSWORD = "123456789";

const char* ATTENDANCE_API_URL = "http://192.168.137.71:8000/api/iot/attendance/";

const char* DEVICE_CODE = "GATE-005-KAMPALA-ROAD-001";
const char* API_KEY = "baEQJpikiu2v6mGjam7_0wIu2vcuQfEl";

// ESP8266 GPIO NUMBERS
#define SS_PIN 2       // D4
#define RST_PIN 0      // D3
#define BUZZER 16      // D0

#define LCD_SDA 4      // D2
#define LCD_SCL 5      // D1

LiquidCrystal_I2C lcd(0x27, 16, 2);
MFRC522 rfid(SS_PIN, RST_PIN);

String getUID() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toLowerCase();
  return uid;
}

void beep(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER, HIGH);
    delay(150);
    digitalWrite(BUZZER, LOW);
    delay(150);
  }
}

void showReady() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("SECURITY CHECK");
  lcd.setCursor(0, 1);
  lcd.print("Scan Card...");
}

void connectWiFi() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Connecting WiFi");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    lcd.setCursor(attempts % 16, 1);
    lcd.print(".");
    attempts++;
  }

  lcd.clear();

  if (WiFi.status() == WL_CONNECTED) {
    lcd.setCursor(0, 0);
    lcd.print("WiFi Connected");
    lcd.setCursor(0, 1);
    lcd.print(WiFi.localIP());
    beep(1);
  } else {
    lcd.setCursor(0, 0);
    lcd.print("WiFi Failed");
    lcd.setCursor(0, 1);
    lcd.print("Check WiFi");
    beep(3);
  }

  delay(2000);
}

bool sendAttendanceSwipe(String uid) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  WiFiClient client;
  HTTPClient http;

  http.begin(client, ATTENDANCE_API_URL);
  http.addHeader("Content-Type", "application/json");

  String payload = "{";
  payload += "\"device_code\":\"" + String(DEVICE_CODE) + "\",";
  payload += "\"api_key\":\"" + String(API_KEY) + "\",";
  payload += "\"card_id\":\"" + uid + "\",";
  payload += "\"action\":\"auto\"";
  payload += "}";

  int statusCode = http.POST(payload);
  String response = http.getString();

  http.end();

  Serial.print("HTTP Status: ");
  Serial.println(statusCode);
  if (statusCode <= 0) {
    Serial.print("HTTP Error: ");
    Serial.println(http.errorToString(statusCode));
  }
  Serial.print("Payload: ");
  Serial.println(payload);
  Serial.println(response);

  lcd.clear();

  if (statusCode >= 200 && statusCode < 300 && response.indexOf("\"success\": true") >= 0) {
    lcd.setCursor(0, 0);
    lcd.print("Swipe Accepted");

    if (response.indexOf("Check-out") >= 0) {
      lcd.setCursor(0, 1);
      lcd.print("CHECKED OUT");
      beep(2);
    } else {
      lcd.setCursor(0, 1);
      lcd.print("CHECKED IN");
      beep(1);
    }

    return true;
  }

  lcd.setCursor(0, 0);
  lcd.print("Swipe Rejected");
  lcd.setCursor(0, 1);

  if (statusCode == 403) {
    lcd.print("Bad Device Key");
  } else if (statusCode <= 0) {
    lcd.print("Server Offline");
  } else {
    lcd.print("Check Django");
  }

  beep(3);
  return false;
}

void setup() {
  Serial.begin(115200);

  pinMode(BUZZER, OUTPUT);
  digitalWrite(BUZZER, LOW);

  Wire.begin(LCD_SDA, LCD_SCL);

  lcd.init();
  lcd.backlight();

  SPI.begin();
  rfid.PCD_Init();

  lcd.setCursor(0, 0);
  lcd.print("RFID Attendance");
  lcd.setCursor(0, 1);
  lcd.print("Starting...");
  delay(1500);

  connectWiFi();
  showReady();
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent()) {
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    return;
  }

  String uid = getUID();

  Serial.print("Card UID: ");
  Serial.println(uid);

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Card Read");
  lcd.setCursor(0, 1);
  lcd.print(uid.substring(0, 16));

  sendAttendanceSwipe(uid);

  delay(2500);
  showReady();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}


