from amaranth import *
from amaranth.sim import Simulator
import math
from rtl.adc.adc_formatter import ADCFormatter


# Stimulus model: Sinusoid
def sine_wave(n, amp, dc, freq, fs):
    return int(dc + amp * math.sin(2 * math.pi * freq * n / fs))


def test_adc_formatter():
    m = ADCFormatter(width=16)

    sim = Simulator(m)
    sim.add_clock(1e-6)

    N = 16
    FS = 1000
    AMP = 10000
    DC = 0

    def process():
        for i in range(200):

            # Stimulus: sinusoid
            x = sine_wave(i, AMP, DC, 5, FS)

            # OFFSET BINARY MODE
            yield m.i_format_mode.eq(0)
            yield m.i_ch0.eq((x + (1 << 15)) & 0xFFFF)
            yield m.i_ch1.eq((x + (1 << 15)) & 0xFFFF)
            yield m.i_valid.eq(1)

            yield # yield = advance 1 clock cycle
            yield

            
            # CHECK OFFSET BINARY DECODE
            o0 = yield m.o_ch0
            o1 = yield m.o_ch1

            expected = x

            assert abs(o0 - expected) < 2, f"CH0 mismatch {o0} vs {expected}"
            assert abs(o1 - expected) < 2, f"CH1 mismatch {o1} vs {expected}"

            # TWO'S COMPLEMENT MODE
            yield m.i_format_mode.eq(1)
            yield m.i_ch0.eq(x & 0xFFFF)
            yield m.i_ch1.eq(x & 0xFFFF)
            yield m.i_valid.eq(1)

            yield
            yield

            o0 = yield m.o_ch0
            o1 = yield m.o_ch1

            assert o0 == x, "Two's complement mismatch CH0"
            assert o1 == x, "Two's complement mismatch CH1"

    sim.add_sync_process(process) # process has to run synchronized to clock edges
    with sim.write_vcd("adc_formatter.vcd"):
        sim.run()