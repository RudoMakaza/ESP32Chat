#!/usr/bin/env python3
"""
ESP32 Chat - browser interface (Flask backend).

Each participant runs one instance of this server on their computer:

  1. The process opens the configured transport (a real ESP32 over USB
     serial, or the sim cloud over localhost TCP) - the same protocol as
     the desktop GUI.
  2. It serves a chat page on http://127.0.0.1:<port>.
  3. The browser sends messages with POST /send and polls GET /api/poll
     (lightweight, reliable on any WSGI server) for live updates.

Usage
-----
With real ESP32s:
    python web_app.py --port 5000 --mode serial --serial-port COM3

Simulation (no hardware; needs sim_server.py on 127.0.0.1:5555):
    python python/sim_server.py
    python web/web_app.py --port 5000 --mode sim          # participant 1
    python web/web_app.py --port 5001 --mode sim          # participant 2

The page also works on a phone/tablet pointed at http://<computer-ip>:5000.
"""

import argparse
import json
import os
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

# Make the shared transport layer importable regardless of the working dir.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from transport import SimTransport, SerialTransport, list_serial_ports

# ---------------------------------------------------------------------------
# In-memory event history; the browser polls it for live updates.
# ---------------------------------------------------------------------------

HISTORY = []            # list of {"id", "kind", "ts", ...} newest last
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 500
_LAST_ID = [0]
_NEXT_MSGID = [0]       # per-backend counter; combined with user name to key messages

STATE = {
    "bridge": None,
    "connected": False,
    "mode": "sim",
    "port": "5555",     # serial COM port OR sim server port, depending on mode
    "baud": 115200,
    "sim_host": "127.0.0.1",
}
STATE_LOCK = threading.Lock()


def push(kind, **fields):
    """Append an event to the history buffer (thread-safe)."""
    with HISTORY_LOCK:
        _LAST_ID[0] += 1
        event = {"id": _LAST_ID[0], "kind": kind, "ts": round(time.time(), 3), **fields}
        HISTORY.append(event)
        if len(HISTORY) > MAX_HISTORY:
            del HISTORY[: len(HISTORY) - MAX_HISTORY]
        return event


def push_status(text):
    push("status", text=text)


def push_message(user, msgid, text):
    push("message", user=user, msgid=msgid, text=text)


def new_msgid():
    _NEXT_MSGID[0] += 1
    return str(_NEXT_MSGID[0])


def on_mesh_frame(cmd, user, msgid, text):
    """Frames arriving from the mesh: MSG / EDIT / DEL."""
    if cmd == "MSG":
        push_message(user, msgid, text)
    elif cmd == "EDIT":
        push("edit", user=user, msgid=msgid, text=text)
    elif cmd == "DEL":
        push("del", user=user, msgid=msgid)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

def make_bridge(mode, port, host="127.0.0.1"):
    if mode == "serial":
        return SerialTransport(port=port, baud=STATE["baud"], on_status=push_status)
    return SimTransport(host=host, port=int(port), on_status=push_status)


def connect(mode, port, host="127.0.0.1"):
    with STATE_LOCK:
        if STATE["connected"]:
            return {"ok": True, "already": True}
        bridge = make_bridge(mode, port, host)
        try:
            bridge.start(on_mesh_frame)
        except Exception as exc:
            push_status("Connection failed: {0}".format(exc))
            return {"ok": False, "error": str(exc)}
        STATE["bridge"] = bridge
        STATE["connected"] = True
        STATE["mode"] = mode
        STATE["port"] = port
        STATE["sim_host"] = host
    push_status("Connected ({0}, {1})".format(mode, port))
    return {"ok": True}


def disconnect():
    with STATE_LOCK:
        bridge = STATE["bridge"]
        STATE["bridge"] = None
        STATE["connected"] = False
    if bridge is not None:
        bridge.stop()
    push_status("Disconnected")


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)


@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.route("/api/state")
def api_state():
    with STATE_LOCK:
        return jsonify({
            "connected": STATE["connected"],
            "mode": STATE["mode"],
            "port": STATE["port"],
            "ports": list_serial_ports(),
        })


@app.route("/api/poll")
def api_poll():
    """Return every event with id > `after`; `now` is the latest event id."""
    try:
        after = max(0, int(request.args.get("after", 0)))
    except ValueError:
        after = 0
    with HISTORY_LOCK:
        events = [e for e in HISTORY if e["id"] > after]
        now = _LAST_ID[0]
    return jsonify({"events": events, "now": now})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode") or "sim"
    port = body.get("port") or ("5555" if mode == "sim" else "COM3")
    host = body.get("host") or STATE["sim_host"]
    return jsonify(connect(mode, port, host))


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    disconnect()
    return jsonify({"ok": True})


@app.route("/send", methods=["POST"])
def api_send():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    user = (body.get("user") or "").strip().replace("|", " ")
    if not text or not user:
        return jsonify({"ok": False, "error": "text and user required"}), 400
    bridge = STATE["bridge"]
    if bridge is None or not STATE["connected"]:
        return jsonify({"ok": False, "error": "not connected"}), 409
    msgid = new_msgid()
    bridge.send_message(user, msgid, text)
    # Keep the sender's own copy in history so a reload keeps it too.
    push_message(user, msgid, text)
    return jsonify({"ok": True, "msgid": msgid})


@app.route("/api/edit", methods=["POST"])
def api_edit():
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "").strip().replace("|", " ")
    msgid = str(body.get("msgid") or "").strip()
    text = (body.get("text") or "").strip()
    if not text or not user or not msgid:
        return jsonify({"ok": False, "error": "user, msgid and text required"}), 400
    bridge = STATE["bridge"]
    if bridge is None or not STATE["connected"]:
        return jsonify({"ok": False, "error": "not connected"}), 409
    bridge.send_edit(user, msgid, text)
    push("edit", user=user, msgid=msgid, text=text)
    return jsonify({"ok": True})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "").strip().replace("|", " ")
    msgid = str(body.get("msgid") or "").strip()
    if not user or not msgid:
        return jsonify({"ok": False, "error": "user and msgid required"}), 400
    bridge = STATE["bridge"]
    if bridge is None or not STATE["connected"]:
        return jsonify({"ok": False, "error": "not connected"}), 409
    bridge.send_delete(user, msgid)
    push("del", user=user, msgid=msgid)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ESP32 chat - browser interface")
    parser.add_argument("--port", type=int, default=5000, help="HTTP port for this participant")
    parser.add_argument("--mode", choices=["serial", "sim"], default="sim")
    parser.add_argument("--serial-port", default=None, help="COM port for the local ESP32")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=5555)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    if args.mode == "serial":
        STATE["mode"] = "serial"
        STATE["port"] = args.serial_port or (list_serial_ports() or ["COM3"])[0]
    else:
        STATE["mode"] = "sim"
        STATE["port"] = str(args.sim_port)
        STATE["sim_host"] = args.sim_host

    host = STATE["sim_host"] if args.mode == "sim" else "127.0.0.1"
    connect(STATE["mode"], STATE["port"], host)

    url = "http://127.0.0.1:{0}".format(args.port)
    push_status("Serving chat page at {0}".format(url))
    if not args.no_browser:
        threading.Timer(0.8, _open_browser, args=(url,)).start()

    app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)


def _open_browser(url):
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    main()