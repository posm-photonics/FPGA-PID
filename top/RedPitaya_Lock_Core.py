"""
red_pitaya_lock_core.py

Board-integration wrapper: LockCoreTop + RedPitayaBusBridge, exposing a
port list meant to be instantiated as ONE leaf module inside a Red
Pitaya red_pitaya_top.v/.sv (either the official RedPitaya-FPGA repo,
or a fork like marceluda/rp_lock-in_pid which already wires a custom
"lock" module into bus slot 6 -- use that instantiation as the wiring
reference for this module).

Target hardware: Red Pitaya STEMlab 125-14 (Zynq XC7Z010, dual 14-bit
125 MSPS ADC + dual 14-bit DAC). Chosen over the 125-10 because it's
the actively-supported, best-documented board with by far the largest
body of open-source reference designs (including prior laser-lock
work), and 14 bits gives meaningfully finer DAC resolution for the
error-signal range than the 10-bit variant -- relevant given the
project's own fixed-point-precision concerns (see docs/03).

------------------------------------------------------------------------
What this module deliberately does NOT do
------------------------------------------------------------------------
It does not touch clock generation, differential IO buffering, PLL
setup, or the ADC/DAC IOB/ODDR primitives. Those live in the existing,
proven Red Pitaya firmware (red_pitaya_pll.v, IBUFDS/ODDR instances in
red_pitaya_top.v) and should be reused as-is, not reimplemented in
Amaranth -- that infrastructure is Xilinx-primitive-heavy and specific
to the board's clock tree; hand-porting it is where new, hard-to-debug
bugs would get introduced for no benefit. This module assumes it is
handed an already-clean adc_clk and already-deserialized 14-bit sample
pair, and produces 14-bit DAC codes ready for the existing ODDR output
stage.

------------------------------------------------------------------------
ADC/DAC encoding -- grounded in Red Pitaya's actual firmware
------------------------------------------------------------------------
Red Pitaya's own red_pitaya_top.v does NOT treat the raw ADC words as
plain offset binary. The comment there says "ADC data is translated
from unsigned neg-slope into two's complement" via this exact bit
trick (confirmed from the official source):

    assign adc_a = {adc_dat_a[13], ~adc_dat_a[12:0]};

i.e. keep the sign (MSB) bit, invert the 13 magnitude bits. This is a
property of the specific ADC part on the board, and is neither of the
two modes ADCFormatter.py already supports (offset_binary = raw -
2^(N-1), or two's-complement passthrough). So that conversion is done
here, in the board layer, BEFORE handing samples to ADCFrontendTop --
which is then told i_format_mode=1 (two's-complement passthrough),
because after the bit-trick the value already IS clean two's
complement.

The DAC path applies the same trick in reverse on the way out
(red_pitaya_top.v: `dac_dat_a <= {dac_a[13], ~dac_a[12:0]}`).

If you retarget this to a different Red Pitaya variant or a TI-frontend
Gen2 board, RECHECK this encoding against that board's actual firmware
-- do not assume it carries over unchanged.

------------------------------------------------------------------------
Width handling
------------------------------------------------------------------------
LockCoreTop's ADC inputs are 16-bit; the ADC here is 14-bit. The 14-bit
two's-complement sample is sign-extended into the low 14 bits of a
16-bit signal (Amaranth auto-sign-extends signed->signed assignment of
different widths). This does NOT rescale full-scale voltage -- it
represents the same signal at 2 fewer bits of resolution than a true
16-bit converter, which is what a 14-bit ADC actually gives you.

LockCoreTop's DAC outputs are 16-bit codes from DACFastFormatter,
configured here for i_mode=0 (two's-complement, no offset-binary
shift) so the output is clean signed data. The top 14 bits are taken
(dropping the 2 least-significant bits) to match the DAC's native
width, then the same sign/invert trick is applied going out.
"""

from amaranth import Module, Signal, Elaboratable, Cat, Const, signed

from top.lock_core_top import LockCoreTop
from rtl.bus.redpitaya_bus_bridge import RedPitayaBusBridge


