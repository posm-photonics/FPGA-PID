"""
gui/server/hw_backend.py

Real register backend: runs ON the Red Pitaya, memory-maps the POSM
lock core's register window via /dev/mem, same technique
build/posm_reg_server.py already uses. That script only ever touched
the global control/status block (0x000-0x01C) plus a fixed allowlist
of safe writes -- it was a pre-install sanity check, not a full
control path. This backend generalizes the same RegMap idea to the
full register space described by gui/server/parameters.py, so
posm_server.py can use it as a drop-in replacement for mock_backend.py
via a single config flag.

SAFETY NOTE: unlike posm_reg_server.py's fixed ALLOWED_WRITES list,
this backend will write to any address a parameter maps to. Access
control (control vs. viewer role, see protocol.py) is enforced up in
posm_server.py, not here -- this module trusts whatever it's asked to
write. Do not expose it directly to the network without posm_server.py
in front of it.
"""

from __future__ import annotations

import mmap
import os
import struct
import threading

MAP_SIZE = 0x1000  # one 4K page covers the whole 12-bit register space


class HardwareBackend:
    """Register backend backed by /dev/mem on the Red Pitaya itself."""

    def __init__(self, base_addr: int, map_size: int = MAP_SIZE):
        if os.geteuid() != 0:
            raise PermissionError(
                "hw_backend needs root (it mmaps /dev/mem) -- run posm_server.py with sudo")
        self.base_addr = base_addr
        self.map_size = map_size
        self._lock = threading.Lock()

        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self.mem = mmap.mmap(
                fd, map_size, mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE, offset=base_addr,
            )
        finally:
            os.close(fd)

        # Not simulated: no on_event hook or sim_time_s. posm_server.py
        # checks hasattr(backend, "on_event") before using it, and
        # falls back to polling the fault_status/lock_state parameters
        # on the "status" stream tick to synthesize events instead.
        self.on_event = None

    def read(self, addr: int) -> int:
        with self._lock:
            return struct.unpack_from("<I", self.mem, addr)[0]

    def write(self, addr: int, value: int) -> None:
        with self._lock:
            struct.pack_into("<I", self.mem, addr, value & 0xFFFFFFFF)

    def stop(self):
        try:
            self.mem.close()
        except Exception:
            pass
