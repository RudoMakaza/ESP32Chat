#!/usr/bin/env python3
"""
ESP32 Chat - Python desktop interface.

Run with real hardware:
    python chat_app.py --mode serial --port COM3

Run in simulation (needs sim_server.py running first):
    python sim_server.py
    python chat_app.py --mode sim --port 5555

You can open several chat_app windows on one machine; every window
joins the same group and messages are broadcast to all (except the sender,
exactly like the ESP-NOW radio).
"""

import argparse
import queue
import socket
import sys
import time

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext
    from tkinter import messagebox
except ImportError:
    sys.exit("Tkinter is required. Install it with your Python distribution.")

from transport import SerialTransport, SimTransport, list_serial_ports

# ---------------------------------------------------------------------------
# UI theme
# ---------------------------------------------------------------------------

COLORS = {
    "bg":      "#10151c",
    "panel":   "#151c26",
    "panel2":  "#1c2532",
    "accent":  "#14b8a6",   # teal
    "accent2": "#0f9488",
    "text":    "#e6edf3",
    "muted":   "#8b98a5",
    "mine":    "#0f3d3a",
    "theirs":  "#1e2a3a",
    "online":  "#22c55e",
    "offline": "#64748b",
}

TIMESTAMP_FMT = "%H:%M:%S"


