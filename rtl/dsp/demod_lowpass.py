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

        # Accumulator (the filter state)
        acc = Signal(signed(self.acc_w))

        # Extend input to accumulator width for arithmetic
        x_ext = Signal(signed(self.acc_w))
        m.d.comb += x_ext.eq(self.sample_in)

        # Difference: x[n] - y[n-1] (in accumulator precision)
        diff = Signal(signed(self.acc_w))
        m.d.comb += diff.eq(x_ext - acc)

        # Clamp alpha_shift to a safe range
        alpha_clamped = Signal(5)
        with m.If(self.alpha_shift > self.max_alpha):
            m.d.comb += alpha_clamped.eq(self.max_alpha)
        with m.Else():
            m.d.comb += alpha_clamped.eq(self.alpha_shift)

        # IIR update: acc += diff >> alpha_shift
        # We use a barrel shifter (variable shift).
        update = Signal(signed(self.acc_w))
        m.d.comb += update.eq(diff >> alpha_clamped)

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
                # Output: take the full accumulator truncated to out_w.
                # Since acc and input have the same integer scaling (no
                # fractional shift between them), we just take the lower
                # out_w bits with saturation-like truncation.
                self.sample_out.eq(acc[:self.out_w].as_signed()),
                self.out_valid.eq(1),
            ]
        with m.Else():
            m.d.sync += self.out_valid.eq(0)

        return m
