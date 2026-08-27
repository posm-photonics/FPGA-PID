#!/usr/bin/env python3
"""
gui/server/posm_server.py

The main daemon (docs/10_gui_implementation_plan.md, section 2). Opens
a WebSocket listener, accepts client connections, routes incoming
messages (protocol.py) to the right handler, and serves the static
browser client (gui/client/) over plain HTTP on the same port. This is
the only thing that needs to run on the Red Pitaya.

Implemented with the standard library only (http.server + socket +
hashlib/base64 for the WebSocket handshake) so there is nothing to pip
install on the board, matching the constraint build/posm_reg_server.py
already documents for itself.

Usage:
    # simulation mode, nothing to plug in, safe to run anywhere:
    python3 posm_server.py --mock --port 8000

    # real hardware, run as root on the Red Pitaya:
    sudo python3 posm_server.py --base 0x40600000 --port 8000

Then open http://<host>:8000/ in a browser.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT_DIR = HERE.parent / "client"
CONFIG_DIR = HERE / "configs"

# Make both the GUI package and the repository RTL packages importable no
# matter whether this module is launched from the repo root or UI/Interface.
GUI_ROOT = HERE.parent.parent
REPO_ROOT = HERE.parents[4]
sys.path.insert(0, str(GUI_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from gui.server import parameters as P  # noqa: E402
from gui.server import protocol as PROTO  # noqa: E402
from gui.server.scope_streamer import ScopeStreamer  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_EVENT_LOG = 500


# =====================================================================
# Minimal WebSocket framing (RFC 6455), stdlib only.
# =====================================================================
class WSConnection:
    OP_TEXT = 0x1
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    def __init__(self, sock):
        self.sock = sock
        self.write_lock = threading.Lock()
        self._closed = False

    def send_text(self, text: str):
        payload = text.encode("utf-8")
        frame = self._build_frame(self.OP_TEXT, payload)
        with self.write_lock:
            if self._closed:
                return
            try:
                self.sock.sendall(frame)
            except OSError:
                self._closed = True

    def send_json(self, obj):
        self.send_text(json.dumps(obj))

    def close(self, code: int = 1000):
        if self._closed:
            return
        try:
            with self.write_lock:
                self.sock.sendall(self._build_frame(self.OP_CLOSE, struct.pack("!H", code)))
        except OSError:
            pass
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    @property
    def closed(self):
        return self._closed

    def _build_frame(self, opcode: int, payload: bytes) -> bytes:
        header = bytes([0x80 | opcode])  # FIN=1, no extensions
        length = len(payload)
        if length < 126:
            header += bytes([length])
        elif length < (1 << 16):
            header += bytes([126]) + struct.pack("!H", length)
        else:
            header += bytes([127]) + struct.pack("!Q", length)
        return header + payload

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf += chunk
        return buf

    def recv_text(self) -> "str | None":
        """Blocks for one full message; returns None on clean close."""
        while True:
            try:
                first2 = self._recv_exact(2)
            except (ConnectionError, OSError):
                self._closed = True
                return None

            b0, b1 = first2[0], first2[1]
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]

            mask_key = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

            if opcode == self.OP_CLOSE:
                self._closed = True
                return None
            if opcode == self.OP_PING:
                with self.write_lock:
                    try:
                        self.sock.sendall(self._build_frame(self.OP_PONG, payload))
                    except OSError:
                        self._closed = True
                        return None
                continue
            if opcode == self.OP_PONG:
                continue
            if opcode == self.OP_TEXT:
                try:
                    return payload.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            # ignore binary/continuation frames -- protocol.py is text-only


def _ws_accept_key(client_key: str) -> str:
    sha1 = hashlib.sha1((client_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(sha1).decode("ascii")


# =====================================================================
# Client bookkeeping
# =====================================================================
class ClientState:
    def __init__(self, conn: WSConnection, client_id: str):
        self.conn = conn
        self.id = client_id
        self.role = "viewer"
        self.name = ""
        self.subscriptions: set[str] = set()


class PosmServer:
    def __init__(self, backend, host="0.0.0.0", port=8000, single_control=True,
                 sim_mode=False, server_version="0.1.0"):
        self.backend = backend
        self.host = host
        self.port = port
        self.single_control = single_control
        self.sim_mode = sim_mode
        self.server_version = server_version

        self.clients: dict[str, ClientState] = {}
        self.clients_lock = threading.RLock()
        self.control_client_id: "str | None" = None

        self.event_log: deque = deque(maxlen=MAX_EVENT_LOG)

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if hasattr(backend, "on_event"):
            backend.on_event = self._on_backend_event

        self._status_thread = threading.Thread(target=self._status_loop, daemon=True, name="status-push")
        self.scope_streamer = ScopeStreamer(
            backend, on_frame=self._broadcast_scope_frame,
            should_run=self._any_scope_subscriber,
        )

    # -----------------------------------------------------------------
    # lifecycle
    # -----------------------------------------------------------------
    def start(self):
        self._status_thread.start()
        self.scope_streamer.start()

    # -----------------------------------------------------------------
    # register helpers
    # -----------------------------------------------------------------
    def _read_param(self, name: str):
        p = P.get(name)
        if p.kind.name == "PULSE":
            return None
        raw = self.backend.read(p.addr)
        return p.raw_to_value(raw)

    def _write_param(self, name: str, value):
        p = P.get(name)
        if not p.writable:
            raise ValueError(f"'{name}' is read-only")
        current_raw = self.backend.read(p.addr) if p.kind.name in ("BIT", "FIELD") else 0
        raw = p.value_to_raw(value, current_raw=current_raw)
        self.backend.write(p.addr, raw)

    def build_snapshot(self) -> dict:
        # Group parameters by address so a shared register (e.g. every
        # CONTROL bit) is only read off the bus once per snapshot.
        by_addr: dict[int, list] = {}
        for p in P.PARAMETERS.values():
            if p.kind.name == "PULSE":
                continue
            by_addr.setdefault(p.addr, []).append(p)

        values = {}
        for addr, params in by_addr.items():
            raw = self.backend.read(addr)
            for p in params:
                values[p.name] = p.raw_to_value(raw)
        return values

    # -----------------------------------------------------------------
    # per-connection message handling
    # -----------------------------------------------------------------
    def handle_connection(self, conn: WSConnection):
        client_id = uuid.uuid4().hex[:8]
        state = ClientState(conn, client_id)
        with self.clients_lock:
            self.clients[client_id] = state
        try:
            while True:
                text = conn.recv_text()
                if text is None:
                    break
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    conn.send_json({"type": "error", "detail": "invalid JSON"})
                    continue
                self._dispatch(state, msg)
        finally:
            with self.clients_lock:
                self.clients.pop(client_id, None)
                if self.control_client_id == client_id:
                    self.control_client_id = None
            conn.close()

    def _dispatch(self, state: ClientState, msg: dict):
        mtype = msg.get("type")
        try:
            handler = getattr(self, f"_handle_{mtype}", None)
            if handler is None:
                state.conn.send_json({"type": "error", "detail": f"unknown type '{mtype}'"})
                return
            handler(state, msg)
        except Exception as exc:
            state.conn.send_json({"type": "error", "detail": str(exc), "in_reply_to": mtype})

    def _handle_hello(self, state: ClientState, msg: dict):
        requested = msg.get("role", "viewer")
        state.name = msg.get("name", "")
        with self.clients_lock:
            if requested == "control":
                if self.single_control and self.control_client_id not in (None, state.id):
                    state.role = "viewer"  # someone else already has the wheel
                else:
                    state.role = "control"
                    self.control_client_id = state.id
            else:
                state.role = "viewer"
        state.conn.send_json({
            "type": "hello_ack", "role": state.role, "sim_mode": self.sim_mode,
            "client_id": state.id, "server_version": self.server_version,
        })

    def _handle_get(self, state: ClientState, msg: dict):
        name = msg["param"]
        state.conn.send_json({"type": "value", "param": name, "value": self._read_param(name)})

    def _handle_get_all(self, state: ClientState, msg: dict):
        state.conn.send_json({"type": "snapshot", "values": self.build_snapshot()})

    def _handle_set(self, state: ClientState, msg: dict):
        if state.role != "control":
            state.conn.send_json({"type": "error", "detail": "read-only connection (viewer role)",
                                   "in_reply_to": "set"})
            return
        name = msg["param"]
        self._write_param(name, msg["value"])
        state.conn.send_json({"type": "value", "param": name, "value": self._read_param(name)})

    def _handle_subscribe(self, state: ClientState, msg: dict):
        stream = msg.get("stream")
        if stream in PROTO.STREAMS:
            state.subscriptions.add(stream)

    def _handle_unsubscribe(self, state: ClientState, msg: dict):
        state.subscriptions.discard(msg.get("stream"))

    def _handle_clear_faults(self, state: ClientState, msg: dict):
        if state.role != "control":
            state.conn.send_json({"type": "error", "detail": "read-only connection (viewer role)"})
            return
        self.backend.write(P.get("clear_all_faults").addr, 0xFFF)

    def _handle_trace_capture(self, state: ClientState, msg: dict):
        if state.role != "control":
            state.conn.send_json({"type": "error", "detail": "read-only connection (viewer role)",
                                   "in_reply_to": "trace_capture"})
            return
        frame = self.scope_streamer.capture_once()
        if frame is not None:
            state.conn.send_json(frame)
        else:
            state.conn.send_json({"type": "error", "detail": "trace capture timed out",
                                   "in_reply_to": "trace_capture"})

    def _handle_save_config(self, state: ClientState, msg: dict):
        if state.role != "control":
            state.conn.send_json({"type": "error", "detail": "read-only connection (viewer role)"})
            return
        name = msg["name"]
        snapshot = {n: v for n, v in self.build_snapshot().items() if P.get(n).writable}
        path = self._config_path(name)
        path.write_text(json.dumps(snapshot, indent=2))
        state.conn.send_json({"type": "config_loaded", "name": name})  # "saved" is fine to reuse the same ack shape

    def _handle_load_config(self, state: ClientState, msg: dict):
        if state.role != "control":
            state.conn.send_json({"type": "error", "detail": "read-only connection (viewer role)"})
            return
        name = msg["name"]
        saved = self._read_config(name)
        for pname, value in saved.items():
            if pname in P.PARAMETERS and P.PARAMETERS[pname].writable:
                self._write_param(pname, value)
        state.conn.send_json({"type": "config_loaded", "name": name})

    def _handle_diff_config(self, state: ClientState, msg: dict):
        name = msg["name"]
        saved = self._read_config(name)
        current = self.build_snapshot()
        changes = {}
        for pname, saved_val in saved.items():
            cur_val = current.get(pname)
            if cur_val != saved_val:
                changes[pname] = {"current": cur_val, "saved": saved_val}
        state.conn.send_json({"type": "config_diff", "name": name, "changes": changes})

    def _handle_list_configs(self, state: ClientState, msg: dict):
        names = sorted(p.stem for p in CONFIG_DIR.glob("*.json"))
        state.conn.send_json({"type": "config_list", "names": names})

    def _config_path(self, name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError("invalid config name")
        return CONFIG_DIR / f"{safe}.json"

    def _read_config(self, name: str) -> dict:
        path = self._config_path(name)
        if not path.exists():
            raise FileNotFoundError(f"no saved config named '{name}'")
        return json.loads(path.read_text())

    # -----------------------------------------------------------------
    # background push loops
    # -----------------------------------------------------------------
    def _status_loop(self):
        while True:
            time.sleep(PROTO.STATUS_PUSH_INTERVAL_S)
            with self.clients_lock:
                subscribers = [c for c in self.clients.values() if "status" in c.subscriptions]
            if not subscribers:
                continue
            snapshot = self.build_snapshot()
            msg = {"type": "snapshot", "values": snapshot}
            for c in subscribers:
                c.conn.send_json(msg)

    def _any_scope_subscriber(self) -> bool:
        with self.clients_lock:
            return any("scope" in c.subscriptions for c in self.clients.values())

    def _broadcast_scope_frame(self, frame: dict):
        with self.clients_lock:
            subscribers = [c for c in self.clients.values() if "scope" in c.subscriptions]
        for c in subscribers:
            c.conn.send_json(frame)

    def _on_backend_event(self, kind: str, detail: str):
        entry = {"type": "event", "ts": time.time(), "kind": kind, "detail": detail}
        self.event_log.append(entry)
        with self.clients_lock:
            subscribers = [c for c in self.clients.values() if "events" in c.subscriptions]
        for c in subscribers:
            c.conn.send_json(entry)


# =====================================================================
# HTTP glue: static file serving + WebSocket upgrade on /ws
# =====================================================================
def make_handler(posm: PosmServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "posm_server/0.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self):
            if self.path == "/ws" or self.path.startswith("/ws?"):
                self._handle_ws_upgrade()
                return
            self._serve_static()

        def _handle_ws_upgrade(self):
            key = self.headers.get("Sec-WebSocket-Key")
            upgrade = (self.headers.get("Upgrade") or "").lower()
            if not key or upgrade != "websocket":
                self.send_error(400, "expected a WebSocket upgrade request")
                return
            accept = _ws_accept_key(key)
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            conn = WSConnection(self.connection)
            posm.handle_connection(conn)  # blocks for the connection's lifetime

        def _serve_static(self):
            rel = self.path.split("?", 1)[0].lstrip("/")
            if rel == "":
                rel = "index.html"
            path = (CLIENT_DIR / rel).resolve()
            if CLIENT_DIR.resolve() not in path.parents and path != CLIENT_DIR.resolve():
                self.send_error(403)
                return
            if not path.is_file():
                self.send_error(404)
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(path.suffix, "application/octet-stream")
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--mock", action="store_true",
                     help="use mock_backend.py (Amaranth simulation) instead of real hardware")
    ap.add_argument("--base", help="hex base address of the POSM sys-bus slot, e.g. 0x40600000 "
                                    "(required unless --mock)")
    ap.add_argument("--multi-control", action="store_true",
                     help="allow more than one 'control' connection at once (off by default -- "
                          "docs/10_gui_implementation_plan.md section 4e)")
    args = ap.parse_args()

    if not args.mock and not args.base:
        ap.error("--base is required for real hardware (or pass --mock to run against the simulator)")

    if args.mock:
        from gui.server.mock_backend import MockBackend
        backend = MockBackend()
        backend.start()
        sim_mode = True
        print("Running in SIMULATED mode (mock_backend.py, no hardware) -- see the SIMULATED banner in the GUI.")
    else:
        from gui.server.hw_backend import HardwareBackend
        base_addr = int(args.base, 0)
        backend = HardwareBackend(base_addr)
        sim_mode = False
        print(f"Running against real hardware at base 0x{base_addr:08x}.")

    posm = PosmServer(backend, host=args.host, port=args.port,
                       single_control=not args.multi_control, sim_mode=sim_mode)
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(posm))
    except OSError as exc:
        if exc.errno != 98 or args.port != 8000:
            raise
        # A previous local GUI session commonly still owns the default port.
        # Keep startup convenient while preserving an explicitly requested
        # non-default port as a hard error.
        for candidate in range(8001, 8100):
            try:
                server = ThreadingHTTPServer((args.host, candidate), make_handler(posm))
                args.port = candidate
                print(f"Port 8000 is busy; using port {candidate} instead.")
                break
            except OSError as candidate_error:
                if candidate_error.errno != 98:
                    raise
        else:
            raise

    posm.port = args.port
    posm.start()
    display_host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    print(f"Serving POSM GUI on http://{display_host}:{args.port}/  (WebSocket at /ws)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(backend, "stop"):
            backend.stop()


if __name__ == "__main__":
    main()
