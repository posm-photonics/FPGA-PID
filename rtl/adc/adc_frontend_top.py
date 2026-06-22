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

        # Outputs
        self.o_ch0 = Signal(signed(width + 1))
        self.o_ch1 = Signal(signed(width + 1))
        self.o_valid = Signal()
        self.o_fault_flags = Signal(5)

    def elaborate(self, platform):
        m = Module()

        m.domains.sync = ClockDomain() #Important for simulation and clock settings

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

        # Outputs (post-guard, but values unchanged)
        m.d.comb += [
            self.o_ch0.eq(gd.i_ch0),
            self.o_ch1.eq(gd.i_ch1),
            self.o_valid.eq(gd.o_valid),
            self.o_fault_flags.eq(gd.o_fault_flags),
        ]

        return m