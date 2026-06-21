from amaranth import *

class ADCGuard(Elaboratable):
    """
    Safety-only validator for ADC stream integrity.

    Does NOT modify sample values.
    Only flags invalid conditions.
    """

    def __init__(self, width=17, guard_count_threshold=16):
        self.width = width
        self.guard_count_threshold = guard_count_threshold

        # Inputs
        self.i_ch0 = Signal(signed(width))
        self.i_ch1 = Signal(signed(width))
        self.i_valid = Signal()

        self.i_overrange_ch0 = Signal()
        self.i_overrange_ch1 = Signal()

        # Outputs
        self.o_ch0_valid = Signal()
        self.o_ch1_valid = Signal()
        self.o_valid = Signal()

        self.o_fault_flags = Signal(5)  # bitfield

        # Internal state
        self._stuck_counter_ch0 = Signal(8)
        self._stuck_counter_ch1 = Signal(8)

        self._last_ch0 = Signal(signed(width))
        self._last_ch1 = Signal(signed(width))

    def elaborate(self, platform):
        m = Module()

        ch0_unchanged = Signal()
        ch1_unchanged = Signal()

        m.d.comb += [
            ch0_unchanged.eq(self.i_ch0 == self._last_ch0),
            ch1_unchanged.eq(self.i_ch1 == self._last_ch1),
        ]

        # Default outputs
        m.d.comb += [
            self.o_ch0_valid.eq(1),
            self.o_ch1_valid.eq(1),
            self.o_valid.eq(self.i_valid),
        ]

        # Fault flags
        faults = Signal(5)

        with m.If(self.i_overrange_ch0):
            m.d.comb += faults[0].eq(1)
        with m.If(self.i_overrange_ch1):
            m.d.comb += faults[1].eq(1)

        # Stuck detection (simple persistence counter)
        with m.If(self.i_valid):
            with m.If(ch0_unchanged):
                m.d.sync += self._stuck_counter_ch0.eq(
                    self._stuck_counter_ch0 + 1
                )
            with m.Else():
                m.d.sync += self._stuck_counter_ch0.eq(0)

            with m.If(ch1_unchanged):
                m.d.sync += self._stuck_counter_ch1.eq(
                    self._stuck_counter_ch1 + 1
                )
            with m.Else():
                m.d.sync += self._stuck_counter_ch1.eq(0)

            m.d.sync += [
                self._last_ch0.eq(self.i_ch0),
                self._last_ch1.eq(self.i_ch1),
            ]

        with m.If(self._stuck_counter_ch0 > self.guard_count_threshold):
            m.d.comb += faults[2].eq(1)

        with m.If(self._stuck_counter_ch1 > self.guard_count_threshold):
            m.d.comb += faults[3].eq(1)

        # Missing valid stream
        with m.If(~self.i_valid):
            m.d.comb += faults[4].eq(1)

        m.d.comb += self.o_fault_flags.eq(faults)

        return m