class ChatApp:
    """Main chat window. One per computer / ESP32."""

    def __init__(self, root, mode, port):
        self.root = root
        self.mode = mode
        self.port = port
        self.username = socket.gethostname().split(".")[0]
        self.bridge = None
        self.connected = False
        self._msg_seq = 0
        self._ui_q = queue.Queue()          # events from bridge thread -> UI thread

        root.title("ESP32 Chat")
        root.configure(bg=COLORS["bg"])
        root.geometry("560x640")
        root.minsize(460, 520)

        self._build_style()
        self._build_layout()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_ui_queue)
        self.root.after(2000, self._refresh_ports)

    # ------------------------------------------------------------------ UI
    def _build_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("TCombobox", fieldbackground=COLORS["panel"],
                        background=COLORS["panel"], foreground=COLORS["text"],
                        arrowcolor=COLORS["text"])
        style.configure("TEntry", fieldbackground=COLORS["panel"],
                        foreground=COLORS["text"], insertcolor=COLORS["text"])
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel"])],
                  foreground=[("readonly", COLORS["text"])])

    def _build_layout(self):
        # ---- connection bar -------------------------------------------------
        bar = tk.Frame(self.root, bg=COLORS["panel"], padx=10, pady=8)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(bar, text="Name", bg=COLORS["panel"], fg=COLORS["muted"]).pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value=self.username)
        tk.Entry(bar, textvariable=self.name_var, bg=COLORS["panel2"], fg=COLORS["text"],
                 relief=tk.FLAT, width=14, insertbackground=COLORS["text"]).pack(
            side=tk.LEFT, padx=(4, 10))

        tk.Label(bar, text="Mode", bg=COLORS["panel"], fg=COLORS["muted"]).pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="Serial ESP32" if self.mode == "serial" else "Simulator")
        self.mode_combo = ttk.Combobox(
            bar, textvariable=self.mode_var, state="readonly", width=12,
            values=["Serial ESP32", "Simulator"])
        self.mode_combo.pack(side=tk.LEFT, padx=(4, 10))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        self.port_var = tk.StringVar(value=self.port)
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, width=8,
                                       values=list_serial_ports())
        self.port_combo.pack(side=tk.LEFT, padx=(4, 4))

        btn_style = {
            "bg": COLORS["accent"], "fg": "#06231f", "activebackground": COLORS["accent2"],
            "relief": tk.FLAT, "font": ("Segoe UI", 9, "bold"), "cursor": "hand2", "bd": 0,
        }
        self.connect_btn = tk.Button(bar, text="Connect", command=self.toggle_connect, **btn_style)
        self.connect_btn.pack(side=tk.LEFT, padx=6)

        self.bulb = tk.Label(bar, text="\u25CF", bg=COLORS["panel"], fg=COLORS["offline"],
                             font=("Segoe UI", 12))
        self.bulb.pack(side=tk.LEFT, padx=(2, 4))

        # ---- chat log -------------------------------------------------------
        self.log = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state=tk.DISABLED, bg=COLORS["bg"], fg=COLORS["text"],
            relief=tk.FLAT, padx=12, pady=10, font=("Segoe UI", 10),
            highlightthickness=0, insertbackground=COLORS["text"])
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.log.tag_configure("meta", foreground=COLORS["muted"], font=("Segoe UI", 9, "italic"))
        self.log.tag_configure("mine", lmargin1=8)
        self.log.tag_configure("theirs", lmargin1=8)

        # ---- status bar -----------------------------------------------------
        self.status_var = tk.StringVar(value="Not connected")
        status = tk.Label(self.root, textvariable=self.status_var, bg=COLORS["panel"],
                          fg=COLORS["muted"], anchor=tk.W, padx=10, pady=4,
                          font=("Segoe UI", 9))
        status.pack(fill=tk.X, side=tk.BOTTOM)

        # ---- composer -------------------------------------------------------
        comp = tk.Frame(self.root, bg=COLORS["bg"], padx=8, pady=6)
        comp.pack(fill=tk.X, side=tk.BOTTOM)

        self.msg_var = tk.StringVar()
        self.msg_entry = tk.Entry(comp, textvariable=self.msg_var, bg=COLORS["panel2"],
                                  fg=COLORS["text"], insertbackground=COLORS["text"],
                                  relief=tk.FLAT, font=("Segoe UI", 10))
        self.msg_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.msg_entry.bind("<Return>", lambda e: self.send())
        self.msg_entry.bind("<KeyRelease>", lambda e: self._toggle_send_state())

        self.send_btn = tk.Button(comp, text="Send", state=tk.DISABLED, command=self.send, **btn_style)
        self.send_btn.pack(side=tk.LEFT, padx=(6, 0))

    # ------------------------------------------------------------------ logic
    def _on_mode_change(self, _event=None):
        mode = self.mode_var.get()
        if mode == "Simulator":
            self.port_combo.config(values=["5555"])
            self.port_var.set("5555")
        else:
            self.port_combo.config(values=list_serial_ports())
            self.port_var.set("COM3")

    def _refresh_ports(self):
        if self.mode_var.get() == "Serial ESP32" and not self.connected:
            self.port_combo.config(values=list_serial_ports())
        self.root.after(3000, self._refresh_ports)

    def log_system(self, text):
        self._append("[" + time.strftime(TIMESTAMP_FMT) + "] " + text, "meta")

    def _append(self, text, tag):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n", tag)
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def receive_message(self, user, text):
        ts = time.strftime(TIMESTAMP_FMT)
        self._append("  {0}  {1}\n{2}".format(ts, user, text), "theirs")
        self._append("  \u2500" * 28, "meta")

    def own_message(self, user, text):
        ts = time.strftime(TIMESTAMP_FMT)
        self._append("  {0}  {1} (me)".format(ts, user), "mine")
        self._append("  {0}".format(text), "mine")
        self._append("  " + "\u2500" * 28, "meta")

    def set_connected(self, state):
        self.connected = state
        self.bulb.config(fg=COLORS["online"] if state else COLORS["offline"])
        self.connect_btn.config(text="Disconnect" if state else "Connect",
                                bg=COLORS["accent"] if not state else "#3b4757",
                                fg="#06231f" if not state else COLORS["text"])
        self._toggle_send_state()

    def _toggle_send_state(self):
        on = self.connected and bool(self.msg_var.get().strip())
        self.send_btn.config(state=tk.NORMAL if on else tk.DISABLED)

    # ------------------------------------------------------------------ events
    def toggle_connect(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Enter a display name first.")
            return
        self.username = name.replace("|", " ").strip()

        if self.mode_var.get() == "Simulator":
            port = int(self.port_var.get() or 5555)
            self.bridge = SimTransport(port=port, on_status=self._status_from_thread)
        else:
            port = self.port_var.get().strip() or "COM3"
            self.bridge = SerialTransport(port=port, on_status=self._status_from_thread)

        try:
            self.bridge.start(self._frame_from_thread)
        except Exception as exc:  # serial errors, refused connections, ...
            messagebox.showerror("Connection failed", str(exc))
            self.bridge = None
            return

        self.set_connected(True)
        self.log_system("Connected - group messages are broadcast over the mesh.")
        self.msg_entry.focus_set()

    def disconnect(self):
        if self.bridge is not None:
            self.bridge.stop()
            self.bridge = None
        self.set_connected(False)
        self.log_system("Disconnected")
        self.status_var.set("Not connected")

    def send(self):
        text = self.msg_var.get().strip()
        if not self.connected or not text or self.bridge is None:
            return
        # Serial and air frames are single-line; collapse the rich text.
        text = " ".join(text.splitlines()).strip()
        self._msg_seq += 1
        msgid = "m{0}".format(self._msg_seq)
        self.msg_var.set("")
        self.own_message(self.username, text)
        self.bridge.send_message(self.username, msgid, text)
        self._toggle_send_state()
        self.msg_entry.focus_set()

    def on_close(self):
        self.disconnect()
        self.root.destroy()

    # ---------------------------------------------------------- thread bridge
    def _status_from_thread(self, text):
        self._ui_q.put(("status", text))

    def _frame_from_thread(self, cmd, user, msgid, text):
        if cmd == "MSG":
            self._ui_q.put(("recv", user, text))
        elif cmd == "EDIT":
            self._ui_q.put(("status", "{0} edited a message".format(user)))
        elif cmd == "DEL":
            self._ui_q.put(("status", "{0} deleted a message".format(user)))

    def _poll_ui_queue(self):
        try:
            while True:
                item = self._ui_q.get_nowait()
                if item[0] == "status":
                    self.status_var.set(item[1])
                elif item[0] == "recv":
                    self.receive_message(item[1], item[2])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui_queue)


def main():
    parser = argparse.ArgumentParser(description="ESP32 wireless chat interface")
    parser.add_argument("--mode", choices=["serial", "sim"], default="serial",
                        help="transport mode (default: serial)")
    parser.add_argument("--port", default=None,
                        help="serial port (e.g. COM3) or sim server port")
    args = parser.parse_args()

    mode = args.mode
    port = args.port
    if port is None:
        port = "5555" if mode == "sim" else (list_serial_ports() or ["COM3"])[0]

    root = tk.Tk()
    ChatApp(root, mode, port)
    root.mainloop()


if __name__ == "__main__":
    main()