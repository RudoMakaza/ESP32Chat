/*
 * ESP32 Chat Bridge Firmware
 * --------------------------
 *
 * Each ESP32 sits on a USB cable between a computer (Python chat GUI)
 * and the wireless group. It does two simple jobs:
 *
 *   1. PC -> AIR: read lines from the serial port in the form
 *        SEND|<user>|<msgid>|<text>     (new message)
 *        EDIT|<user>|<msgid>|<text>     (message edited)
 *        DEL|<user>|<msgid>             (message deleted)
 *      and broadcast them to every other ESP32 over WiFi using ESP-NOW.
 *
 *   2. AIR -> PC: receive an ESP-NOW broadcast frame in the form
 *        C|<user>|<msgid>|<text>        (message)
 *        E|<user>|<msgid>|<text>        (edited)
 *        D|<user>|<msgid>               (deleted)
 *      and print it to the serial port in the receiver-facing form
 *        MSG|<user>|<msgid>|<text>  /  EDIT|<user>|<msgid>|<text>  /  DEL|<user>|<msgid>
 *      so the computer's Python GUI can render it.
 *
 * The radio runs in Wi-Fi AP mode on a fixed channel so that no router
 * is required: every ESP32 in the room shares channel 1 and exchanges
 * frames directly via ESP-NOW broadcast (MAC FF:FF:FF:FF:FF:FF).
 *
 * Serial protocol (newline separated, UTF-8):
 *   PC -> ESP32  :  SEND|EDIT|DEL|...   (see above)
 *   ESP32 -> PC  :  MSG / EDIT / DEL frames
 *                  ACK                  (broadcast transmitted)
 *                  ERR|<reason>         (failure)
 *                  PONG                 (reply to PING)
 *
 * Air protocol (ESP-NOW payload, max 250 bytes):
 *   C|<user>|<msgid>|<text>   E|<user>|<msgid>|<text>   D|<user>|<msgid>
 */

#include <WiFi.h>
#include <esp_now.h>

#define SERIAL_BAUD 115200
#define ESP_NOW_CHANNEL 1
#define MAX_OTA_PAYLOAD 250

// Broadcast MAC address (all ESP32s in the group)
const uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Buffers
String serialBuf = "";        // partial line read from USB serial

// ---------------------------------------------------------------------------
// Radio callbacks
// ---------------------------------------------------------------------------

// A chat frame arrived over the air -> forward it to the computer over USB.
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void onDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
#else
void onDataRecv(const uint8_t *mac, const uint8_t *incomingData, int len) {
#endif
  if (len < 1) return;
  if (len > MAX_OTA_PAYLOAD) len = MAX_OTA_PAYLOAD;
  char buf[MAX_OTA_PAYLOAD + 1];
  memcpy(buf, incomingData, len);
  buf[len] = '\0';
  String frame = String(buf);

  int p1 = frame.indexOf('|');
  int p2 = frame.indexOf('|', p1 + 1);
  if (p1 < 0 || p2 < 0) return;                 // malformed frame
  String cmd = frame.substring(0, p1);
  String user = frame.substring(p1 + 1, p2);
  String rest = frame.substring(p2 + 1);

  int p3 = rest.indexOf('|');
  if (p3 < 0) {
    // D|<user>|<msgid>
    if (cmd == "D") {
      Serial.print("DEL|");
      Serial.print(user);
      Serial.print("|");
      Serial.println(rest);
    }
    return;
  }

  String msgid = rest.substring(0, p3);
  String text = rest.substring(p3 + 1);

  if (cmd == "C") {
    Serial.print("MSG|");
  } else if (cmd == "E") {
    Serial.print("EDIT|");
  } else {
    return;                                     // unknown frame, drop
  }
  Serial.print(user);
  Serial.print("|");
  Serial.print(msgid);
  Serial.print("|");
  Serial.println(text);
}

// Send status callback (only used to keep the PC informed).
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void onDataSent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
#else
void onDataSent(const uint8_t *mac, esp_now_send_status_t status) {
#endif
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "ACK" : "ERR|send_timeout");
}

// ---------------------------------------------------------------------------
// Radio setup
// ---------------------------------------------------------------------------

void setupRadio() {
  // AP mode fixes the radio channel without needing a Wi-Fi router.
  WiFi.mode(WIFI_AP);
  WiFi.softAP("ESP32-Chat", NULL, ESP_NOW_CHANNEL);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ERR|esp_now_init_failed");
    return;
  }

  esp_now_register_recv_cb(onDataRecv);
  esp_now_register_send_cb(onDataSent);

  // Register the broadcast peer so esp_now_send() is allowed.
  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = ESP_NOW_CHANNEL;
  peerInfo.encrypt = false;
  esp_now_add_peer(&peerInfo);
}

// ---------------------------------------------------------------------------
// Serial handling
// ---------------------------------------------------------------------------

void broadcastFrame(const String &cmd, const String &user,
                    const String &msgid, const String &text) {
  String frame = cmd + "|" + user + "|" + msgid;
  if (text.length() > 0) {
    frame += "|" + text;
  }
  if (frame.length() > MAX_OTA_PAYLOAD) {
    frame = frame.substring(0, MAX_OTA_PAYLOAD);
  }

  esp_err_t result = esp_now_send(broadcastAddress, (const uint8_t *)frame.c_str(), frame.length());
  if (result != ESP_OK) {
    Serial.println("ERR|esp_now_send_failed");
  }
  // On success the onDataSent() callback prints "ACK".
}

void handleSerialLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line == "PING") {
    Serial.println("PONG");
    return;
  }

  // Expected: COMMAND|<user>|<msgid>[|<text>]
  int p1 = line.indexOf('|');
  if (p1 < 0) return;
  String cmd = line.substring(0, p1);
  String rest = line.substring(p1 + 1);

  int p2 = rest.indexOf('|');
  if (p2 < 0) return;
  String user = rest.substring(0, p2);
  String rest2 = rest.substring(p2 + 1);

  int p3 = rest2.indexOf('|');
  String msgid = p3 < 0 ? rest2 : rest2.substring(0, p3);
  String text = p3 < 0 ? "" : rest2.substring(p3 + 1);

  if (cmd == "SEND") {
    broadcastFrame("C", user, msgid, text);
  } else if (cmd == "EDIT") {
    broadcastFrame("E", user, msgid, text);
  } else if (cmd == "DEL") {
    broadcastFrame("D", user, msgid, "");
  }
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      handleSerialLine(serialBuf);
      serialBuf = "";
    } else if (serialBuf.length() < 512) {
      serialBuf += c;
    }
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) {
    delay(10);
  }
  Serial.println("PONG");            // boot handshake: PC knows the node is live
  setupRadio();
  Serial.println("OK|ready");
}