class RedPitayaLockCore(Elaboratable):
    def __init__(self):
        # Clock/reset for the whole design. Drive these from the board's
        # existing adc_clk / adc_rstn (inverted to active-high here) --
        # see red_pitaya_top.v's `adc_clk`/`adc_rstn` nets.
        self.clk = Signal()
        self.rst = Signal()

        # Raw 14-bit ADC words, already deserialized by the existing
        # board IO stage (red_pitaya_top.v's adc_dat_a/adc_dat_b
        # registers), still in the board's native "neg-slope" encoding
        # -- NOT yet converted to two's complement. That conversion
        # happens inside this module.
        self.i_adc_dat_a = Signal(14)
        self.i_adc_dat_b = Signal(14)

        # 14-bit DAC words in the board's native encoding, ready to feed
        # directly into the existing ODDR output stage
        # (dac_dat_a/dac_dat_b in red_pitaya_top.v).
        self.o_dac_dat_a = Signal(14)
        self.o_dac_dat_b = Signal(14)

        # External safety input, wired from wherever the interlock
        # signal lands on this board (e.g. an exp_p_io pin).
        self.i_external_interlock = Signal()
        self.i_feature_selected = Signal()

        # Status outputs -- wire these to led_o or exp_p_io for
        # bring-up visibility before the register bus is fully tested.
        self.o_lock_state = Signal(4)
        self.o_lock_fault = Signal()
        self.o_trace_ready = Signal()

        # Red Pitaya's native peripheral bus for this module's slot.
        # Wire these directly to the sys_* signals for whichever slot
        # this module occupies in red_pitaya_top.v (bus slot 6 is free
        # in the reference design this was checked against).
        self.sys_addr  = Signal(32)
        self.sys_wdata = Signal(32)
        self.sys_sel   = Signal(4)
        self.sys_wen   = Signal()
        self.sys_ren   = Signal()
        self.sys_rdata = Signal(32)
        self.sys_err   = Signal()
        self.sys_ack   = Signal()

    def elaborate(self, platform):
        m = Module()

        m.submodules.core   = core   = LockCoreTop()
        m.submodules.bridge = bridge = RedPitayaBusBridge()

        # ---- clock / reset ----
        m.d.comb += [
            core.clk.eq(self.clk),
            core.rst.eq(self.rst),
        ]

        # ---- ADC: board neg-slope encoding -> two's complement ----
        # {sign, ~magnitude}, per red_pitaya_top.v.
        adc_a_signed = Signal(signed(14))
        adc_b_signed = Signal(signed(14))
        m.d.comb += [
            adc_a_signed.eq(Cat(~self.i_adc_dat_a[:13], self.i_adc_dat_a[13]).as_signed()),
            adc_b_signed.eq(Cat(~self.i_adc_dat_b[:13], self.i_adc_dat_b[13]).as_signed()),
        ]

        # Sign-extend 14-bit -> LockCoreTop's 16-bit ADC input width.
        m.d.comb += [
            core.i_adc_ch0.eq(adc_a_signed),  # ADC_CH0: demodulated MTS error
            core.i_adc_ch1.eq(adc_b_signed),  # ADC_CH1: raw RF monitor
            core.i_adc_valid.eq(1),           # ADC samples are valid every cycle at full rate
            core.i_adc_overrange_ch0.eq(0),   # TODO: wire from real ADC overrange detect if available
            core.i_adc_overrange_ch1.eq(0),
            core.i_format_mode.eq(1),         # two's-complement passthrough (already converted above)
            core.i_external_interlock.eq(self.i_external_interlock),
            core.i_feature_selected.eq(self.i_feature_selected),
        ]

        # ---- DAC: two's complement -> board neg-slope encoding ----
        # LockCoreTop's DACFastFormatter output is 16-bit; take the top
        # 14 bits (drop 2 LSBs) to match the physical DAC width.
        dac_fast_14 = Signal(signed(14))
        dac_slow_14 = Signal(signed(14))
        m.d.comb += [
            dac_fast_14.eq(core.o_dac_fast.as_signed()[2:]),
            dac_slow_14.eq(core.o_dac_slow.as_signed()[2:]),
        ]
        m.d.comb += [
            self.o_dac_dat_a.eq(Cat(~dac_fast_14[:13], dac_fast_14[13])),
            self.o_dac_dat_b.eq(Cat(~dac_slow_14[:13], dac_slow_14[13])),
        ]

        # ---- status ----
        m.d.comb += [
            self.o_lock_state.eq(core.lock_state),
            self.o_lock_fault.eq(core.lock_fault),
            self.o_trace_ready.eq(core.trace_ready),
        ]

        # ---- register bus: Red Pitaya sys_* <-> project adr/dat_w/... ----
        m.d.comb += [
            bridge.sys_addr.eq(self.sys_addr),
            bridge.sys_wdata.eq(self.sys_wdata),
            bridge.sys_sel.eq(self.sys_sel),
            bridge.sys_wen.eq(self.sys_wen),
            bridge.sys_ren.eq(self.sys_ren),
            self.sys_rdata.eq(bridge.sys_rdata),
            self.sys_err.eq(bridge.sys_err),
            self.sys_ack.eq(bridge.sys_ack),

            core.adr.eq(bridge.adr),
            core.dat_w.eq(bridge.dat_w),
            bridge.dat_r.eq(core.dat_r),
            core.we.eq(bridge.we),
            core.stb.eq(bridge.stb),
        ]

        return m