from amaranth import *
from amaranth.sim import Simulator

from rtl.control.fault_gate import FaultGate



def test_fault_gate():
    SAFE_CODE = -12345

    dut = FaultGate(
        width=24,
        SAFE_CODE=SAFE_CODE
    )

    sim = Simulator(dut)

    sim.add_clock(1e-6)

    async def process(ctx):
        # =========================================================
        # TEST 1:
        #
        # Normal operation
        #
        # No fault -> output follows input
        # =========================================================
        ctx.set(dut.i_u, 50000)
        ctx.set(dut.i_fault, 0)
        ctx.set(dut.i_valid, 1)

        await ctx.tick()

        output = ctx.get(dut.o_u)
        valid = ctx.get(dut.o_valid)

        assert output == 50000, \
            f"Fault gate pass-through failed: {output}"

        assert valid == 1, \
            "Valid output missing"

        # =========================================================
        # TEST 2:
        #
        # Active fault
        #
        # Safety stage must override controller output.
        #
        # =========================================================
        ctx.set(dut.i_u, 90000)
        ctx.set(dut.i_fault, 1)
        ctx.set(dut.i_valid, 1)

        await ctx.tick()

        output = ctx.get(dut.o_u)

        assert output == SAFE_CODE, \
            f"Fault safe code failed: {output}"
        # =========================================================
        # TEST 3:
        #
        # Fault recovery
        #
        # Clearing fault should restore normal operation.
        # =========================================================
        ctx.set(dut.i_fault, 0)
        ctx.set(dut.i_u, 321)

        await ctx.tick()

        output = ctx.get(dut.o_u)

        assert output == 321, \
            "Fault recovery failed"

        # =========================================================
        # TEST 4:
        #
        # Invalid input
        #
        # =========================================================
        ctx.set(dut.i_valid, 0)

        await ctx.tick()

        valid = ctx.get(dut.o_valid)

        assert valid == 0, \
            "Invalid input should disable output valid"

    sim.add_testbench(process)

    with sim.write_vcd("fault_gate.vcd"):
        sim.run()
        
if __name__ == "__main__":
    test_fault_gate()