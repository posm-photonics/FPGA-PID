"""
tb_demod_lowpass.py

Testbench for rtl/dsp/demod_lowpass.DemodLowpass.

AUDIT NOTES
-----------
This testbench was failing on the committed tree
("AssertionError: Expected 10000, got 9973") and the red test was
shipped anyway. Two separate problems were tangled together:

1. The RTL had a real defect (S2-2): the IIR accumulated at input scale
   and floored the update, so it stopped moving once the difference fell
   below 2^alpha and settled permanently short of its input. Measured on
   the old RTL for a step of 10000: 15 counts short at alpha=4, 255 at
   alpha=8, 4095 at alpha=12 (a 41% amplitude error).

2. The test itself only ran 100 ticks. With alpha_shift = 4 the time
   constant is 16 samples, so 100 ticks is ~6 time constants and the
   filter is still ~19 counts away on exponential settling alone. The
   assertion could not distinguish "still settling" from "permanently
   stuck", which is why the failure was easy to wave away.

Both are fixed. The test now runs long enough for settling to complete,
so the exact-equality assertion tests only what it should: zero
steady-state error. A parameter sweep was added as a direct regression
guard on the dead zone.

The testbench was also moved from the deprecated add_process/Tick() API
to the async testbench API, because the old API's settle semantics made
the synchronous-reset check ambiguous (it needed an extra Tick for a
reset that is correct in one clock edge).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth.sim import Simulator
from rtl.dsp.demod_lowpass import DemodLowpass


def _to_signed(value, width):
    return value - (1 << width) if value >= (1 << (width - 1)) else value


def test_lpf_step_and_reset():
    """DC step response settles exactly, and reset clears the filter."""
    dut = DemodLowpass(in_w=20, out_w=20, acc_w=40, max_alpha=20)

    async def testbench(ctx):
        # --- reset the filter ---
        ctx.set(dut.alpha_shift, 4)
        ctx.set(dut.reset_filter, 1)
        ctx.set(dut.sample_valid, 0)
        await ctx.tick()
        ctx.set(dut.reset_filter, 0)

        # --- DC step response ---
        ctx.set(dut.sample_in, 10000)
        ctx.set(dut.sample_valid, 1)

        # 1000 ticks is ~62 time constants at alpha=4: fully settled.
        await ctx.tick().repeat(1000)
        out = _to_signed(ctx.get(dut.sample_out), 20)
        assert out == 10000, \
            f"step response: expected 10000, got {out} (S2-2 dead zone?)"

        # --- synchronous reset clears the accumulator in one edge ---
        ctx.set(dut.reset_filter, 1)
        await ctx.tick()
        ctx.set(dut.reset_filter, 0)
        out = _to_signed(ctx.get(dut.sample_out), 20)
        assert out == 0, f"reset: expected 0, got {out}"

        print("PASS: test_lpf_step_and_reset")

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(testbench)
    sim.run()


def test_lpf_no_dead_zone():
    """
    Regression guard for S2-2.

    The old implementation settled 2^alpha - 1 counts short of its input,
    permanently, for every alpha. Sweep alpha and require exact
    convergence at each one. Only alphas whose settling time fits in the
    simulated window are checked; the dead zone was independent of run
    length, so this window is more than sufficient to catch it.
    """
    step = 10000
    for alpha in (1, 2, 4, 6, 8, 10, 12):
        dut = DemodLowpass(in_w=20, out_w=20, acc_w=40, max_alpha=20)
        captured = {}

        async def testbench(ctx, alpha=alpha, captured=captured):
            ctx.set(dut.alpha_shift, alpha)
            ctx.set(dut.sample_in, step)
            ctx.set(dut.sample_valid, 1)
            # ~30 time constants at the slowest alpha checked here.
            await ctx.tick().repeat(30 * (1 << alpha))
            captured["out"] = _to_signed(ctx.get(dut.sample_out), 20)

        sim = Simulator(dut)
        sim.add_clock(1e-8)
        sim.add_testbench(testbench)
        sim.run()

        out = captured["out"]
        assert out == step, (
            f"alpha_shift={alpha}: expected {step}, got {out} "
            f"(short by {step - out}; the S2-2 dead zone was 2^alpha - 1 "
            f"= {(1 << alpha) - 1})"
        )
        print(f"  alpha_shift={alpha:>2}: settled exactly on {out}")

    print("PASS: test_lpf_no_dead_zone")


if __name__ == "__main__":
    test_lpf_step_and_reset()
    test_lpf_no_dead_zone()
