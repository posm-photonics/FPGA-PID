# tb_error_calc.py
# Simulation testbench for ErrorCalc
# Run with: python sim/tb_error_calc.py

from amaranth import *
from amaranth.sim import *
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rtl.dsp.error_calc import ErrorCalc


def test_error_calc():
    dut = ErrorCalc(adc_w=16, err_w=20)

    pass_count = 0
    fail_count = 0

    def check(condition, test_name, detail=""):
        nonlocal pass_count, fail_count
        if condition:
            print(f"PASS: {test_name} | {detail}")
            pass_count += 1
        else:
            print(f"FAIL: {test_name} | {detail}")
            fail_count += 1

    async def testbench(ctx):

        # helper: apply one sample and read result after 1 cycle
        async def apply_and_check(sample, offset, setpoint,
                                   invert, expected, name):
            ctx.set(dut.sample_in,    sample)
            ctx.set(dut.offset,       offset)
            ctx.set(dut.setpoint,     setpoint)
            ctx.set(dut.invert_error, invert)
            ctx.set(dut.sample_valid, 1)

            await ctx.tick()  # registered output latency

            result = ctx.get(dut.error_out)
            valid  = ctx.get(dut.error_valid)

            check(valid == 1,
                  f"{name}_valid",
                  f"error_valid={valid} should be 1")
            check(result == expected,
                  name,
                  f"in={sample} off={offset} sp={setpoint} "
                  f"inv={invert} | got={result} expected={expected}")

            ctx.set(dut.sample_valid, 0)
            await ctx.tick()

        print("=== error_calc testbench starting ===")

        # Test 1: all zero
        await apply_and_check(0, 0, 0, 0, 0, "all_zero")

        # Test 2: offset subtraction only
        # 100 - 30 - 0 = 70
        await apply_and_check(100, 30, 0, 0, 70, "offset_subtract")

        # Test 3: setpoint subtraction only
        # 100 - 0 - 40 = 60
        await apply_and_check(100, 0, 40, 0, 60, "setpoint_subtract")

        # Test 4: both offset and setpoint
        # 100 - 30 - 40 = 30
        await apply_and_check(100, 30, 40, 0, 30, "offset_and_setpoint")

        # Test 5: polarity inversion
        # 100 inverted = -100
        await apply_and_check(100, 0, 0, 1, -100, "invert_positive")

        # Test 6: negative sample
        # -50 - 0 - 0 = -50
        await apply_and_check(-50, 0, 0, 0, -50, "negative_sample")

        # Test 7: negative sample inverted
        # -(-50) = +50
        await apply_and_check(-50, 0, 0, 1, 50, "negative_sample_inverted")

        # Test 8: negative offset
        # 0 - (-20) - 0 = +20
        await apply_and_check(0, -20, 0, 0, 20, "negative_offset")

        # Test 9: valid latency
        # sample_valid low -> error_valid should be low
        ctx.set(dut.sample_valid, 0)
        ctx.set(dut.sample_in, 999)
        await ctx.tick()
        valid = ctx.get(dut.error_valid)
        check(valid == 0,
              "valid_latency",
              f"error_valid={valid} should be 0 with no input valid")

        # Test 10: large positive value near ADC max
        # 30000 - 0 - 0 = 30000
        await apply_and_check(30000, 0, 0, 0, 30000, "large_positive")

        # Test 11: large negative value near ADC min
        # -30000 - 0 - 0 = -30000
        await apply_and_check(-30000, 0, 0, 0, -30000, "large_negative")

        # Test 12: offset larger than sample (result goes negative)
        # 10 - 50 - 0 = -40
        await apply_and_check(10, 50, 0, 0, -40, "offset_larger_than_sample")

        print(f"\n=== RESULTS: {pass_count} passed, {fail_count} failed ===")
        if fail_count == 0:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED - check output above")

    # run the simulation
    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(testbench)

    with sim.write_vcd("outputs/waveforms/error_calc.vcd"):
        sim.run()


if __name__ == "__main__":
    test_error_calc()
