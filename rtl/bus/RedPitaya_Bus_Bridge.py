"""
redpitaya_bus_bridge.py

Adapter between Red Pitaya's native peripheral register bus and this
project's internal register bus (see register_bank.py docstring for the
adr/dat_w/dat_r/we/stb convention this targets).

------------------------------------------------------------------------
Why this exists, and why it is NOT an AXI4-Lite slave
------------------------------------------------------------------------
Red Pitaya's official top level (red_pitaya_top.v) does not expose raw
AXI4-Lite to individual peripherals. red_pitaya_ps.v hides the Zynq PS7
AXI GP0 port behind a simple, single-cycle synchronous bus:

    sys_addr  [31:0]  byte address (full 32-bit; only the low bits
                       within a peripheral's decoded slot matter here)
    sys_wdata [31:0]  write data
    sys_sel   [3:0]   write byte-enables (per-byte strobe)
    sys_wen           write enable, pulses for exactly one cycle
    sys_ren           read enable, pulses for exactly one cycle
    sys_rdata [31:0]  read data (peripheral drives this back)
    sys_err           peripheral signals a bus error
    sys_ack           peripheral signals the transaction is complete

red_pitaya_top.v decodes sys_addr[22:20] into 8 slots (sys_cs), ANDs
sys_wen/sys_ren with the selected slot, and ORs the 8 peripherals'
sys_rdata/sys_err/sys_ack together. This bridge is meant to sit in one
of those slots (an existing reference laser-lock project on Red Pitaya,
marceluda/rp_lock-in_pid, uses slot 6 for exactly this kind of custom
module -- worth using as a wiring reference).

Because both sides are already simple, non-pipelined, single-cycle
buses, this bridge is a thin protocol translation, not a full bus
master/slave state machine:

    sys_wen  -> stb=1, we=1, one cycle
    sys_ren  -> stb=1, we=0, one cycle  (our bus's dat_r is purely
                                          combinational off `adr`, so
                                          the read data is already
                                          valid the same cycle -- see
                                          register_bank.py, which OR's
                                          multiple submodules' dat_r
                                          together on that assumption)
    sys_ack  -> always 1 the cycle after wen/ren, since nothing on our
                internal bus ever introduces wait states
    sys_err  -> tied 0; this project's bus has no error signaling

This module does NOT implement byte-strobe (sys_sel) partial-word
writes -- like the rest of this project's register bus, every write is
a full 32-bit word (see register_bank.py, which has the same
limitation). sys_sel is accepted on the port but intentionally unused;
if partial-word writes are ever needed, extend dat_w masking here, not
in the individual register modules.
"""

from amaranth import Module, Signal, Elaboratable, Mux


class RedPitayaBusBridge(Elaboratable):
    def __init__(self, addr_width=12, data_width=32):
        self.addr_width = addr_width
        self.data_width = data_width

        # --- Red Pitaya side (sys_* bus, one address-decoded slot) ---
        self.sys_addr  = Signal(32)
        self.sys_wdata = Signal(data_width)
        self.sys_sel   = Signal(data_width // 8)
        self.sys_wen   = Signal()
        self.sys_ren   = Signal()
        self.sys_rdata = Signal(data_width)
        self.sys_err   = Signal()
        self.sys_ack   = Signal()

        # --- Project-internal side (register_bank.py convention) ---
        self.adr   = Signal(addr_width)
        self.dat_w = Signal(data_width)
        self.dat_r = Signal(data_width)
        self.we    = Signal()
        self.stb   = Signal()

    def elaborate(self, platform):
        m = Module()

        # sys_addr is the full byte address the PS presents; the upper
        # bits (which pick this peripheral's slot) are already consumed
        # by the address decoder in red_pitaya_top.v before sys_wen/
        # sys_ren reach this bridge, so only the low addr_width bits are
        # meaningful here.
        m.d.comb += self.adr.eq(self.sys_addr[:self.addr_width])

        m.d.comb += [
            self.dat_w.eq(self.sys_wdata),
            self.we.eq(self.sys_wen),
            self.stb.eq(self.sys_wen | self.sys_ren),
            self.sys_rdata.eq(self.dat_r),
            self.sys_err.eq(0),
        ]

        # Single-cycle ack the clock after either strobe -- matches
        # every register-bus submodule in this project, which never
        # stalls a read or write.
        m.d.sync += self.sys_ack.eq(self.sys_wen | self.sys_ren)

        return m