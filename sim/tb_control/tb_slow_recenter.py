"""
tb_slow_recenter.py

Minimal smoke test for slow_recenter.py. Not a substitute for the
full test list in packet section 12.4 ("fast-output centering, slew
limit, saturation") -- this just exercises the basic accumulate-
toward-target behavior described by Eq. 25 so you can sanity check
the module compiles and moves in the right direction before writing
the real testbench.

Run with:
    python3 -m sim.tb_modules.tb_slow_recenter
"""

from amaranth.sim import Simulator
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rtl.control.slow_recenter import SlowRecenter, GAIN_FRAC
from rtl.bus.register_defs import (
    ADDR_SLOW_CTRL_CONFIG, ADDR_SLOW_RECENTER_TARGET,
    ADDR_SLOW_RECENTER_GAIN, ADDR_SLOW_OUT_MIN, ADDR_SLOW_OUT_MAX,
    ADDR_SLOW_SLEW_LIMIT,
    SLOW_CFG_RECENTER_ENABLE,
)


async def bus_write(ctx, dut, addr, data):
    ctx.set(dut.adr, addr)
    ctx.set(dut.dat_w, data & 0xFFFF_FFFF)
    ctx.set(dut.we, 1)
    ctx.set(dut.stb, 1)
    await ctx.tick()
    ctx.set(dut.we, 0)
    ctx.set(dut.stb, 0)
    await ctx.tick()
 
 
def main():
    dut = SlowRecenter(dac_w=16, gain_w=16)
 
    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz, arbitrary for this smoke test
 
    async def bench(ctx):
        # configure: target = 0 (center DAC_FAST at zero code),
        # gain Ks = 0.25 in Q12, slew limit generous, no tick divider
        # (tick every sample_valid pulse) so it converges fast for
        # the test.
        await bus_write(ctx, dut, ADDR_SLOW_RECENTER_TARGET, 0)
        await bus_write(ctx, dut, ADDR_SLOW_RECENTER_GAIN, int(0.25 * (1 << GAIN_FRAC)))
        await bus_write(ctx, dut, ADDR_SLOW_OUT_MIN, (-32768) & 0xFFFF)
        await bus_write(ctx, dut, ADDR_SLOW_OUT_MAX, 32767)
        await bus_write(ctx, dut, ADDR_SLOW_SLEW_LIMIT, 5000)
        await bus_write(ctx, dut, ADDR_SLOW_CTRL_CONFIG,
                         1 << SLOW_CFG_RECENTER_ENABLE)
 
        # simulate the fast DAC sitting away from center (e.g. rail-ish)
        ctx.set(dut.dac_fast_in, 8000)
 
        out_before = ctx.get(dut.slow_out)
        for _ in range(200):
            ctx.set(dut.sample_valid, 1)
            await ctx.tick()
            ctx.set(dut.sample_valid, 0)
            await ctx.tick()
 
        out_after = ctx.get(dut.slow_out)
        print(f"slow_out before={out_before}, after 200 ticks={out_after}")
        assert out_after > out_before, (
            "slow_out should have moved toward correcting u_fast back "
            "toward its center per Eq. 25"
        )
        print("tb_slow_recenter: OK (accumulator moved in the expected direction)")
 
    sim.add_testbench(bench)
    sim.run()
 
 
if __name__ == "__main__":
    main()
