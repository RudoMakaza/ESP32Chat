# ESP32 Wireless Chat

A chat application that lets people talk to each other over a **wireless mesh of
ESP32 boards**, each plugged into a computer through a USB cable.

```
┌──────────────┐  USB serial   ┌─────────────┐                                        ┌─────────────┐   USB serial  ┌──────────────┐
│   Computer 1 │◄════════════►│   ESP32 A   │◄──────────────────────────────────────►│   ESP32 B   │◄════════════►│   Computer 2 │
│  Python GUI  │               │ (ESP-NOW)   │   WiFi broadcast (no router needed!)   │ (ESP-NOW)   │               │  Python GUI  │
└──────────────┘  (cable)      └─────────────┘                                        └─────────────┘   (cable)      └──────────────┘
                                    │                                                     │
                                    ▼                                                     ▼
                               ESP32 C ...                                           ESP32 D ...
```

Every message typed in a Python window is pushed down the USB cable to the local
ESP32, which broadcasts it **over WiFi** to every other ESP32 using Espressif's
**ESP-NOW** protocol. Each receiving ESP32 sends the message up its own USB cable
to its computer, and the Python GUI displays it. No router, access point or
internet connection is required — the ESP32 boards are the network.

---

## 1. How it works

### Message flow (one message, one receiver)

1. The user types `hello team` in the Python interface and presses Enter.
2. `chat_app.py` sends the frame `SEND|<username>|<text>` over its COM port.
3. The local ESP32 reads the line and transmits an ESP-NOW broadcast frame
   `C|<username>|<text>` with destination MAC `FF:FF:FF:FF:FF:FF`.
4. Every ESP32 in range (on the same WiFi channel) receives the frame.
5. Each receiver prints `RECV|<username>|<text>` to its serial port.
6. `chat_app.py` on each computer parses that line and renders it in the log.

### Wireless layer: ESP-NOW

| Property          | Value                                            |
|-------------------|--------------------------------------------------|
| Protocol          | ESP-NOW (Espressif, peer-to-peer, connectionless)|
| Mode              | Wi-Fi `SOFTAP`, fixed channel **1**              |
| Destination       | Broadcast `FF:FF:FF:FF:FF:FF`                    |
| Max payload       | 250 bytes per frame                              |
| Router needed?    | No — the boards talk directly to each other      |
| Range             | Typical indoor 20–50 m (ESP32 DevKit antennas)   |

The firmware creates an AP named `ESP32-Chat` purely to lock the radio to channel
1. All boards in the group must be on the same channel, which they are by default.

### Message protocol

**Over the USB cable (newline-terminated lines, UTF-8):**

| Direction         | Frame                          | Meaning                       |
|-------------------|--------------------------------|-------------------------------|
| PC → ESP32        | `SEND|<user>|<msgid>|<text>`   | broadcast a new message       |
| PC → ESP32        | `EDIT|<user>|<msgid>|<text>`   | broadcast an edit             |
| PC → ESP32        | `DEL|<user>|<msgid>`           | broadcast a delete            |
| ESP32 → PC        | `MSG|<user>|<msgid>|<text>`    | a message arrived             |
| ESP32 → PC        | `EDIT|<user>|<msgid>|<text>`   | a message was edited          |
| ESP32 → PC        | `DEL|<user>|<msgid>`           | a message was deleted         |
| ESP32 → PC        | `ACK` / `ERR|<reason>`         | broadcast status              |
| both              | `PING` / `PONG`                | health check                  |

**Over the air (ESP-NOW payload):**

```
C|<user>|<msgid>|<text>      E|<user>|<msgid>|<text>      D|<user>|<msgid>
```

> `msgid` is a short per-node counter. A message is keyed by `user:msgid`, so an
> `EDIT` or `DEL` frame can be matched and applied on every node — editing or
> deleting a message updates it everywhere, firewall-style, in the same fire-
> and-forget broadcast model as sending. The `|` separator is positional:
> `SEND`, `EDIT`, `DEL` and the user are split first, everything after the third
> `|` is the message text.

---

## 2. Project layout

```
ESP32Chat/
├── README.md
├── requirements.txt
├── python/                        # runs on each computer
│   ├── chat_app.py                # Tkinter desktop interface (entry point)
│   ├── transport.py               # serial bridge + simulation transport
│   └── sim_server.py              # no-hardware cloud simulator
├── web/                           # browser interface (Flask)
│   ├── web_app.py                 # per-participant backend
│   └── static/index.html          # chat page (SSE live updates)
└── esp32_firmware/
    └── esp32_chat/
        └── esp32_chat.ino         # Arduino sketch flashed to each ESP32
```

---

## 3. Hardware you need

