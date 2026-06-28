# pi_controller.py
# Fixed-point PI controller for POSM MTS laser lock
#
# I[n] = I[n-1] + Ki * e[n]       (with anti-windup)
# u[n] = Kp * e[n] + I[n]
# ulim[n] = clip(u[n], umin, umax)
#
# Gain format: Q3.14 fixed-point
#   real_gain = register_value / 2^14
#   example: Kp=0.5 -> store 8192
#
# Latency: 2 clock cycles

from amaranth import *
from amaranth.sim import *


class PICore(Elaboratable):
    """
    Fixed-point PI controller.

    Parameters
    ----------
    err_w     : int  error input width  (default 20)
    out_w     : int  DAC output width   (default 16)
    gain_w    : int  gain register width (default 18)
    gain_frac : int  fractional bits in gain Q3.14 (default 14)
    acc_w     : int  accumulator width  (default 40)

    Ports
    -----
    error_in         : signed input, err_w bits
    error_valid      : input, 1 bit
    kp               : signed input, gain_w bits (Q3.14)
    ki               : signed input, gain_w bits (Q3.14)
    lock_enable      : input, 1 bit
    hold_enable      : input, 1 bit  (freezes output)
    integrator_reset : input, 1 bit  (clears integrator)
    integrator_load  : input, 1 bit  (loads integrator)
    load_value       : signed input, acc_w bits
    out_min          : signed input, out_w bits
    out_max          : signed input, out_w bits
    out_safe         : signed input, out_w bits
    control_out      : signed output, out_w bits
    control_valid    : output, 1 bit
    sat_hi           : output, 1 bit
    sat_lo           : output, 1 bit
    """

    def __init__(self, err_w=20, out_w=16,
                 gain_w=18, gain_frac=14, acc_w=40):
        self.err_w     = err_w
        self.out_w     = out_w
        self.gain_w    = gain_w
        self.gain_frac = gain_frac
        self.acc_w     = acc_w

        # --- input ports ---
        self.error_in         = Signal(signed(err_w))
        self.error_valid      = Signal()
        self.kp               = Signal(signed(gain_w))
        self.ki               = Signal(signed(gain_w))
        self.lock_enable      = Signal()
        self.hold_enable      = Signal()
        self.integrator_reset = Signal()
        self.integrator_load  = Signal()
        self.load_value       = Signal(signed(acc_w))
        self.out_min          = Signal(signed(out_w))
        self.out_max          = Signal(signed(out_w))
        self.out_safe         = Signal(signed(out_w))

        # --- output ports ---
        self.control_out   = Signal(signed(out_w))
        self.control_valid = Signal()
        self.sat_hi        = Signal()
        self.sat_lo        = Signal()

    def elaborate(self, platform):
        m = Module()

        # -------------------------------------------------------
        # Internal state
        # -------------------------------------------------------
        integrator = Signal(signed(self.acc_w))

        # -------------------------------------------------------
        # Wide intermediate signals
        # Bit growth:
        #   p_term : err_w + gain_w = 38 bits
        #   i_term : err_w + gain_w = 38 bits
        #   p_scaled, i_scaled : 38 - gain_frac = 24 -> acc_w
        #   candidate : acc_w = 40 bits
        # -------------------------------------------------------
        mul_w = self.err_w + self.gain_w  # 38 bits

        p_term     = Signal(signed(mul_w))
        i_term     = Signal(signed(mul_w))
        p_scaled   = Signal(signed(self.acc_w))
        i_scaled   = Signal(signed(self.acc_w))
        candidate  = Signal(signed(self.acc_w))
        int_next   = Signal(signed(self.acc_w))

        # saturation flags (combinational)
        sat_hi_comb = Signal()
        sat_lo_comb = Signal()

        # anti-windup suppress flag
        windup_suppress = Signal()

        # sign-extended limits for wide comparison
        out_max_ext = Signal(signed(self.acc_w))
        out_min_ext = Signal(signed(self.acc_w))

        # -------------------------------------------------------
        # Combinational math
        # -------------------------------------------------------
        m.d.comb += [
            # sign extend limits to accumulator width
            out_max_ext.eq(self.out_max),
            out_min_ext.eq(self.out_min),

            # multiply: full precision
            # Amaranth automatically handles signed multiply
            p_term.eq(self.error_in * self.kp),
            i_term.eq(self.error_in * self.ki),

            # scale: arithmetic right shift by gain_frac
            # this is the fixed-point divide by 2^14
            p_scaled.eq(p_term >> self.gain_frac),
            i_scaled.eq(i_term >> self.gain_frac),

            # integrator candidate
            int_next.eq(integrator + i_scaled),

            # output candidate: Kp*e + I[n-1]
            candidate.eq(p_scaled + integrator),

            # saturation detection
            sat_hi_comb.eq(candidate > out_max_ext),
            sat_lo_comb.eq(candidate < out_min_ext),

            # anti-windup:
            # suppress if saturated high and i_scaled pushing higher
            # suppress if saturated low  and i_scaled pushing lower
            windup_suppress.eq(
                (sat_hi_comb & (i_scaled > 0)) |
                (sat_lo_comb & (i_scaled < 0))
            ),
        ]

        # -------------------------------------------------------
        # Registered logic
        # -------------------------------------------------------
        with m.If(self.integrator_reset):
            m.d.sync += integrator.eq(0)

        with m.Elif(self.integrator_load):
            m.d.sync += integrator.eq(self.load_value)

        with m.Elif(self.error_valid & self.lock_enable & ~self.hold_enable):
            with m.If(~windup_suppress):
                m.d.sync += integrator.eq(int_next)

        # output update
        m.d.sync += self.control_valid.eq(0)

        with m.If(self.error_valid & self.lock_enable):

            with m.If(self.hold_enable):
                m.d.sync += self.control_valid.eq(1)

            with m.Else():
                m.d.sync += self.control_valid.eq(1)

                with m.If(sat_hi_comb):
                    m.d.sync += [
                        self.control_out.eq(self.out_max),
                        self.sat_hi.eq(1),
                        self.sat_lo.eq(0),
                    ]
                with m.Elif(sat_lo_comb):
                    m.d.sync += [
                        self.control_out.eq(self.out_min),
                        self.sat_hi.eq(0),
                        self.sat_lo.eq(1),
                    ]
                with m.Else():
                    m.d.sync += [
                        self.control_out.eq(candidate[:self.out_w]),
                        self.sat_hi.eq(0),
                        self.sat_lo.eq(0),
                    ]

        with m.Elif(~self.lock_enable):
            m.d.sync += [
                self.control_out.eq(self.out_safe),
                self.sat_hi.eq(0),
                self.sat_lo.eq(0),
                self.control_valid.eq(self.error_valid),
            ]

        return m
