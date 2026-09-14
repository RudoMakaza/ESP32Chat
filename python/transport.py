"""
Transport layer for the ESP32 chat system.

A Transport moves newline-delimited text frames between the Python GUI
and the wireless mesh. Two implementations are provided:

  SerialTransport  - talks to a real ESP32 over a USB (serial) cable.
                     Frames:
                       PC  -> ESP32 :  SEND|<user>|<text>
                       ESP32 -> PC  :  RECV|<user>|<text>   /  OK  /  ERR|..  /  PONG

  SimTransport     - talks to sim_server.py over localhost TCP, so the
                     whole application can be tested with no hardware.
                     The server relays RECV frames exactly like an
                     ESP-NOW broadcast (never echoed back to the sender).

A Transport is started with a callback ``handle_frame(cmd, user, msgid, text)``
that runs on the transport's reader thread. Keep it short and thread-safe.

Wire protocol v2 (frames are newline-terminated UTF-8):

  Serial, PC -> ESP32   :  SEND|<user>|<msgid>|<text>
                            EDIT|<user>|<msgid>|<text>
                            DEL|<user>|<msgid>
  Serial, ESP32 -> PC   :  MSG|<user>|<msgid>|<text>  (message arrived)
                            EDIT|<user>|<msgid>|<text> (message edited)
                            DEL|<user>|<msgid>         (message deleted)
                            ACK / ERR|<reason>         (broadcast status)
  Air (ESP-NOW payload) :  C|<user>|<msgid>|<text>     E|<user>|<msgid>|<text>
                            D|<user>|<msgid>
"""

import abc
import socket
import threading


class Transport(abc.ABC):
    """Minimal duplex frame transport used by the chat GUI."""

    def __init__(self, on_status=None):
        self.on_status = on_status or (lambda text: None)
        self.running = False
        self._reader = None

    # -- lifecycle ---------------------------------------------------------

    @abc.abstractmethod
    def internal_start(self):
        """Open the link and start the reader loop."""

    @abc.abstractmethod
    def internal_stop(self):
        """Close the link and the reader loop."""

    @abc.abstractmethod
    def write_line(self, line):
        """Push one newline-terminated text frame down the link."""

    # -- concrete helpers --------------------------------------------------

    def start(self, handle_frame):
        self.handle_frame = handle_frame
        self._stop_evt = threading.Event()
        self.internal_start()
        self.running = True

    def stop(self):
        self.running = False
        self._stop_evt.set()
        try:
            self.internal_stop()
        except Exception:
            pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)

    def send_message(self, user, msgid, text):
        """Broadcast a new chat message."""
        self.write_line("SEND|{0}|{1}|{2}".format(user, msgid, text))

    def send_edit(self, user, msgid, text):
        """Broadcast an edit of message `msgid` sent by `user`."""
        self.write_line("EDIT|{0}|{1}|{2}".format(user, msgid, text))

    def send_delete(self, user, msgid):
        """Broadcast a delete of message `msgid` sent by `user`."""
        self.write_line("DEL|{0}|{1}".format(user, msgid))

    def report_status(self, text):
        self.on_status(text)


# ---------------------------------------------------------------------------
# Serial transport (real ESP32 hardware)
# ---------------------------------------------------------------------------

class SerialTransport(Transport):
    def __init__(self, port, baud=115200, on_status=None):
        super().__init__(on_status)
        self.port = port
        self.baud = baud
        self._ser = None

    def internal_start(self):
        import serial  # imported lazily so the sim mode needs no pyserial

        self.report_status("Connecting to {0} ...".format(self.port))
        self._ser = serial.Serial(self.port, self.baud, timeout=0.2)
        self._ser.reset_input_buffer()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.report_status("Connected on {0} @ {1}".format(self.port, self.baud))

    def internal_stop(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def write_line(self, line):
        if self._ser is None:
            self.report_status("Disconnected - message dropped")
            return
        try:
            self._ser.write((line + "\n").encode("utf-8"))
        except Exception as exc:
            self.report_status("Write failed: {0}".format(exc))

    def _read_loop(self):
        self._ser.reset_input_buffer()
        while not self._stop_evt.is_set():
            try:
                raw = self._ser.readline()
            except Exception:
                break
            if not raw:
                continue
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if line:
                self._dispatch(line)

    def _dispatch(self, line):
        # ACK / PONG / OK|ready appear after a broadcast or at ESP32 boot.
        if line in ("ACK", "PONG") or line.startswith("OK|"):
            self.report_status("ESP32 ready")
            return
        if line.startswith("ERR|"):
            self.report_status("ESP32 error: " + line[4:])
            return
        parts = line.split("|")
        cmd = parts[0]
        if cmd == "MSG":
            if len(parts) >= 4:
                self.handle_frame("MSG", parts[1], parts[2], "|".join(parts[3:]))
            else:
                self.handle_frame("MSG", parts[1], "", parts[2])
            return
        if cmd == "RECV":  # legacy firmware frame
            self.handle_frame("MSG", parts[1], "", parts[2])
            return
        if cmd == "EDIT" and len(parts) >= 4:
            self.handle_frame("EDIT", parts[1], parts[2], "|".join(parts[3:]))
            return
        if cmd == "DEL" and len(parts) >= 3:
            self.handle_frame("DEL", parts[1], parts[2], "")
            return
        # Ignore unexpected lines silently.


# ---------------------------------------------------------------------------
# Simulated transport (no hardware - chat over localhost via sim_server.py)
# ---------------------------------------------------------------------------

class SimTransport(Transport):
    def __init__(self, host="127.0.0.1", port=5555, on_status=None):
        super().__init__(on_status)
        self.host = host
        self.port = port
        self._sock = None

    def internal_start(self):
        self.report_status("Connecting to sim server {0}:{1} ...".format(self.host, self.port))
        self._sock = socket.create_connection((self.host, self.port), timeout=5)
        self._sock.settimeout(0.2)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.report_status("Joined the group (simulated ESP-NOW)")

    def internal_stop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def write_line(self, line):
        if self._sock is None:
            self.report_status("Disconnected - message dropped")
            return
        try:
            self._sock.sendall((line + "\n").encode("utf-8"))
        except Exception as exc:
            self.report_status("Send failed: {0}".format(exc))

    def _read_loop(self):
        buf = b""
        while not self._stop_evt.is_set():
            try:
                chunk = self._sock.recv(1024)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    text = line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if text:
                    self._dispatch(text)
        self.report_status("Disconnected from sim server")

    def _dispatch(self, line):
        # A chat frame on the wire carries user|msgid|text; the sender never
        # receives its own broadcast, so whatever arrives is from the mesh.
        parts = line.split("|")
        cmd = parts[0]
        if cmd in ("MSG", "SEND", "RECV"):
            if len(parts) >= 4:
                self.handle_frame("MSG", parts[1], parts[2], "|".join(parts[3:]))
            else:
                self.handle_frame("MSG", parts[1], "", parts[2])
            return
        if cmd == "EDIT" and len(parts) >= 4:
            self.handle_frame("EDIT", parts[1], parts[2], "|".join(parts[3:]))
            return
        if cmd == "DEL" and len(parts) >= 3:
            self.handle_frame("DEL", parts[1], parts[2], "")
            return
        if cmd == "STATUS":
            self.report_status("|".join(parts[1:]))
            return
        self.report_status("Sim server: " + line)


def list_serial_ports():
    """Return available COM ports. Falls back to [COM3..COM5] if pyserial is absent."""
    try:
        import serial.tools.list_ports as lp

        return sorted({p.device for p in lp.comports()})
    except Exception:
        return ["COM3", "COM4", "COM5"]