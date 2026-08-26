# demodulator.py
# PDH demodulator (digital mixer) for the POSM FPGA MTS lock core
#
# Performs the multiply:
#   I = adc_sample × sin(reference + demod_phase)
#   Q = adc_sample × cos(reference + demod_phase)
#
# The NCO reference signals (sin/cos with phase offset already applied)
# are provided externally — the demodulator does NOT contain its own
# oscillator.  This guarantees the modulation and demodulation share
# a single phase accumulator (architectural requirement).
#
# Fixed-point analysis:
#   ADC input   : signed 17 bits (from ADCFrontendTop.o_ch0)
#   NCO ref     : signed 16 bits (from NCO.o_sin / o_cos)
#   Full product: signed 33 bits (17 + 16)
#   Output      : signed 20 bits (right-shifted by 13, then saturated)
#
#   The right-shift by 13 keeps the result in a range compatible with
#   the existing error_calc (err_w = 20).  The NCO amplitude is ±32767
#   (≈ Q1.14), so shifting by 13 effectively divides by 8192, giving
#   a result scaled to ≈ 2× the ADC code magnitude.
#
# Latency: 1 clock cycle (registered output)

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from amaranth import *
from rtl.common.sat_math import SatMath


class Demodulator(Elaboratable):
    """Digital mixer for PDH demodulation.

    Parameters
    ----------
    adc_w : int
        ADC sample width (default 17, matching ADCFrontendTop output).
    ref_w : int
        NCO reference width (default 16, matching NCO output).
    out_w : int
        Output width after saturation (default 20, matching err_w).
    shift : int
        Right-shift applied to the full product before saturation
        (default 13).

    Ports
    -----
    adc_in     : signed input, adc_w bits
    adc_valid  : input, 1 bit
    ref_sin    : signed input, ref_w bits (NCO sine with demod phase)
    ref_cos    : signed input, ref_w bits (NCO cosine with demod phase)
    i_out      : signed output, out_w bits (in-phase mixer output)
    q_out      : signed output, out_w bits (quadrature mixer output)
    out_valid  : output, 1 bit
    """

    def __init__(self, adc_w=17, ref_w=16, out_w=20, shift=13):
        self.adc_w = adc_w
        self.ref_w = ref_w
        self.out_w = out_w
        self.shift = shift

        # Full product width
        self.mul_w = adc_w + ref_w  # 33 bits

        # --- input ports ---
        self.adc_in = Signal(signed(adc_w))
        self.adc_valid = Signal()
        self.ref_sin = Signal(signed(ref_w))
        self.ref_cos = Signal(signed(ref_w))

        # --- output ports ---
        self.i_out = Signal(signed(out_w))
        self.q_out = Signal(signed(out_w))
        self.out_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        # Full-precision products
        i_product = Signal(signed(self.mul_w))
        q_product = Signal(signed(self.mul_w))

        m.d.comb += [
            i_product.eq(self.adc_in * self.ref_sin),
            q_product.eq(self.adc_in * self.ref_cos),
        ]

        # Right-shift to scale down, then saturate to out_w
        # The shifted width is mul_w (the shift doesn't reduce the signal
        # width, it just changes the numeric value).
        shifted_w = self.mul_w  # still need all bits for SatMath input

        i_shifted = Signal(signed(shifted_w))
        q_shifted = Signal(signed(shifted_w))

        m.d.comb += [
            i_shifted.eq(i_product >> self.shift),
            q_shifted.eq(q_product >> self.shift),
        ]

        # Saturate to output width using the project's standard saturator
        m.submodules.sat_i = sat_i = SatMath(shifted_w, self.out_w)
        m.submodules.sat_q = sat_q = SatMath(shifted_w, self.out_w)

        m.d.comb += [
            sat_i.value_in.eq(i_shifted),
            sat_q.value_in.eq(q_shifted),
        ]

        # Registered output (1-cycle latency)
        with m.If(self.adc_valid):
            m.d.sync += [
                self.i_out.eq(sat_i.value_out),
                self.q_out.eq(sat_q.value_out),
                self.out_valid.eq(1),
            ]
        with m.Else():
            m.d.sync += self.out_valid.eq(0)

        return m
