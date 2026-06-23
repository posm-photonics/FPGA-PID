from amaranth import *
from amaranth.sim import Simulator
from amaranth.sim import Tick
from rtl.adc.adc_frontend_top import ADCFrontendTop

def test_adc_frontend_top():
    m = ADCFrontendTop(width=16)

    sim = Simulator(m)
    sim.add_clock(1e-6)

    async def process(ctx):
        # NORMAL STREAMING
        for i in range(20):
            ctx.set(m.i_format_mode, 1)
            ctx.set(m.i_ch0, i)
            ctx.set(m.i_ch1, i + 10)
            ctx.set(m.i_valid, 1)
            ctx.set(m.i_overrange_ch0, 0)
            ctx.set(m.i_overrange_ch1, 0)

            await ctx.tick()
            await ctx.tick()

            o_valid = ctx.get(m.o_valid)
            assert o_valid == 1

        # FAULT INJECTION
        ctx.set(m.i_overrange_ch0,1)
        await ctx.tick()
        await ctx.tick()

        faults = ctx.get(m.o_fault_flags)
        assert faults != 0, "frontend failed to propagate guard fault"

    sim.add_testbench(process) # process has to run synchronized to clock edges
    with sim.write_vcd("adc_frontend_top.vcd"):
        sim.run()
