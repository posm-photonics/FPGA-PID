"""
build/generate_verilog.py

Elaborates RedPitayaLockCore (the Amaranth board-integration wrapper in
top/RedPitaya_Lock_Core.py) down to plain Verilog-2001, so it can be added
as a source file to a Vivado project alongside Red Pitaya's own RTL.

This is the script scripts/build_posm_red_pitaya.tcl's header comment
refers to when it says "python3 build/generate_verilog.py" -- previously
that file only contained documentation text and did not actually do this.

Requires: amaranth, and either a system `yosys` >= 0.40 on PATH or the
`amaranth-yosys` PyPI package installed as a fallback backend.

Usage:
    python3 build/generate_verilog.py

Output:
    build/out/red_pitaya_lock_core.v
"""

import os
import sys

# Allow running as `python3 build/generate_verilog.py` from the repo root
# without needing the package installed.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from amaranth.back import verilog

from top.RedPitaya_Lock_Core import RedPitayaLockCore


def main():
    core = RedPitayaLockCore()

    # Top-level ports for the generated Verilog module. This must match
    # the port list red_pitaya_top.v instantiates against (see the
    # instantiation template in scripts/build_posm_red_pitaya.tcl).
    ports = [
        core.clk,
        core.rst,
        core.i_adc_dat_a,
        core.i_adc_dat_b,
        core.o_dac_dat_a,
        core.o_dac_dat_b,
        core.i_external_interlock,
        core.i_feature_selected,
        core.o_lock_state,
        core.o_lock_fault,
        core.o_trace_ready,
        core.o_heartbeat,
        core.sys_addr,
        core.sys_wdata,
        core.sys_sel,
        core.sys_wen,
        core.sys_ren,
        core.sys_rdata,
        core.sys_err,
        core.sys_ack,
    ]

    verilog_text = verilog.convert(
        core,
        name="red_pitaya_lock_core",
        ports=ports,
    )

    out_dir = os.path.join(REPO_ROOT, "build", "out")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "red_pitaya_lock_core.v")

    with open(out_path, "w") as f:
        f.write(verilog_text)

    print(f"Wrote {out_path} ({len(verilog_text)} chars)")


if __name__ == "__main__":
    main()
