# demod_lowpass.py
# Single-pole IIR low-pass filter for PDH demodulation
#
# Purpose:
#   Remove the sum-frequency component from the demodulator output,
#   retaining only the baseband (DC / near-DC) PDH error signal.
#
# Transfer function (Z-domain):
#   y[n] = y[n-1] + (x[n] - y[n-1]) >> alpha_shift
#
#   This is equivalent to:
#     y[n] = (1 - α) · y[n-1] + α · x[n]
#   where α = 1 / 2^alpha_shift.
#
#   Approximate -3 dB cutoff:
#     fc ≈ fs × α / (2π)  =  fs / (2π × 2^alpha_shift)
#
#   Example at fs = 125 MHz:
#     alpha_shift = 4  →  fc ≈ 1.24 MHz
#     alpha_shift = 8  →  fc ≈ 77.7 kHz
#     alpha_shift = 12 →  fc ≈ 4.86 kHz
#
# Fixed-point:
#   Input     : signed, in_w bits (default 20, matching demodulator output)
#   Accumulator: signed, acc_w bits (default 40, matching PI acc_w convention)
#   Output    : signed, out_w bits (default 20, matching err_w)
#
#   The accumulator is wider than the input to preserve precision through
#   the repeated right-shift operations.  The output is taken from the
#   upper bits of the accumulator (right-shifted by acc_w - out_w).
#
# Latency: 1 clock cycle (registered output)
# Reset: accumulator clears to zero deterministically

from amaranth import *


class DemodLowpass(Elaboratable):
    """Single-pole IIR low-pass filter for PDH demodulation.

    Parameters
    ----------
    in_w : int
        Input sample width (default 20).
    out_w : int
        Output sample width (default 20).
    acc_w : int
        Internal accumulator width (default 40).
    max_alpha : int
        Maximum allowed alpha_shift value (default 20).

    Ports
    -----
    sample_in    : signed input, in_w bits
    sample_valid : input, 1 bit
    alpha_shift  : input, 5 bits (unsigned) — filter bandwidth control
    reset_filter : input, 1 bit — clears accumulator to zero
    sample_out   : signed output, out_w bits
    out_valid    : output, 1 bit
    """

    def __init__(self, in_w=20, out_w=20, acc_w=40, max_alpha=20):
        self.in_w = in_w
        self.out_w = out_w
        self.acc_w = acc_w
        self.max_alpha = max_alpha

        # --- input ports ---
        self.sample_in = Signal(signed(in_w))
        self.sample_valid = Signal()
        self.alpha_shift = Signal(5)       # 0–31, but values > max_alpha
                                           # are clamped internally
        self.reset_filter = Signal()       # synchronous filter reset

        # --- output ports ---
        self.sample_out = Signal(signed(out_w))
        self.out_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        # ===================================================================
        # AUDIT FIX (S2-2)
        # ===================================================================
        # The previous version accumulated at INPUT scale and read the
        # LOW bits of the accumulator:
        #
        #     x_ext      = sample_in                 # no upscaling
        #     update     = diff >> alpha_shift       # floor division
        #     acc       += update
        #     sample_out = acc[:out_w]               # low bits
        #
        # When 0 <= diff < 2^alpha the update floored to zero and the
        # accumulator simply STOPPED, so the filter settled short of its
        # input by up to 2^alpha - 1 counts. Measured on the previous
        # version, for a step input of 10000:
        #
        #     alpha_shift    final output    gap
        #          4              9985        15
        #          8              9745       255
        #         12              5905      4095   (41% amplitude error)
        #
        # sim/tb_dsp/tb_demod_lowpass.py already failed on this
        # ("Expected 10000, got 9973") and the red test was committed.
        #
        # The module header already described the correct design ("The
        # accumulator is wider than the input to preserve precision
        # through the repeated right-shift operations. The output is
        # taken from the upper bits of the accumulator") -- it simply was
        # never implemented. The 20 bits of headroom that were meant to
        # hold the fractional residual went unused.
        #
        # Corrected: the accumulator carries `frac` fractional bits below
        # the input LSB, the update is rounded rather than floored, and
        # the output is taken from the upper bits with rounding. The dead
        # zone becomes 2^(alpha-frac) output LSBs, below one LSB for
        # every supported alpha.
        #
        # Same structural point as the PI integrator fix (S1-2), and the
        # same structure Linien uses in its filter path.
        # ===================================================================

        frac = self.acc_w - self.out_w      # 20 fractional bits by default

        # Accumulator (the filter state), Q(frac) relative to the input
        acc = Signal(signed(self.acc_w))

        # Extend AND upscale the input into accumulator precision
        x_ext = Signal(signed(self.acc_w))
        m.d.comb += x_ext.eq(self.sample_in << frac)

        # Difference: x[n] - y[n-1] (in accumulator precision).
        # One extra bit: x_ext and acc can sit at opposite extremes.
        diff = Signal(signed(self.acc_w + 1))
        m.d.comb += diff.eq(x_ext - acc)

        # Clamp alpha_shift to a safe range
        alpha_clamped = Signal(5)
        with m.If(self.alpha_shift > self.max_alpha):
            m.d.comb += alpha_clamped.eq(self.max_alpha)
        with m.Else():
            m.d.comb += alpha_clamped.eq(self.alpha_shift)

        # Round-to-nearest constant for the variable shift:
        # 2^(alpha-1), or 0 when alpha == 0 (the shift is a no-op).
        # alpha_minus_1 is kept UNSIGNED: Amaranth rejects a signed shift
        # amount, and `alpha_clamped - 1` is signed. Only evaluated where
        # alpha_clamped >= 1, so the subtraction cannot underflow.
        alpha_minus_1 = Signal(5)
        m.d.comb += alpha_minus_1.eq(alpha_clamped - 1)

        round_add = Signal(signed(self.acc_w + 1))
        with m.If(alpha_clamped == 0):
            m.d.comb += round_add.eq(0)
        with m.Else():
            m.d.comb += round_add.eq(
                Const(1, unsigned(self.acc_w)) << alpha_minus_1)

        # IIR update: acc += round(diff / 2^alpha)
        update = Signal(signed(self.acc_w + 1))
        m.d.comb += update.eq((diff + round_add) >> alpha_clamped)

        # Output: take the UPPER bits, with round-to-nearest.
        out_round = Signal(signed(self.acc_w + 1))
        if frac > 0:
            m.d.comb += out_round.eq((acc + (1 << (frac - 1))) >> frac)
        else:
            m.d.comb += out_round.eq(acc)

        # The accumulator is bounded by the input range, so out_round
        # normally fits out_w. Clamp anyway: in_w and out_w are separate
        # parameters and a caller may legitimately set in_w > out_w.
        out_max = (1 << (self.out_w - 1)) - 1
        out_min = -(1 << (self.out_w - 1))
        out_clamped = Signal(signed(self.out_w))
        with m.If(out_round > out_max):
            m.d.comb += out_clamped.eq(out_max)
        with m.Elif(out_round < out_min):
            m.d.comb += out_clamped.eq(out_min)
        with m.Else():
            m.d.comb += out_clamped.eq(out_round)

        # --- State update ---
        with m.If(self.reset_filter):
            m.d.sync += [
                acc.eq(0),
                self.sample_out.eq(0),
                self.out_valid.eq(0),
            ]
        with m.Elif(self.sample_valid):
            m.d.sync += [
                acc.eq(acc + update),
                self.sample_out.eq(out_clamped),
                self.out_valid.eq(1),
            ]
        with m.Else():
            m.d.sync += self.out_valid.eq(0)

        return m
