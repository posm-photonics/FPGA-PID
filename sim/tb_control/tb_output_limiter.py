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

from rtl.control.output_limiter import OutputLimiter


def test_output_limiter():

    dut = OutputLimiter(width=24)

    sim = Simulator(dut)

    sim.add_clock(1e-6)


    async def process(ctx):

        # ---------------------------------------------------------
        # Configure limits
        #
        # Allowed actuator range:
        #
        # -100000 <= output <= 100000
        #
        # ---------------------------------------------------------
        ctx.set(dut.i_min, -100000)
        ctx.set(dut.i_max, 100000)


        # =========================================================
        # TEST 1:
        #
        # Normal operation
        #
        # Input inside limits should pass unchanged.
        # =========================================================
        ctx.set(dut.i_u, 50000)
        ctx.set(dut.i_valid, 1)

        await ctx.tick()

        output = ctx.get(dut.o_u)
        sat = ctx.get(dut.o_sat)
        valid = ctx.get(dut.o_valid)


        assert output == 50000, \
            f"Normal pass failed: {output}"

        assert sat == 0, \
            "Saturation flag should be zero"

        assert valid == 1, \
            "Valid should propagate"


        # =========================================================
        # TEST 2:
        #
        # Positive saturation
        #
        # Input above maximum must clamp.
        # =========================================================

        ctx.set(dut.i_u, 200000)
        ctx.set(dut.i_valid, 1)

        await ctx.tick()

        output = ctx.get(dut.o_u)
        sat = ctx.get(dut.o_sat)

        assert output == 100000, \
            f"Upper clamp failed: {output}"

        assert sat == 1, \
            "Upper saturation flag missing"



        # =========================================================
        # TEST 3:
        #
        # Negative saturation
        # =========================================================

        ctx.set(dut.i_u, -200000)

        await ctx.tick()

        output = ctx.get(dut.o_u)
        sat = ctx.get(dut.o_sat)

        assert output == -100000, \
            f"Lower clamp failed: {output}"

        assert sat == 1, \
            "Lower saturation flag missing"



        # =========================================================
        # TEST 4:
        #
        # Invalid input
        #
        # The block should not advertise valid data.
        # =========================================================
        ctx.set(dut.i_valid, 0)

        await ctx.tick()

        valid = ctx.get(dut.o_valid)

        assert valid == 0, \
            "Invalid input should remove valid output"

    sim.add_testbench(process)

    with sim.write_vcd("output_limiter.vcd"):
        sim.run()

if __name__ == "__main__":
    test_output_limiter()