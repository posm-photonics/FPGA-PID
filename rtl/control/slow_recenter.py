"""
slow_recenter.py

Implements slow_recenter.sv from section 8.11 of the onboarding
packet:

    "After lock, slowly adjust DAC_SLOW so that DAC_FAST stays near
    its center code:

        s[n+1] = s[n] + Ks * (u_fast[n] - u_fast_center)      (Eq. 25)

    The update rate should be slow compared with the fast loop."

This module is Category B (section 6.2): it must never run at the
1 MHz fast-loop rate. It is clocked by the same system clock as the
fast path (so it can be summed/muxed into DAC_SLOW deterministically)
but only *updates* its accumulator on a slow tick derived from a
programmable divider (SLOW_CTRL_CONFIG tick-div field), per the "slow
compared with the fast loop" requirement.

Because the CTL200 AC/fast input can't carry true DC correction
(section 3.4 / section 8.5's "Important" box), this accumulator is
where the long-term integral authority actually lives -- pi_core's
own integrator is deliberately weak/leaky (FAST_KI_LOCAL, FAST_INT_LEAK).

Register map (byte offsets, matches section 11.5 exactly):

    0x100 SLOW_CTRL_CONFIG      R/W  see bit layout below
    0x104 SLOW_BIAS             R/W  DC bias / center code, added to accumulator
    0x108 SLOW_KI               R/W  reserved for a future slow-scan integrator (unused here)
    0x10C SLOW_RECENTER_TARGET  R/W  u_fast_center (Eq. 25)
    0x110 SLOW_RECENTER_GAIN    R/W  Ks, signed fixed-point (Q(GAIN_FRAC))
    0x114 SLOW_OUT_MIN          R/W  hard clamp, low
    0x118 SLOW_OUT_MAX          R/W  hard clamp, high
    0x11C SLOW_OUT_SAFE         R/W  output forced here while faulted/disabled
    0x120 SLOW_SLEW_LIMIT       R/W  max |delta| per slow tick
    0x124 SLOW_OUT_CURRENT      R    current slow DAC command (post-clamp)

SLOW_CTRL_CONFIG bit layout (not enumerated bit-by-bit in the packet
beyond "enables scan, hold, slow integrator, recentering" -- defined
concretely here, see register_defs.py):

    bit 0       recenter_enable
    bit 1       hold            (freeze accumulator, keep last output)
    bit 2       accum_reset     (force accumulator to zero / SLOW_BIAS)
    bit 3       accum_load      (load accumulator from a write to
                                  SLOW_OUT_CURRENT, for smooth scan->lock
                                  handoff, mirrors pi_core's
                                  integrator_load/output_load requirement
                                  in section 9.3)
    bits [15:8] tick_div_shift  slow tick = 1 every 2**tick_div_shift
                                  fast-domain cycles where sample_valid pulses

This module does NOT decide whether it or ramp_scan drives DAC_SLOW.
Per section 9 ("Before lock: scan and center. After lock: slow
recentering..."), that arbitration belongs to lock_fsm / a small mux
in lock_core_top, driven off the `locked` status. This module's
`slow_out` should be selected once locked; ramp_scan's ramp should be
selected otherwise.
"""

from amaranth import Module, Signal, Elaboratable, Mux, signed
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from rtl.bus.register_defs import (
    ADDR_SLOW_CTRL_CONFIG, ADDR_SLOW_BIAS, ADDR_SLOW_KI,
    ADDR_SLOW_RECENTER_TARGET, ADDR_SLOW_RECENTER_GAIN,
    ADDR_SLOW_OUT_MIN, ADDR_SLOW_OUT_MAX, ADDR_SLOW_OUT_SAFE,
    ADDR_SLOW_SLEW_LIMIT, ADDR_SLOW_OUT_CURRENT,
    SLOW_CFG_RECENTER_ENABLE, SLOW_CFG_HOLD, SLOW_CFG_ACCUM_RESET,
    SLOW_CFG_ACCUM_LOAD, SLOW_CFG_TICK_DIV_SHIFT, SLOW_CFG_TICK_DIV_WIDTH,
    DAC_W,
)

GAIN_FRAC = 12  # SLOW_RECENTER_GAIN is signed Q(16-GAIN_FRAC).GAIN_FRAC


