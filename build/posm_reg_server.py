#!/usr/bin/env python3
"""
tools/posm_reg_server.py

Runs ON the Red Pitaya (Python 3, stdlib only -- no pip install needed on
the board). Memory-maps the POSM lock core's register window via /dev/mem
and serves it as JSON over HTTP, so the sanity-check dashboard
(tools/posm_sanity_dashboard.html) running in your laptop's browser can
poll it and let you flip a few safe control bits before wiring the loop
into real optics/electronics.

This talks to the GLOBAL control/status block only (register_bank.py,
offsets 0x000-0x01C from rtl/bus/register_defs.py):

    0x000 VERSION       R
    0x004 CONTROL       R/W
    0x008 STATUS        R
    0x00C MODE          R/W
    0x010 FAULT_STATUS  R   (sticky)
    0x014 FAULT_ENABLE  R/W
    0x018 FAULT_CLEAR   W   (write-one-to-clear)
    0x01C DEBUG_SELECT  R/W

BASE ADDRESS: you must pass --base matching wherever you actually wired
red_pitaya_lock_core onto the sys bus in step 4 of the build (the sys-bus
slot's memory window). This script does NOT know that address -- there is
no safe default to guess, and guessing wrong means reading/writing memory
that belongs to a different peripheral. Check your red_pitaya_top.v edit
and Red Pitaya's own memory map documentation for the correct value.

USAGE (on the Red Pitaya, as root -- /dev/mem requires it):
    python3 posm_reg_server.py --base 0x40600000 --port 5001

Then on your laptop, open tools/posm_sanity_dashboard.html and point it
at http://<red-pitaya-ip>:5001

SAFETY: the /write endpoint only accepts a fixed allowlist of operations
(see ALLOWED_WRITES below) -- test-pattern enables, fault clear, and
outputs-enable -- not arbitrary register pokes. Even so: only enable
DAC_TEST_PATTERN_EN with the DAC output disconnected from any real
actuator (piezo, laser current driver, etc.) or feeding a scope/dummy
load. Only enable OUTPUTS_ENABLE once you've confirmed STATUS/FAULT
readback looks sane.
"""

import argparse
import json
import mmap
import os
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- register map (must match rtl/bus/register_defs.py) --------------------
ADDR_VERSION      = 0x000
ADDR_CONTROL      = 0x004
ADDR_STATUS       = 0x008
ADDR_MODE         = 0x00C
ADDR_FAULT_STATUS = 0x010
ADDR_FAULT_ENABLE = 0x014
ADDR_FAULT_CLEAR  = 0x018
ADDR_DEBUG_SELECT = 0x01C

MAP_SIZE = 0x1000  # one 4K page is plenty for the global block

CTRL_GLOBAL_ENABLE        = 0
CTRL_SOFT_RESET           = 1
CTRL_OUTPUTS_ENABLE       = 2
CTRL_LOCK_ENABLE_REQUEST  = 3
CTRL_HOLD_REQUEST         = 4
CTRL_FAULT_CLEAR_REQUEST  = 5
CTRL_INTEGRATOR_RESET     = 6
CTRL_INTEGRATOR_LOAD      = 7
CTRL_TRACE_CAPTURE_ENABLE = 8
CTRL_AUTOLOCK_ENABLE      = 9
CTRL_SLOW_RECENTER_ENABLE = 10
CTRL_ADC_TEST_PATTERN_EN  = 11
CTRL_DAC_TEST_PATTERN_EN  = 12

EXPECTED_VERSION = 0x0003_0000  # major=3, minor=0 (see register_bank.py)

# Allowlisted write operations the dashboard can trigger. Each is a
# (register, bitmask, description) -- the dashboard sends {"op": name,
# "value": 0 or 1}, never a raw address+value, so a compromised/buggy
# browser tab can't poke arbitrary registers.
ALLOWED_WRITES = {
    "outputs_enable":       (ADDR_CONTROL, CTRL_OUTPUTS_ENABLE),
    "adc_test_pattern_en":  (ADDR_CONTROL, CTRL_ADC_TEST_PATTERN_EN),
    "dac_test_pattern_en":  (ADDR_CONTROL, CTRL_DAC_TEST_PATTERN_EN),
    "global_enable":        (ADDR_CONTROL, CTRL_GLOBAL_ENABLE),
}


