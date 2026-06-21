from amaranth import *

class ADCFormatter(Elaboratable):
    """
    Converts raw ADC codes into signed two's complement samples.

    Supported formats:
        format_mode = 0 -> offset binary
        format_mode = 1 -> two's complement passthrough
    """

    def __init__(self, width=16):
        self.width = width

        # Inputs
        self.i_ch0 = Signal(width)
        self.i_ch1 = Signal(width)
        self.i_valid = Signal()

        self.i_format_mode = Signal()  # 0=offset binary, 1=two's complement

        # Outputs
        self.o_ch0 = Signal(signed(width + 1))
        self.o_ch1 = Signal(signed(width + 1))
        self.o_valid = Signal()

    def elaborate(self, platform):
        m = Module()

        msb_mask = 1 << (self.width - 1)

        # Offset binary -> signed conversion:
        # subtract midpoint (MSB flip)
        def decode(raw):
            # Convert to integer domain first
            # Amaranth bitwise XOR works directly on Signals
            return (raw ^ msb_mask)

        with m.If(self.i_format_mode == 0):
            # offset binary mode
            m.d.comb += [
                self.o_ch0.eq(decode(self.i_ch0)),
                self.o_ch1.eq(decode(self.i_ch1)),
            ]
        with m.Else():
            # two's complement passthrough (sign-extend only)
            m.d.comb += [
                self.o_ch0.eq(self.i_ch0.as_signed()),
                self.o_ch1.eq(self.i_ch1.as_signed()),
            ]

        m.d.comb += self.o_valid.eq(self.i_valid)

        return m
