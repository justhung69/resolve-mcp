"""Client for the in-Resolve ClaudeBridge (newline-delimited JSON over 127.0.0.1)."""
import itertools
import json
import os
import socket
import threading
import time

STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ResolveMCP")
INFO_FILE = os.path.join(STATE_DIR, "bridge.json")

NOT_RUNNING = ("The ClaudeBridge is not running inside DaVinci Resolve. Open Resolve, load a project, then click "
               "Workspace > Scripts > ClaudeBridge (one click per Resolve session). "
               "Resolve Free blocks external scripting, so this click cannot be automated through the API "
               "(a computer-use agent can click it).")


class BridgeError(Exception):
    """The bridge answered with ok=false (the Resolve call itself failed)."""

    def __init__(self, message, traceback_text=None):
        super().__init__(message)
        self.traceback = traceback_text


class BridgeUnavailable(Exception):
    """No bridge to talk to."""


class BridgeClient:
    def __init__(self, timeout=60.0):
        self.timeout = timeout
        self._sock = None
        self._buf = b""
        self._info = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # -- connection
    def _read_info(self):
        try:
            with open(INFO_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            raise BridgeUnavailable(NOT_RUNNING)

    def _connect(self):
        self._info = self._read_info()
        try:
            self._sock = socket.create_connection(("127.0.0.1", self._info["port"]), timeout=5)
        except OSError:
            self._sock = None
            raise BridgeUnavailable(NOT_RUNNING + " (bridge.json exists but nothing is listening - Resolve may have been closed)")
        self._buf = b""

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def _roundtrip(self, payload, timeout):
        self._sock.settimeout(timeout)
        self._sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        deadline = time.time() + timeout
        while b"\n" not in self._buf:
            if time.time() > deadline:
                raise TimeoutError("bridge did not answer within %.0fs" % timeout)
            chunk = self._sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("bridge closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    # -- public
    def request(self, op, timeout=None, **fields):
        """Send one op. Retries once on a dropped connection (e.g. the bridge was restarted)."""
        timeout = timeout or self.timeout
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect()
                    payload = dict(fields, op=op, id=next(self._ids), token=self._info["token"])
                    resp = self._roundtrip(payload, timeout)
                    break
                except BridgeUnavailable:
                    raise
                except TimeoutError:
                    # The bridge is single-threaded: an answer may still arrive later, so drop this socket.
                    self.close()
                    raise BridgeError("Resolve did not answer '%s' within %.0fs. Resolve may be busy "
                                      "(rendering, modal dialog open?)." % (op, timeout))
                except (OSError, ConnectionError, ValueError) as e:
                    self.close()
                    if attempt == 2:
                        raise BridgeUnavailable("%s (%s)" % (NOT_RUNNING, e))
        if resp.get("error") == "bad token":
            self.close()
            raise BridgeUnavailable("bridge token mismatch - restart the ClaudeBridge script.")
        if not resp.get("ok"):
            raise BridgeError(resp.get("error", "unknown error"), resp.get("traceback"))
        return resp.get("result")

    def call(self, target, method, *args, timeout=None):
        return self.request("call", target=target, method=method, args=list(args), timeout=timeout)

    def try_call(self, target, method, *args, default=None):
        try:
            return self.call(target, method, *args)
        except BridgeError:
            return default


def handle_id(h):
    """Accept either a handle dict {"$h": id} or a bare id string."""
    if isinstance(h, dict):
        return h.get("$h")
    return h


def handle_type(h):
    hid = handle_id(h)
    if hid == "resolve":
        return "Resolve"
    if not hid:
        return None
    for sep in (":", "@"):
        if sep in hid:
            return hid.split(sep, 1)[0]
    return None
