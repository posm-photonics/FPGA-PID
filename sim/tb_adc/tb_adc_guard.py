from amaranth import *
from amaranth.sim import Simulator
from amaranth.sim import Tick
from rtl.adc.adc_guard import ADCGuard

def test_adc_guard():
    m = ADCGuard(width=17, guard_count_threshold=3)

    sim = Simulator(m)
    sim.add_clock(1e-6)

    async def process(ctx):
        # NORMAL OPERATION
        for i in range(5):
            ctx.set(m.i_valid, 1)
            ctx.set(m.i_ch0, i)
            ctx.set(m.i_ch1, i + 1)
            ctx.set(m.i_overrange_ch0, 0)
            ctx.set(m.i_overrange_ch1, 0)

            await ctx.tick()
            await ctx.tick()

            faults = ctx.get(m.o_fault_flags)
            assert faults == 0

    
        # STUCK-AT DETECTION
        for i in range(10):
            ctx.set(m.i_valid, 1)
            ctx.set(m.i_ch0, 100)  # stuck
            ctx.set(m.i_ch1, 200)
            
            await ctx.tick()
            await ctx.tick()

        faults = ctx.get(m.o_fault_flags)
        assert (faults & (1 << 2)) != 0, "stuck CH0 not detected"

        # OVER-RANGE
        ctx.set(m.i_overrange_ch1,1)
        await ctx.tick()
        await ctx.tick()

        faults = ctx.get(m.o_fault_flags)
        assert (faults & (1 << 1)) != 0

        
        # MISSING VALID
        ctx.set(m.i_valid,0)
        await ctx.tick()
        await ctx.tick()

        faults = ctx.get(m.o_fault_flags)
        assert (faults & (1 << 4)) != 0   
    sim.add_testbench(process) # process has to run synchronized to clock edges
    with sim.write_vcd("adc_guard.vcd"):
        sim.run()
