from amaranth import *

class DACFastFormatter(Elaboratable):
    """
    ============================================================
    DAC FAST FORMATTER
    ============================================================

    Converts signed control signal into DAC code.

    Modes:
        mode = 0 -> two's complement
        mode = 1 -> offset binary

    ------------------------------------------------------------
    """

    def __init__(self, width=24, DAC_WIDTH=16, SHIFT=0):
        self.width = width
        self.DAC_WIDTH = DAC_WIDTH
        self.SHIFT = SHIFT

        self.i_u = Signal(signed(width))
        self.i_valid = Signal()

        self.i_mode = Signal()

        self.o_dac = Signal(DAC_WIDTH) # Unsigned because the DAC chip doesn't 
                                     # understand "negative numbers."
        self.o_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        scaled = Signal(signed(self.width))
        formatted = Signal(self.DAC_WIDTH)

        dac_max = (1 << self.DAC_WIDTH) - 1 # max = (1 << n) − 1
        dac_min = 0 # dac is unsigned

        with m.If(self.i_valid):
            # scale back before sending the output. In case we had to add 
            # extra bits for computation.
            m.d.sync += scaled.eq(self.i_u >> self.SHIFT) 

            with m.If(self.i_mode):
                # offset binary
                m.d.sync += formatted.eq(scaled + (1 << (self.DAC_WIDTH - 1)))

                # Offset-binary values are unsigned and can be clamped in
                # the normal numeric domain.
                with m.If(formatted > dac_max):
                    m.d.sync += self.o_dac.eq(dac_max)
                with m.Elif(formatted < dac_min):
                    m.d.sync += self.o_dac.eq(dac_min)
                with m.Else():
                    m.d.sync += self.o_dac.eq(formatted)
            with m.Else():
                # two's complement
                # Preserve the signed bit pattern. Comparing this value as
                # unsigned would turn every negative command into a false
                # high-side clamp.
                m.d.sync += self.o_dac.eq(scaled[:self.DAC_WIDTH])

            m.d.sync += self.o_valid.eq(1)

        with m.Else():
            m.d.sync += self.o_valid.eq(0)

        return m