class RegMap:
    def __init__(self, base_addr, map_size=MAP_SIZE):
        self.base_addr = base_addr
        self.map_size = map_size
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self.mem = mmap.mmap(
                fd, map_size,
                mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                offset=base_addr,
            )
        finally:
            os.close(fd)  # mmap keeps its own reference; fd not needed after

    def read32(self, offset):
        return struct.unpack_from("<I", self.mem, offset)[0]

    def write32(self, offset, value):
        struct.pack_into("<I", self.mem, offset, value & 0xFFFFFFFF)

    def snapshot(self):
        version      = self.read32(ADDR_VERSION)
        control      = self.read32(ADDR_CONTROL)
        status       = self.read32(ADDR_STATUS)
        mode         = self.read32(ADDR_MODE)
        fault_status = self.read32(ADDR_FAULT_STATUS)
        fault_enable = self.read32(ADDR_FAULT_ENABLE)

        return {
            "version": {
                "raw": version,
                "expected": EXPECTED_VERSION,
                "match": version == EXPECTED_VERSION,
            },
            "control": {
                "raw": control,
                "global_enable":        bool(control & (1 << CTRL_GLOBAL_ENABLE)),
                "soft_reset":           bool(control & (1 << CTRL_SOFT_RESET)),
                "outputs_enable":       bool(control & (1 << CTRL_OUTPUTS_ENABLE)),
                "lock_enable_request":  bool(control & (1 << CTRL_LOCK_ENABLE_REQUEST)),
                "hold_request":         bool(control & (1 << CTRL_HOLD_REQUEST)),
                "integrator_reset":     bool(control & (1 << CTRL_INTEGRATOR_RESET)),
                "trace_capture_enable": bool(control & (1 << CTRL_TRACE_CAPTURE_ENABLE)),
                "autolock_enable":      bool(control & (1 << CTRL_AUTOLOCK_ENABLE)),
                "slow_recenter_enable": bool(control & (1 << CTRL_SLOW_RECENTER_ENABLE)),
                "adc_test_pattern_en":  bool(control & (1 << CTRL_ADC_TEST_PATTERN_EN)),
                "dac_test_pattern_en":  bool(control & (1 << CTRL_DAC_TEST_PATTERN_EN)),
            },
            "status": {
                "raw": status,
                "state":        status & 0xF,
                "locked":       bool(status & (1 << 4)),
                "scanning":     bool(status & (1 << 5)),
                "saturation":   bool(status & (1 << 6)),
                "trace_ready":  bool(status & (1 << 7)),
                "fault_active": bool(status & (1 << 8)),
            },
            "mode": mode,
            "fault_status": {
                "raw": fault_status,
                "bits": [bool(fault_status & (1 << i)) for i in range(12)],
            },
            "fault_enable": fault_enable,
        }

    def set_bit(self, addr, bit, value):
        current = self.read32(addr)
        if value:
            new = current | (1 << bit)
        else:
            new = current & ~(1 << bit)
        self.write32(addr, new)
        return new

    def clear_all_faults(self):
        # FAULT_CLEAR is write-one-to-clear, masked by FAULT_ENABLE in HW.
        self.write32(ADDR_FAULT_CLEAR, 0xFFFFFFFF)


def make_handler(regmap: RegMap):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/regs"):
                try:
                    self._json(200, regmap.snapshot())
                except Exception as e:
                    self._json(500, {"error": str(e)})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.startswith("/write"):
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    op = body.get("op")
                    value = bool(body.get("value"))

                    if op == "clear_faults":
                        regmap.clear_all_faults()
                        self._json(200, {"ok": True, "op": op})
                        return

                    if op not in ALLOWED_WRITES:
                        self._json(400, {"error": f"op '{op}' not in allowlist"})
                        return

                    addr, bit = ALLOWED_WRITES[op]
                    new_val = regmap.set_bit(addr, bit, value)
                    self._json(200, {"ok": True, "op": op, "value": value, "raw": new_val})
                except Exception as e:
                    self._json(500, {"error": str(e)})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True,
                    help="Base address of the POSM sys-bus slot, e.g. 0x40600000 "
                         "(hex, must match your red_pitaya_top.v wiring)")
    p.add_argument("--port", type=int, default=5001)
    args = p.parse_args()

    base_addr = int(args.base, 0)

    if os.geteuid() != 0:
        print("ERROR: this must run as root (needs /dev/mem access). Try sudo.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Opening /dev/mem at base 0x{base_addr:08x} ...")
    regmap = RegMap(base_addr)

    try:
        snap = regmap.snapshot()
    except Exception as e:
        print(f"ERROR: couldn't read registers at 0x{base_addr:08x}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"VERSION reads 0x{snap['version']['raw']:08x} "
          f"(expected 0x{EXPECTED_VERSION:08x}) -> "
          f"{'MATCH' if snap['version']['match'] else 'MISMATCH -- check your base address / wiring'}")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(regmap))
    print(f"Serving on http://0.0.0.0:{args.port} (GET /regs, POST /write)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
