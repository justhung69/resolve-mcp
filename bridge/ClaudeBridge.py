"""ClaudeBridge - runs INSIDE DaVinci Resolve (Workspace > Scripts > ClaudeBridge).

Resolve Free only exposes the scripting API to scripts launched from its own Scripts menu,
and such scripts are killed as soon as their main thread returns (verified on 21.0.4 Free).
So this launcher never returns: it serves newline-delimited JSON over 127.0.0.1 on its main
thread until it receives a `shutdown` request or Resolve quits.

All API logic lives in bridge_core.py (next to bridge.json in %LOCALAPPDATA%\\ResolveMCP), which
the `reload` op re-reads, so the bridge can be updated without clicking this script again.

Protocol (one JSON object per line, both directions):
    request : {"id": any, "token": str, "op": str, ...op fields}
    response: {"id": any, "ok": bool, "result": any} | {"id": any, "ok": false, "error": str, "traceback": str}
"""
import json
import os
import secrets
import selectors
import socket
import time
import traceback

LAUNCHER_VERSION = "1.0.0"
HOST = "127.0.0.1"
PORT = int(os.environ.get("RESOLVE_MCP_PORT", "47750"))
STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ResolveMCP")
INFO_FILE = os.path.join(STATE_DIR, "bridge.json")
CORE_FILE = os.path.join(STATE_DIR, "bridge_core.py")
LOG_FILE = os.path.join(STATE_DIR, "bridge.log")

R = resolve  # noqa: F821 - injected by Resolve for menu scripts


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def load_core():
    ns = {"__name__": "bridge_core", "resolve": R, "LAUNCHER_VERSION": LAUNCHER_VERSION}
    with open(CORE_FILE, encoding="utf-8") as f:
        exec(compile(f.read(), CORE_FILE, "exec"), ns)
    return ns


def respond(core, line, token):
    try:
        req = json.loads(line)
    except ValueError as e:
        return {"id": None, "ok": False, "error": "bad json: %s" % e}, core, False
    rid = req.get("id")
    if not secrets.compare_digest(str(req.get("token", "")), token):
        return {"id": rid, "ok": False, "error": "bad token"}, core, False
    op = req.get("op")
    if op == "shutdown":
        return {"id": rid, "ok": True, "result": "bye"}, core, True
    if op == "reload":
        try:
            core = load_core()
            log("core reloaded (%s)" % core.get("CORE_VERSION"))
            return {"id": rid, "ok": True, "result": {"core_version": core.get("CORE_VERSION")}}, core, False
        except Exception as e:
            return {"id": rid, "ok": False, "error": "reload failed, old core kept: %s" % e,
                    "traceback": traceback.format_exc()}, core, False
    return core["handle"](req), core, False


def already_running():
    try:
        with open(INFO_FILE, encoding="utf-8") as f:
            info = json.load(f)
        c = socket.create_connection((HOST, info["port"]), timeout=2)
        c.sendall((json.dumps({"id": 0, "token": info["token"], "op": "ping"}) + "\n").encode())
        ok = b'"ok": true' in c.recv(65536)
        c.close()
        return ok
    except Exception:
        return False


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    if already_running():
        print("[ClaudeBridge] already running - nothing to do.")
        return
    core = load_core()
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((HOST, PORT))  # loopback only
    srv.listen(8)
    srv.setblocking(False)
    with open(INFO_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": PORT, "token": token, "pid": os.getpid(), "started": time.time(),
                   "version": LAUNCHER_VERSION, "resolve": R.GetVersionString()}, f)
    log("bridge %s listening on %s:%d (pid %d, Resolve %s)" % (LAUNCHER_VERSION, HOST, PORT, os.getpid(), R.GetVersionString()))
    print("[ClaudeBridge] listening on %s:%d - Claude can now control Resolve." % (HOST, PORT))

    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, None)
    buffers = {}
    running = True
    while running:
        for key, _ in sel.select(timeout=1.0):
            if key.data is None:
                conn, _ = srv.accept()
                conn.setblocking(True)
                buffers[conn] = b""
                sel.register(conn, selectors.EVENT_READ, "c")
                continue
            conn = key.fileobj
            try:
                chunk = conn.recv(1 << 20)
            except OSError:
                chunk = b""
            if not chunk:
                sel.unregister(conn)
                conn.close()
                buffers.pop(conn, None)
                continue
            buffers[conn] += chunk
            while b"\n" in buffers[conn]:
                line, buffers[conn] = buffers[conn].split(b"\n", 1)
                if not line.strip():
                    continue
                resp, core, stop = respond(core, line.decode("utf-8", "replace"), token)
                try:
                    conn.sendall((json.dumps(resp, default=repr) + "\n").encode("utf-8"))
                except OSError:
                    pass
                if stop:
                    running = False
    for key in list(sel.get_map().values()):
        key.fileobj.close()
    try:
        os.remove(INFO_FILE)
    except OSError:
        pass
    log("bridge stopped")
    print("[ClaudeBridge] stopped.")


try:
    main()
except Exception:
    log("bridge crashed:\n" + traceback.format_exc())
    raise
