"""Stops a running ClaudeBridge (Workspace > Scripts > ClaudeBridge_Stop)."""
import json
import os
import socket

INFO_FILE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ResolveMCP", "bridge.json")

try:
    with open(INFO_FILE, encoding="utf-8") as f:
        info = json.load(f)
    c = socket.create_connection(("127.0.0.1", info["port"]), timeout=5)
    c.sendall((json.dumps({"id": 1, "token": info["token"], "op": "shutdown"}) + "\n").encode())
    print("[ClaudeBridge_Stop]", c.recv(4096).decode().strip())
    c.close()
except FileNotFoundError:
    print("[ClaudeBridge_Stop] bridge is not running.")
except OSError as e:
    print("[ClaudeBridge_Stop] could not reach bridge: %s" % e)
