#!/usr/bin/env python3
"""
sim_server.py - wireless cloud simulator for ESP32 chat.

A tiny TCP server that mimics the ESP-NOW radio behaviour so the whole
Python application can be developed and tested without any hardware:

  - multiple chat windows connect to it like ESP32s join a radio channel,
  - every line a client sends is relayed to ALL the other clients,
  - a message is never echoed back to its sender (like ESP-NOW broadcast),
  - a line is relayed as RECV|<user>|<text> so each GUI renders it.

Usage:
    python sim_server.py [--host 127.0.0.1] [--port 5555]

Then in other terminals run:
    python chat_app.py --mode sim --port 5555
"""

import argparse
import socket
import threading

client_lock = threading.Lock()
clients = set()


def broadcast(origin, data):
    """Send raw bytes to every connected client except the origin."""
    data = data.rstrip(b"\r\n")
    if not data:
        return
    # A SEND frame that arrived here is like an ESP-NOW air frame seen by a
    # receiver: rewrite SEND|<user>|<msgid>|<text> into MSG|<user>|<msgid>|<text>.
    # EDIT| and DEL| frames are already receiver-facing and pass through.
    if data.startswith(b"SEND|"):
        data = b"MSG|" + data[len(b"SEND|"):]
    with client_lock:
        targets = list(clients)
    dead = []
    for client in targets:
        if client is origin:
            continue
        try:
            client.sendall(data + b"\n")
        except OSError:
            dead.append(client)
    if dead:
        with client_lock:
            for client in dead:
                clients.discard(client)


def handle(client, addr):
    name = getattr(client, "peer_name", addr)
    print("[+] connected: {0}:{1}".format(addr[0], addr[1]))
    with client_lock:
        clients.add(client)
    message = "STATUS|joined the group ({0}/{1})".format(len(clients), len(clients))
    try:
        client.sendall(message.encode() + b"\n")
    except OSError:
        pass
    try:
        buf = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                broadcast(client, line)
    except OSError:
        pass
    finally:
        with client_lock:
            clients.discard(client)
        try:
            client.close()
        except OSError:
            pass
        print("[-] disconnected: {0}:{1}".format(addr[0], addr[1]))


def main():
    parser = argparse.ArgumentParser(description="ESP32 chat wireless cloud simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(16)
    print("Simulated ESP-NOW cloud on {0}:{1}".format(args.host, args.port))
    print("Connect clients with:  python chat_app.py --mode sim --port {0}".format(args.port))
    while True:
        client, addr = server.accept()
        threading.Thread(target=handle, args=(client, addr), daemon=True).start()


if __name__ == "__main__":
    main()