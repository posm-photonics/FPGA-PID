# error_calc.py
# Converts validated ADC sample into a signed control error
#
# e[n] = p(sample_in - offset - setpoint)
# p = +1 or -1 depending on invert_error
#
# Latency: 1 clock cycle
# Reset behavior: output zero, error_valid low

from amaranth import *
from amaranth.sim import *


class ErrorCalc(Elaboratable):
    """
    Signed error calculator for MTS laser lock.

    Parameters
    ----------
    adc_w : int
        ADC sample width in bits (default 16)
    err_w : int
        Error output width in bits (default 20)

    Ports
    -----
    sample_in    : signed input, adc_w bits
    sample_valid : input, 1 bit
    offset       : signed input, adc_w bits (DC background)
    setpoint     : signed input, adc_w bits (desired lock point)
    invert_error : input, 1 bit (flips sign for slope polarity)
    error_out    : signed output, err_w bits
    error_valid  : output, 1 bit
    """

    def __init__(self, adc_w=16, err_w=20):
        self.adc_w = adc_w
        self.err_w = err_w

        # --- input ports ---
        self.sample_in    = Signal(signed(adc_w))
        self.sample_valid = Signal()
        self.offset       = Signal(signed(adc_w))
        self.setpoint     = Signal(signed(adc_w))
        self.invert_error = Signal()

        # --- output ports ---
        self.error_out    = Signal(signed(err_w))
        self.error_valid  = Signal()

    def elaborate(self, platform):
        m = Module()

        # -------------------------------------------------------
        # Intermediate signals
        # Widths grow at each subtraction step to avoid overflow
        # x_corr  : adc_w + 1 = 17 bits (sample - offset)
        # err_raw : adc_w + 2 = 18 bits (x_corr - setpoint)
        # err_wide: err_w     = 20 bits (sign extended)
        # -------------------------------------------------------
        x_corr   = Signal(signed(self.adc_w + 1))
        err_raw  = Signal(signed(self.adc_w + 2))
        err_wide = Signal(signed(self.err_w))

        # -------------------------------------------------------
        # Combinational: compute error each cycle
        # Amaranth automatically handles sign extension when
        # assigning narrower signed signals to wider ones
        # -------------------------------------------------------
        m.d.comb += [
            x_corr.eq(self.sample_in - self.offset),
            err_raw.eq(x_corr - self.setpoint),
            err_wide.eq(err_raw),
        ]

        # -------------------------------------------------------
        # Registered output — 1 cycle latency
        # -------------------------------------------------------
        m.d.sync += [
            self.error_valid.eq(self.sample_valid),
        ]

        with m.If(self.sample_valid):
            with m.If(self.invert_error):
                m.d.sync += self.error_out.eq(-err_wide)
            with m.Else():
                m.d.sync += self.error_out.eq(err_wide)

        return m
