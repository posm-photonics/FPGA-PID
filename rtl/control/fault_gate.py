from amaranth import *


class FaultGate(Elaboratable):
    """
    ============================================================
    FAULT GATE (FINAL SAFETY STAGE)
    ============================================================

    If fault = 1:
        output = safe code
    else:
        output = input

    ------------------------------------------------------------
    AUDIT FIXES (S2-3)
    ------------------------------------------------------------
    1. THE OVERRIDE WAS GATED BY i_valid.

       The previous version wrapped everything in

           with m.If(self.i_valid):
               with m.If(self.i_fault):
                   o_u <= SAFE_CODE
               ...

       so if the upstream valid stream stopped, o_u simply HELD its last
       pre-fault value and the DAC kept driving it. An ADC failure is
       precisely the event that both stops the valid stream and raises
       the fault, so the final safety stage did not fail safe on the
       failure mode it exists to handle.

       The fault override is now unconditional. It does not wait for a
       sample, because the entire point is that samples may have
       stopped arriving.

    2. THE SAFE CODE WAS A COMPILE-TIME CONSTANT.

       POSM_project_FPGALock.pdf section 10.2 requires a fault to "force
       DAC_FAST to FAST_OUT_SAFE", which is a register (packet 11.4,
       0x0E4). The old module baked SAFE_CODE in at construction, and
       nothing connected the register to it, so the safe code could only
       be changed by rebuilding the bitstream.

       i_safe_code is now an input port. SAFE_CODE remains the power-on
       default for the case where software has not yet written the
       register.

    Note on encoding: i_safe_code is in the SIGNED controller domain,
    not DAC code space, so 0 means mid-scale regardless of whether the
    downstream formatter is set to two's complement or offset binary.
    Keeping the safe code in controller units is what makes it
    encoding-independent.
    """

    def __init__(self, width=24, SAFE_CODE=0):
        self.width = width
        self.SAFE_CODE = SAFE_CODE

        self.i_u = Signal(signed(width))
        self.i_fault = Signal()
        self.i_valid = Signal()

        # Programmable safe output, in signed controller units. Defaults
        # to SAFE_CODE so an unconfigured system still parks at a
        # defined, safe value.
        self.i_safe_code = Signal(signed(width), init=SAFE_CODE)

        self.o_u = Signal(signed(width), init=SAFE_CODE)
        self.o_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        # ------------------------------------------------------
        # Fault override: UNCONDITIONAL.
        #
        # Deliberately outside the i_valid guard. A stopped sample
        # stream is a reason to drive the safe code, not a reason to
        # skip driving it.
        # ------------------------------------------------------
        with m.If(self.i_fault):
            m.d.sync += [
                self.o_u.eq(self.i_safe_code),
                # A safe output is still a valid command: the DAC stage
                # must keep clocking the safe code out rather than
                # freezing on its last pre-fault value.
                self.o_valid.eq(1),
            ]

        with m.Elif(self.i_valid):
            m.d.sync += [
                self.o_u.eq(self.i_u),
                self.o_valid.eq(1),
            ]

        with m.Else():
            m.d.sync += self.o_valid.eq(0)

        return m
