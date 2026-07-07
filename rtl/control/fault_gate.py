from amaranth import *

class FaultGate(Elaboratable):
    """
    ============================================================
    FAULT GATE (FINAL SAFETY STAGE)
    ============================================================

    If fault=1:
        output = SAFE_CODE
    else:
        output = input

    We made one specific assumption in this module: SAFE_CODE is centered 
    DAC value unless specified externally. Which is usually true but might
    change if needed.
    """

    def __init__(self, width=24, SAFE_CODE=0):
        self.width = width
        self.SAFE_CODE = SAFE_CODE

        self.i_u = Signal(signed(width))
        self.i_fault = Signal()
        self.i_valid = Signal()

        self.o_u = Signal(signed(width))
        self.o_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        with m.If(self.i_valid):
            with m.If(self.i_fault):
                m.d.sync += self.o_u.eq(self.SAFE_CODE)
            with m.Else():
                m.d.sync += self.o_u.eq(self.i_u)

            m.d.sync += self.o_valid.eq(1)

        with m.Else():
            m.d.sync += self.o_valid.eq(0)

        return m