from amaranth import *

class OutputLimiter(Elaboratable):
    """
    Hard saturation block for DAC command safety.

    It might seem redundant at first but this is actually very important. All
    the clipping and saturation bits that we have in other modules are actually
    arithmetic clipping and protection in case of overflow.

    But this one is a hardware protection! It protects the actuator so that 
    we don't send outputs that are out of its range of movement. 
    """

    def __init__(self, width=24):
        self.width = width

        self.i_u = Signal(signed(width))
        self.i_valid = Signal()

        self.i_min = Signal(signed(width))
        self.i_max = Signal(signed(width))

        self.o_u = Signal(signed(width))
        self.o_sat = Signal()
        self.o_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        with m.If(self.i_valid):
            with m.If(self.i_u > self.i_max):
                m.d.sync += self.o_u.eq(self.i_max)
                m.d.sync += self.o_sat.eq(1)
            with m.Elif(self.i_u < self.i_min):
                m.d.sync += self.o_u.eq(self.i_min)
                m.d.sync += self.o_sat.eq(1)
            with m.Else():
                m.d.sync += self.o_u.eq(self.i_u)
                m.d.sync += self.o_sat.eq(0)

            m.d.sync += self.o_valid.eq(1)

        with m.Else():
            m.d.sync += self.o_valid.eq(0)

        return m