class SlowRecenter(Elaboratable):
    """
    Parameters
    ----------
    dac_w : int
        Width of dac_fast_in / slow_out, signed.
    gain_w : int
        Width of the SLOW_RECENTER_GAIN fixed-point register.
    accum_w : int
        Internal accumulator width (Q(GAIN_FRAC) fixed point). Wider
        than dac_w so mid-accumulation values don't clip; the final
        result is clamped down to dac_w range by SLOW_OUT_MIN/MAX
        before being exposed as slow_out.
    """

    def __init__(self, dac_w=DAC_W, gain_w=16, accum_w=None):
        self.dac_w = dac_w
        self.gain_w = gain_w
        self.accum_w = accum_w or (dac_w + gain_w + 4)

        # --- streaming inputs ---
        self.dac_fast_in  = Signal(signed(dac_w))  # u_fast[n], from output_limiter/fault_gate
        self.sample_valid = Signal()                # fast-domain sample strobe (drives the tick divider)
        self.fault_force  = Signal()                # force output to SLOW_OUT_SAFE, freeze accumulator

        # --- output ---
        self.slow_out       = Signal(signed(dac_w))  # clamped recenter contribution to DAC_SLOW
        self.slow_saturated = Signal()                # high if clamp is active (for lock_watch / fault_gate)

        # --- bus ---
        self.adr   = Signal(12)
        self.dat_w = Signal(32)
        self.dat_r = Signal(32)
        self.we    = Signal()
        self.stb   = Signal()

    def elaborate(self, platform):
        m = Module()

        dac_w, gain_w, accum_w = self.dac_w, self.gain_w, self.accum_w

        config     = Signal(32)
        bias       = Signal(signed(dac_w), reset=0)
        ki_unused  = Signal(signed(gain_w))  # SLOW_KI: register exists, not consumed here
        target     = Signal(signed(dac_w))
        gain       = Signal(signed(gain_w))
        out_min    = Signal(signed(dac_w))
        out_max    = Signal(signed(dac_w))
        out_safe   = Signal(signed(dac_w))
        slew_limit = Signal(dac_w)  # unsigned magnitude

        accumulator  = Signal(signed(accum_w))
        slow_current = Signal(signed(dac_w))

        recenter_enable = config[SLOW_CFG_RECENTER_ENABLE]
        hold            = config[SLOW_CFG_HOLD]
        accum_reset     = config[SLOW_CFG_ACCUM_RESET]
        accum_load      = config[SLOW_CFG_ACCUM_LOAD]
        tick_div_shift  = config[SLOW_CFG_TICK_DIV_SHIFT:
                                 SLOW_CFG_TICK_DIV_SHIFT + SLOW_CFG_TICK_DIV_WIDTH]

        word_adr = self.adr[2:]

        # ---------------- bus write decode ----------------
        with m.If(self.stb & self.we):
            with m.Switch(word_adr):
                with m.Case(ADDR_SLOW_CTRL_CONFIG >> 2):
                    m.d.sync += config.eq(self.dat_w)
                with m.Case(ADDR_SLOW_BIAS >> 2):
                    m.d.sync += bias.eq(self.dat_w[:dac_w].as_signed())
                with m.Case(ADDR_SLOW_KI >> 2):
                    m.d.sync += ki_unused.eq(self.dat_w[:gain_w].as_signed())
                with m.Case(ADDR_SLOW_RECENTER_TARGET >> 2):
                    m.d.sync += target.eq(self.dat_w[:dac_w].as_signed())
                with m.Case(ADDR_SLOW_RECENTER_GAIN >> 2):
                    m.d.sync += gain.eq(self.dat_w[:gain_w].as_signed())
                with m.Case(ADDR_SLOW_OUT_MIN >> 2):
                    m.d.sync += out_min.eq(self.dat_w[:dac_w].as_signed())
                with m.Case(ADDR_SLOW_OUT_MAX >> 2):
                    m.d.sync += out_max.eq(self.dat_w[:dac_w].as_signed())
                with m.Case(ADDR_SLOW_OUT_SAFE >> 2):
                    m.d.sync += out_safe.eq(self.dat_w[:dac_w].as_signed())
                with m.Case(ADDR_SLOW_SLEW_LIMIT >> 2):
                    m.d.sync += slew_limit.eq(self.dat_w[:dac_w])
                with m.Case(ADDR_SLOW_OUT_CURRENT >> 2):
                    # writing here is the accum_load data source
                    # (paired with the accum_load config bit), letting
                    # the PC/lock_fsm hand off a known-good starting
                    # point without an output jump (section 9.3).
                    m.d.sync += accumulator.eq(
                        self.dat_w[:dac_w].as_signed() << GAIN_FRAC
                    )

        # ---------------- slow tick divider ----------------
        tick_counter = Signal(SLOW_CFG_TICK_DIV_WIDTH)
        slow_tick    = Signal()
        m.d.comb += slow_tick.eq(self.sample_valid & (tick_counter == 0))
        with m.If(self.sample_valid):
            with m.If(tick_counter == 0):
                with m.If(tick_div_shift == 0):
                    m.d.sync += tick_counter.eq(0)
                with m.Else():
                    m.d.sync += tick_counter.eq((1 << tick_div_shift) - 1)
            with m.Else():
                m.d.sync += tick_counter.eq(tick_counter - 1)

        # ---------------- Eq. 25 update ----------------
        # error term: (u_fast[n] - u_fast_center)
        fast_err = Signal(signed(dac_w + 1))
        m.d.comb += fast_err.eq(self.dac_fast_in - target)

        product = Signal(signed(dac_w + gain_w + 2))
        m.d.comb += product.eq(fast_err * gain)  # already in Q(GAIN_FRAC) since gain is Q(GAIN_FRAC)

        # slew-limit the per-tick delta (same Q(GAIN_FRAC) domain as accumulator)
        delta_limit = Signal(signed(accum_w))
        delta_final = Signal(signed(accum_w))
        m.d.comb += delta_limit.eq(slew_limit.as_signed() << GAIN_FRAC)
        with m.If(product > delta_limit):
            m.d.comb += delta_final.eq(delta_limit)
        with m.Elif(product < -delta_limit):
            m.d.comb += delta_final.eq(-delta_limit)
        with m.Else():
            m.d.comb += delta_final.eq(product)

        with m.If(accum_reset):
            m.d.sync += accumulator.eq(0)
        with m.Elif(slow_tick & recenter_enable & ~hold
                    & ~accum_load & ~self.fault_force):
            m.d.sync += accumulator.eq(accumulator + delta_final)
        # accum_load is handled entirely by the SLOW_OUT_CURRENT write
        # case above; nothing else to do here on that path.

        # ---------------- output: bias + accumulator, clamped -------
        unclamped = Signal(signed(accum_w + dac_w + 2))
        m.d.comb += unclamped.eq((bias << GAIN_FRAC) + accumulator)

        descaled = Signal(signed(accum_w + dac_w + 2))
        m.d.comb += descaled.eq(unclamped >> GAIN_FRAC)  # drop fractional bits

        clamped   = Signal(signed(dac_w))
        saturated = Signal()
        with m.If(descaled > out_max):
            m.d.comb += [clamped.eq(out_max), saturated.eq(1)]
        with m.Elif(descaled < out_min):
            m.d.comb += [clamped.eq(out_min), saturated.eq(1)]
        with m.Else():
            m.d.comb += [clamped.eq(descaled[:dac_w].as_signed()), saturated.eq(0)]

        m.d.sync += slow_current.eq(Mux(self.fault_force, out_safe, clamped))

        m.d.comb += [
            self.slow_out.eq(slow_current),
            self.slow_saturated.eq(saturated),
        ]

        # ---------------- bus read decode ----------------
        with m.Switch(word_adr):
            with m.Case(ADDR_SLOW_CTRL_CONFIG >> 2):
                m.d.comb += self.dat_r.eq(config)
            with m.Case(ADDR_SLOW_BIAS >> 2):
                m.d.comb += self.dat_r.eq(bias)
            with m.Case(ADDR_SLOW_KI >> 2):
                m.d.comb += self.dat_r.eq(ki_unused)
            with m.Case(ADDR_SLOW_RECENTER_TARGET >> 2):
                m.d.comb += self.dat_r.eq(target)
            with m.Case(ADDR_SLOW_RECENTER_GAIN >> 2):
                m.d.comb += self.dat_r.eq(gain)
            with m.Case(ADDR_SLOW_OUT_MIN >> 2):
                m.d.comb += self.dat_r.eq(out_min)
            with m.Case(ADDR_SLOW_OUT_MAX >> 2):
                m.d.comb += self.dat_r.eq(out_max)
            with m.Case(ADDR_SLOW_OUT_SAFE >> 2):
                m.d.comb += self.dat_r.eq(out_safe)
            with m.Case(ADDR_SLOW_SLEW_LIMIT >> 2):
                m.d.comb += self.dat_r.eq(slew_limit)
            with m.Case(ADDR_SLOW_OUT_CURRENT >> 2):
                m.d.comb += self.dat_r.eq(slow_current)
            with m.Default():
                m.d.comb += self.dat_r.eq(0)

        return m