- One **ESP32 DevKit** (e.g. ESP32-DevKitC / NodeMCU-32S) per participant
- One USB-to-micro-USB (or USB-C) cable per module
- A computer per participant (Windows / macOS / Linux)

Wiring is just the USB cable — the ESP32 boards carry their own antenna and
regulator; no breadboard or external parts are required.

---

## 4. Flash the firmware

1. Install the [Arduino IDE](https://www.arduino.cc/en/software) (or Arduino CLI).
2. Add the **ESP32 board**: File → Preferences → Additional boards manager URLs →
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   then Boards Manager → search *esp32* → install **esp32 by Espressif Systems**.
3. Open `esp32_firmware/esp32_chat/esp32_chat.ino`.
4. Tools → Board → **ESP32 Dev Module**, Port → the COM port of that ESP32.
5. Click **Upload**. Repeat for every ESP32 (they are all identical — no per-node
   setup or MAC-pairing is needed thanks to broadcast).

> **Compatibility Note:** The firmware includes dual-compatibility preprocessor support for both modern ESP32 Arduino Core v3.x and legacy v2.x without requiring manual code modifications.

---

## 5. Run the Python interface

```powershell
pip install -r requirements.txt
python python/chat_app.py --mode serial --port COM3
```

Do the same on every computer (each with its own COM port):

```powershell
python python/chat_app.py --mode serial --port COM5
```

In the window:

1. Type a **display name** (defaults to the computer's host name).
2. Keep **Mode = Serial ESP32**, pick the COM port of the attached ESP32.
3. Click **Connect** — the status dot turns green and `ESP32 ready` appears.
4. Type a message and press **Enter** or click **Send**.

### Without any hardware (simulator)

The radio cloud can be emulated over localhost so the entire GUI and message flow
can be tested on one machine:

```powershell
python python/sim_server.py                      # terminal 1
python python/chat_app.py --mode sim --port 5555 # terminal 2
python python/chat_app.py --mode sim --port 5555 # terminal 3, etc.
```

`sim_server.py` behaves like the ESP-NOW radio: a message from one window is
broadcast to every *other* window and never echoed back to the sender.

---

## 5b. Run in the browser (Flask)

Each participant runs a small backend that serves a live chat page. The page
needs no install and works on phones/tablets on the same LAN.

```powershell
# terminal 1 - the radio cloud (sim) or a real ESP32 per participant
python python/sim_server.py

# participant 1
python web/web_app.py --port 5000 --mode sim

# participant 2 (second computer or another tab)
python web/web_app.py --port 5001 --mode sim
```

A browser tab opens automatically at `http://127.0.0.1:5000`. For hardware,
each participant uses their own COM port:

```powershell
python web/web_app.py --port 5000 --mode serial --serial-port COM3
```

How it works: the backend attaches to the same transport protocol
(`SEND`/`RECV` over serial or the TCP cloud), pushes arrivals to the page live
via Server-Sent Events (`/stream`), and accepts new messages via `POST /send`.
The `--mode` / port can be switched inside the page (Connect / Disconnect).

---

## 6. Tuning & extensions

**Multiple groups in one room** — change `ESP_NOW_CHANNEL` in the firmware
(e.g. to 3, 6 or 11) and re-flash. Groups on different channels don't hear each
other.

**Longer messages** — ESP-NOW caps frames at 250 bytes. The firmware truncates;
raise the serial readable input by keeping messages short in the GUI.

**Internet-wide chat** — to extend the same hardware beyond WiFi range, replace
the ESP-NOW hop with MQTT:

```cpp
// Concept only — requires PubSubClient + a broker address.
// Each ESP32 publishes to "chat/group1" and subscribes to it as well,
// so messages flow over the internet instead of the radio mesh.
```

Alternatively, host `sim_server.py` on a small VPS and use
`python chat_app.py --mode sim --host <server_ip>` — but the ESP32 radio path is
the point of this project.

**Privacy** — frames are unencrypted. For hostile environments enable ESP-NOW
encryption (`peerInfo.encrypt = true` with a shared 16-byte PMK) in the sketch.

---

## 7. Troubleshooting

| Symptom                                   | Fix                                                         |
|-------------------------------------------|-------------------------------------------------------------|
| `Connect` errors right away               | Check the COM port in Windows Device Manager / `dmesg`.     |
| Connected but messages never arrive       | All boards must be on the same channel; re-flash same code. |
| `NameError` / module missing in Python    | `pip install -r requirements.txt` (pyserial, flask).       |
| GUI renders but nothing appears in log    | Open the Serial Monitor at 115200 to see `RECV|x|...` lines.|
| Broadcast works only sometimes            | Keep boards within ~30 m or add more ESP32 relays.          |