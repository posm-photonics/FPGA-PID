from amaranth import *
from rtl.adc.adc_formatter import ADCFormatter
from rtl.adc.adc_guard import ADCGuard

class ADCFrontendTop(Elaboratable):
    """
    Top-level ADC ingestion pipeline:

        ADC raw → formatter → guard → clean stream output

    No DSP, no control, no filtering.
    """

    def __init__(self, width=16):
        self.width = width

        # Inputs
        self.i_ch0 = Signal(width)
        self.i_ch1 = Signal(width)
        self.i_valid = Signal()

        self.i_overrange_ch0 = Signal()
        self.i_overrange_ch1 = Signal()

        self.i_format_mode = Signal()

        # Runtime-programmable stuck-sample threshold, passed through to
        # the guard (packet 11.2 ADC_GUARD_COUNT). See ADCGuard for why
        # a compile-time constant was not workable at 125 MSPS.
        self.i_guard_threshold = Signal(16, init=16)

        # Outputs
        self.o_ch0 = Signal(signed(width + 1))
        self.o_ch1 = Signal(signed(width + 1))
        self.o_valid = Signal()
        self.o_fault_flags = Signal(5)
        self.o_ch0_valid = Signal()
        self.o_ch1_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        # Submodules
        m.submodules.fmt = fmt = ADCFormatter(self.width)
        m.submodules.gd = gd = ADCGuard(self.width + 1)

        # Wiring: ADC -> formatter
        m.d.comb += [
            fmt.i_ch0.eq(self.i_ch0),
            fmt.i_ch1.eq(self.i_ch1),
            fmt.i_valid.eq(self.i_valid),
            fmt.i_format_mode.eq(self.i_format_mode),
        ]

        # Wiring: formatter -> guard
        m.d.comb += [
            gd.i_ch0.eq(fmt.o_ch0),
            gd.i_ch1.eq(fmt.o_ch1),
            gd.i_valid.eq(fmt.o_valid),
            gd.i_overrange_ch0.eq(self.i_overrange_ch0),
            gd.i_overrange_ch1.eq(self.i_overrange_ch1),
        ]

        # Guard configuration passthrough
        m.d.comb += gd.i_guard_threshold.eq(self.i_guard_threshold)

        # Outputs (post-guard, but values unchanged)
        #
        # AUDIT FIX (S4): these used to read gd.i_ch0 / gd.i_ch1, the
        # guard's own INPUTS. The guard is a passthrough so the value was
        # identical, but sourcing a top-level output from a block's input
        # rather than its output means the guard gets bypassed silently
        # the moment anyone makes it non-passthrough. Read the formatter
        # directly and say so, instead of reaching through the guard.
        m.d.comb += [
            self.o_ch0.eq(fmt.o_ch0),
            self.o_ch1.eq(fmt.o_ch1),
            self.o_valid.eq(gd.o_valid),
            self.o_fault_flags.eq(gd.o_fault_flags),
            self.o_ch0_valid.eq(gd.o_ch0_valid),
            self.o_ch1_valid.eq(gd.o_ch1_valid),
        ]

        return m