from amaranth import *


class DACFastFormatter(Elaboratable):
    """
    ============================================================
    DAC FAST FORMATTER
    ============================================================

    Converts the signed internal control command into a DAC code.

    Modes:
        i_mode = 0 -> two's complement
        i_mode = 1 -> offset binary

    ------------------------------------------------------------
    AUDIT FIXES (S1-1, S2-9)
    ------------------------------------------------------------
    1. S1-1 (build blocker): the previous version wrote

           self.o_dac.eq(dac_max_signed[:self.DAC_WIDTH])

       where dac_max_signed is a plain Python int. Integers are not
       subscriptable, so this raised TypeError at elaboration and took
       LockCoreTop, RedPitayaLockCore, build/generate_verilog.py,
       run_bench.py and both integration testbenches down with it.

    2. S2-9 (silent wrap): the offset-binary branch computed
       `formatted = scaled + 2^(W-1)` into an UNSIGNED W-bit signal and
       then tested `formatted > 2^W - 1` (impossible for an unsigned
       W-bit signal) and `formatted < 0` (impossible for an unsigned
       signal). Both clamp arms were dead logic, so a negative overflow
       wrapped to a large positive DAC code and drove the actuator to
       the opposite rail.

    3. The two branches also had different pipeline depths (2 vs 3
       cycles) because `scaled` and `formatted` were both registered
       while the clamp compared the previous cycle's `formatted`.

    Corrected structure: clamp ONCE, in the signed domain, at full
    width, then convert the already-in-range value into the requested
    DAC encoding. Both modes now have identical, deterministic latency.

    Latency: 1 clock cycle from i_valid to o_valid, in both modes.
    """

    def __init__(self, width=24, DAC_WIDTH=16, SHIFT=0):
        self.width = width
        self.DAC_WIDTH = DAC_WIDTH
        self.SHIFT = SHIFT

        self.i_u = Signal(signed(width))
        self.i_valid = Signal()

        self.i_mode = Signal()

        # Unsigned because the DAC receives a binary code, not a signed
        # number. i_mode selects how that code is encoded.
        self.o_dac = Signal(DAC_WIDTH)
        self.o_valid = Signal()

        # High when the incoming command was outside the converter range
        # and had to be clamped. Exposed so lock_watch and the register
        # bank can observe real converter saturation instead of
        # inferring it from the limiter.
        self.o_sat = Signal()

    def elaborate(self, platform):
        m = Module()

        W = self.DAC_WIDTH

        # Signed bounds of the physical converter.
        dac_max_signed = (1 << (W - 1)) - 1     # +32767 for 16 bit
        dac_min_signed = -(1 << (W - 1))        # -32768 for 16 bit

        # ------------------------------------------------------
        # Combinational: rescale, then clamp in the SIGNED domain
        # ------------------------------------------------------
        scaled = Signal(signed(self.width))
        m.d.comb += scaled.eq(self.i_u >> self.SHIFT)

        clamped = Signal(signed(W))
        sat = Signal()

        with m.If(scaled > dac_max_signed):
            m.d.comb += [clamped.eq(dac_max_signed), sat.eq(1)]
        with m.Elif(scaled < dac_min_signed):
            m.d.comb += [clamped.eq(dac_min_signed), sat.eq(1)]
        with m.Else():
            m.d.comb += [clamped.eq(scaled), sat.eq(0)]

        # ------------------------------------------------------
        # Encoding conversion
        #
        # `clamped` is guaranteed to sit inside the converter's signed
        # range, so both conversions below are exact and neither can
        # overflow the W-bit output.
        # ------------------------------------------------------
        code = Signal(W)

        with m.If(self.i_mode):
            # Offset binary: move zero to the DAC midpoint.
            # clamped + 2^(W-1) lies in [0, 2^W - 1] by construction.
            m.d.comb += code.eq(clamped + (1 << (W - 1)))
        with m.Else():
            # Two's complement: the bit pattern is already correct.
            m.d.comb += code.eq(clamped)

        # ------------------------------------------------------
        # Single output register (uniform latency in both modes)
        # ------------------------------------------------------
        with m.If(self.i_valid):
            m.d.sync += [
                self.o_dac.eq(code),
                self.o_sat.eq(sat),
                self.o_valid.eq(1),
            ]
        with m.Else():
            m.d.sync += self.o_valid.eq(0)

        return m
