from amaranth import *


class ADCGuard(Elaboratable):
    """
    Safety-only validator for ADC stream integrity.

    Does NOT modify sample values.
    Only flags invalid conditions.

    ------------------------------------------------------------
    AUDIT FIXES (S3-5)
    ------------------------------------------------------------
    1. THE STUCK COUNTER WRAPPED.

       _stuck_counter_ch0/_ch1 were 8-bit and incremented without a
       ceiling, so a permanently stuck ADC rolled the counter over every
       256 samples. The fault flag then dropped LOW for 17 out of every
       256 cycles instead of staying asserted. A fault condition that
       blinks is worse than one that does not fire at all, because
       anything edge- or level-sampling downstream sees it intermittently.

       The counters now saturate at their threshold instead of wrapping,
       so the flag is stable once raised.

    2. THE THRESHOLD WAS A COMPILE-TIME CONSTANT.

       Packet 11.2 defines ADC_GUARD_COUNT as a register ("Bad-sample
       count before fault candidate"). The correct value is entirely
       deployment-dependent: with i_adc_valid tied high the guard runs at
       125 MSPS, where a quiet 14-bit input holding the same code for 17
       consecutive samples is completely normal. The old fixed threshold
       of 16 therefore produced routine false positives on a healthy
       system. i_guard_threshold is now an input port.

    3. o_ch0_valid / o_ch1_valid WERE HARDWIRED TO 1.

       They were driven to constant 1 and never connected by the parent,
       which made them look like real qualifiers while carrying no
       information. They now actually deassert on a detected fault for
       their channel, so a consumer can gate on them.
    """

    def __init__(self, width=17, guard_count_threshold=16, count_w=16):
        self.width = width
        self.count_w = count_w

        # Power-on default for the stuck-sample threshold.
        self.guard_count_threshold = guard_count_threshold

        # Inputs
        self.i_ch0 = Signal(signed(width))
        self.i_ch1 = Signal(signed(width))
        self.i_valid = Signal()

        self.i_overrange_ch0 = Signal()
        self.i_overrange_ch1 = Signal()

        # Runtime-programmable stuck threshold (see note 2 above).
        self.i_guard_threshold = Signal(count_w, init=guard_count_threshold)

        # Outputs
        self.o_ch0_valid = Signal()
        self.o_ch1_valid = Signal()
        self.o_valid = Signal()

        self.o_fault_flags = Signal(5)  # bitfield
        # bit 0 -> ch0 overrange
        # bit 1 -> ch1 overrange
        # bit 2 -> ch0 stuck
        # bit 3 -> ch1 stuck
        # bit 4 -> missing valid

        # Internal state
        #
        # Counters are count_w wide and SATURATING (see note 1 above).
        self._stuck_counter_ch0 = Signal(count_w)
        self._stuck_counter_ch1 = Signal(count_w)

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

        m.d.comb += self.o_valid.eq(self.i_valid)

        # Fault flags
        faults = Signal(5)

        with m.If(self.i_overrange_ch0):
            m.d.comb += faults[0].eq(1)
        with m.If(self.i_overrange_ch1):
            m.d.comb += faults[1].eq(1)

        # -----------------------------------------------------------
        # Stuck detection (saturating persistence counter)
        # -----------------------------------------------------------
        with m.If(self.i_valid):
            with m.If(ch0_unchanged):
                # Saturate rather than wrap: once the threshold is
                # reached the count holds, so the fault flag stays
                # asserted for as long as the channel is stuck.
                with m.If(self._stuck_counter_ch0 < self.i_guard_threshold):
                    m.d.sync += self._stuck_counter_ch0.eq(
                        self._stuck_counter_ch0 + 1
                    )
            with m.Else():
                # Sample moved: the channel is alive, clear suspicion.
                m.d.sync += self._stuck_counter_ch0.eq(0)

            with m.If(ch1_unchanged):
                with m.If(self._stuck_counter_ch1 < self.i_guard_threshold):
                    m.d.sync += self._stuck_counter_ch1.eq(
                        self._stuck_counter_ch1 + 1
                    )
            with m.Else():
                m.d.sync += self._stuck_counter_ch1.eq(0)

            m.d.sync += [
                self._last_ch0.eq(self.i_ch0),
                self._last_ch1.eq(self.i_ch1),
            ]

        # The counters saturate AT the threshold, so the comparison is
        # >= rather than the previous > (which needed threshold+1 and
        # could therefore never be reached once saturating).
        #
        # A threshold of 0 disables stuck detection for that channel,
        # which is the sane interpretation of "zero bad samples allowed
        # before flagging" for a check that is inherently heuristic.
        with m.If((self.i_guard_threshold != 0)
                  & (self._stuck_counter_ch0 >= self.i_guard_threshold)):
            m.d.comb += faults[2].eq(1)

        with m.If((self.i_guard_threshold != 0)
                  & (self._stuck_counter_ch1 >= self.i_guard_threshold)):
            m.d.comb += faults[3].eq(1)

        # Missing valid stream
        with m.If(~self.i_valid):
            m.d.comb += faults[4].eq(1)

        m.d.comb += self.o_fault_flags.eq(faults)

        # Per-channel qualifiers now carry real information.
        m.d.comb += [
            self.o_ch0_valid.eq(self.i_valid & ~faults[0] & ~faults[2]),
            self.o_ch1_valid.eq(self.i_valid & ~faults[1] & ~faults[3]),
        ]

        return m
