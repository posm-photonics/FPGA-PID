import os
import sys

# AUDIT FIX: this testbench had no sys.path bootstrap and could not
# be run standalone ("ModuleNotFoundError: No module named 'rtl'"),
# contradicting README.md's claim that the repo "can be cloned and
# simulated without hidden local paths".
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))
from amaranth import *
from amaranth.sim import Simulator
from amaranth.sim import Tick
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

    async def process(ctx):
        for i in range(200):

            # Stimulus: sinusoid
            x = sine_wave(i, AMP, DC, 5, FS)

            # OFFSET BINARY MODE
            ctx.set(m.i_format_mode, 0)
            ctx.set(m.i_ch0, (x + (1 << 15)) & 0xFFFF)
            ctx.set(m.i_ch1, (x + (1 << 15)) & 0xFFFF)
            ctx.set(m.i_valid, 1)

            await ctx.tick() # await ctx.tick() = advance 1 clock cycle
            await ctx.tick()

            
            # CHECK OFFSET BINARY DECODE
            o0 = ctx.get(m.o_ch0)
            o1 = ctx.get(m.o_ch1)

            expected = x

            assert abs(o0 - expected) < 2, f"CH0 mismatch {o0} vs {expected}"
            assert abs(o1 - expected) < 2, f"CH1 mismatch {o1} vs {expected}"

            # TWO'S COMPLEMENT MODE
            ctx.set(m.i_format_mode,1)
            ctx.set(m.i_ch0, (x & 0xFFFF))
            ctx.set(m.i_ch1,(x & 0xFFFF))
            ctx.set(m.i_valid,1)

            await ctx.tick()
            await ctx.tick()

            o0 = ctx.get(m.o_ch0)
            o1 = ctx.get(m.o_ch1)

            assert o0 == x, "Two's complement mismatch CH0"
            assert o1 == x, "Two's complement mismatch CH1"

    sim.add_testbench(process) # process has to run synchronized to clock edges
    with sim.write_vcd("adc_formatter.vcd"):
        sim.run()