"""
gui/server/scope_streamer.py

Reads the trace_capture buffer (rtl/control/trace_capture.py, see
gui/server/parameters.py group "scope") on a timer, and hands the
finished waveform to a callback so posm_server.py can push it out to
any client subscribed to the "scope" stream (protocol.py). This is
what makes the live plot in the browser actually update
(docs/10_gui_implementation_plan.md, section 2).

Deliberately backend-agnostic: it only calls backend.read()/write()
with addresses from parameters.py, so it drives mock_backend.py and
hw_backend.py identically -- same as every other part of the server.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import parameters as P

DEFAULT_LENGTH = 128
DEFAULT_DECIM = 1
CAPTURE_POLL_INTERVAL_S = 0.01
CAPTURE_TIMEOUT_S = 3.0


class ScopeStreamer(threading.Thread):
    def __init__(self, backend, on_frame: Callable[[dict], None],
                 should_run: Callable[[], bool], interval_s: float = 1.5):
        super().__init__(name="scope-streamer", daemon=True)
        self.backend = backend
        self.on_frame = on_frame
        self.should_run = should_run
        self.interval_s = interval_s
        self._stop = threading.Event()
        self.length = DEFAULT_LENGTH
        self.decim = DEFAULT_DECIM

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            if self.should_run():
                try:
                    frame = self.capture_once()
                    if frame is not None:
                        self.on_frame(frame)
                except Exception as exc:  # keep the streamer alive across transient errors
                    self.on_frame({"type": "scope_frame", "error": str(exc), "x": [], "y": []})
            self._stop.wait(self.interval_s)

    # -----------------------------------------------------------------
    def _write(self, name, value, current_raw=None):
        p = P.get(name)
        if current_raw is None and p.kind.name in ("BIT", "FIELD"):
            current_raw = self.backend.read(p.addr)
        raw = p.value_to_raw(value, current_raw=current_raw or 0)
        self.backend.write(p.addr, raw)

    def _read(self, name):
        p = P.get(name)
        raw = self.backend.read(p.addr)
        return p.raw_to_value(raw)

    def capture_once(self) -> Optional[dict]:
        channel_sel = self._read("trace_config_channel_sel")

        self._write("trace_config_enable", True)
        self._write("trace_length", self.length)
        self._write("trace_decim", self.decim)
        self.backend.write(P.get("trace_start").addr, 1)  # pulse, any value arms+starts

        deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline:
            if self._read("trace_status_ready"):
                ready = True
                break
            if self._read("trace_status_overflow"):
                break
            time.sleep(CAPTURE_POLL_INTERVAL_S)

        if not ready:
            return None

        write_ptr = int(self._read("trace_write_ptr"))
        count = max(0, min(self.length, write_ptr if write_ptr > 0 else self.length))
        if count == 0:
            return None

        xs, ys = [], []
        addr_p = P.get("trace_read_addr")
        x_p = P.get("trace_read_data_x")
        y_p = P.get("trace_read_data_y")
        for i in range(count):
            self.backend.write(addr_p.addr, i)
            xs.append(x_p.raw_to_value(self.backend.read(x_p.addr)))
            ys.append(y_p.raw_to_value(self.backend.read(y_p.addr)))

        return {
            "type": "scope_frame",
            "x": xs,
            "y": ys,
            "channel": "ch1" if channel_sel else "error",
        }
