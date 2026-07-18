# sat_math.py
# Saturating arithmetic utilities for POSM MTS laser lock
#
# Provides reusable saturating functions used across all DSP modules.
# All arithmetic is signed two's complement.
#
# In Amaranth, saturation is handled differently than SystemVerilog.
# Instead of a package with functions, we provide:
#   1. A standalone Amaranth module (SatMath) for use in hardware
#   2. Plain Python functions for use in testbenches and scripts
#
# Width documentation:
#   sat_18to16 : 18-bit signed -> 16-bit signed
#   sat_40to16 : 40-bit signed -> 16-bit signed
#   sat_40to18 : 40-bit signed -> 18-bit signed
#   sat_Nto M  : N-bit signed  -> M-bit signed (generic)

from amaranth import *
from amaranth.sim import *


# -------------------------------------------------------
# Pure Python saturation functions
# Use these in testbenches, scripts, and gain calculators
# They mirror exactly what the hardware does
# -------------------------------------------------------

def sat(value, out_w):
    """
    Saturate a signed integer value to out_w bits.
    
    Parameters
    ----------
    value : int
        Input value (signed integer, any width)
    out_w : int
        Output bit width

    Returns
    -------
    int
        Saturated signed integer in range
        [-(2^(out_w-1)), 2^(out_w-1)-1]

    Examples
    --------
    >>> sat(40000, 16)   # positive overflow
    32767
    >>> sat(-40000, 16)  # negative overflow
    -32768
    >>> sat(100, 16)     # passthrough
    100
    >>> sat(-100, 16)    # passthrough negative
    -100
    """
    max_val =  (1 << (out_w - 1)) - 1   #  2^(out_w-1) - 1
    min_val = -(1 << (out_w - 1))       # -2^(out_w-1)

    if value > max_val:
        return max_val
    elif value < min_val:
        return min_val
    else:
        return value


def sat_18to16(value):
    """Saturate 18-bit signed to 16-bit signed."""
    return sat(value, 16)


def sat_40to16(value):
    """Saturate 40-bit signed to 16-bit signed."""
    return sat(value, 16)


def sat_40to18(value):
    """Saturate 40-bit signed to 18-bit signed."""
    return sat(value, 18)


def q314_to_real(register_value):
    """
    Convert Q3.14 fixed-point register value to real float.

    Parameters
    ----------
    register_value : int
        Signed integer stored in gain register

    Returns
    -------
    float
        Real gain value

    Examples
    --------
    >>> q314_to_real(8192)   # 0.5
    0.5
    >>> q314_to_real(16384)  # 1.0
    1.0
    >>> q314_to_real(-8192)  # -0.5
    -0.5
    """
    return register_value / (2 ** 14)


def real_to_q314(real_gain):
    """
    Convert real float gain to Q3.14 fixed-point register value.

    Parameters
    ----------
    real_gain : float
        Real gain value

    Returns
    -------
    int
        Signed integer to write to gain register

    Examples
    --------
    >>> real_to_q314(0.5)    # 8192
    8192
    >>> real_to_q314(1.0)    # 16384
    16384
    >>> real_to_q314(0.004)  # 65
    65
    """
    return int(real_gain * (2 ** 14))


# -------------------------------------------------------
# Amaranth hardware module
# Use this inside elaborate() methods when you need
# saturating output in RTL
# -------------------------------------------------------

class SatMath(Elaboratable):
    """
    Saturating arithmetic module for use in Amaranth hardware.

    Takes a wide signed input and saturates it to a narrower
    signed output. Exposes saturation flags for diagnostics
    and anti-windup logic.

    Parameters
    ----------
    in_w  : int  input width in bits
    out_w : int  output width in bits

    Ports
    -----
    value_in  : signed input,  in_w bits
    value_out : signed output, out_w bits
    sat_hi    : output, 1 bit — input exceeded positive max
    sat_lo    : output, 1 bit — input exceeded negative min
    """

    def __init__(self, in_w, out_w):
        assert in_w > out_w, \
            f"SatMath: in_w ({in_w}) must be wider than out_w ({out_w})"

        self.in_w  = in_w
        self.out_w = out_w

        # --- ports ---
        self.value_in  = Signal(signed(in_w))
        self.value_out = Signal(signed(out_w))
        self.sat_hi    = Signal()
        self.sat_lo    = Signal()

    def elaborate(self, platform):
        m = Module()

        # -------------------------------------------------------
        # Compute signed max and min for out_w
        # max =  2^(out_w-1) - 1
        # min = -2^(out_w-1)
        # These are constants so Amaranth computes them at
        # elaboration time, not in hardware
        # -------------------------------------------------------
        max_val =  (1 << (self.out_w - 1)) - 1
        min_val = -(1 << (self.out_w - 1))

        # -------------------------------------------------------
        # Combinational saturation logic
        # -------------------------------------------------------
        with m.If(self.value_in > max_val):
            m.d.comb += [
                self.value_out.eq(max_val),
                self.sat_hi.eq(1),
                self.sat_lo.eq(0),
            ]
        with m.Elif(self.value_in < min_val):
            m.d.comb += [
                self.value_out.eq(min_val),
                self.sat_hi.eq(0),
                self.sat_lo.eq(1),
            ]
        with m.Else():
            m.d.comb += [
                self.value_out.eq(self.value_in[:self.out_w]),
                self.sat_hi.eq(0),
                self.sat_lo.eq(0),
            ]

        return m
