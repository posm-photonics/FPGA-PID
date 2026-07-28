from amaranth import *
from amaranth.sim import *
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from rtl.dsp.error_calc import ErrorCalc


def test_error_calc():
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

    # -----------------------------------------------------------------
    # DUT 1: default widths (adc_w=16, err_w=20). At these widths the
    # SatMath stage (in_w=21, out_w=20) can never actually saturate,
    # since the widest possible err_raw (adc_w+2 = 18 bits, ~+/-98304)
    # sits well inside the +/-524288 clamp range. These tests confirm
    # the normal error-calc math still passes straight through the
    # saturator unchanged.
    # -----------------------------------------------------------------
    dut = ErrorCalc(adc_w=16, err_w=20)

    async def testbench(ctx):

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

        # Test 10: large positive value near ADC max (still far below
        # the +/-524288 saturation limit at err_w=20 -> passes through)
        # 30000 - 0 - 0 = 30000
        await apply_and_check(30000, 0, 0, 0, 30000, "large_positive")

        # Test 11: large negative value near ADC min (same headroom note)
        # -30000 - 0 - 0 = -30000
        await apply_and_check(-30000, 0, 0, 0, -30000, "large_negative")

        # Test 12: offset larger than sample (result goes negative)
        # 10 - 50 - 0 = -40
        await apply_and_check(10, 50, 0, 0, -40, "offset_larger_than_sample")

        # Test 13: max magnitude combination stays within +/-524288
        # 32767 - (-32768) - (-32768) = 98303 (still far under the limit)
        await apply_and_check(32767, -32768, -32768, 0, 98303,
                               "max_headroom_no_saturation")

    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(testbench)

    os.makedirs("outputs/waveforms", exist_ok=True)
    with sim.write_vcd("outputs/waveforms/error_calc.vcd"):
        sim.run()

    # -----------------------------------------------------------------
    # DUT 2: narrow err_w=8 (max_val=127, min_val=-128) specifically to
    # exercise the SatMath clamp, since it's unreachable at err_w=20.
    # -----------------------------------------------------------------
    dut_sat = ErrorCalc(adc_w=16, err_w=8)

    async def sat_testbench(ctx):

        async def apply_and_check_sat(sample, offset, setpoint,
                                       invert, expected, name):
            ctx.set(dut_sat.sample_in,    sample)
            ctx.set(dut_sat.offset,       offset)
            ctx.set(dut_sat.setpoint,     setpoint)
            ctx.set(dut_sat.invert_error, invert)
            ctx.set(dut_sat.sample_valid, 1)

            await ctx.tick()

            result = ctx.get(dut_sat.error_out)
            valid  = ctx.get(dut_sat.error_valid)

            check(valid == 1,
                  f"{name}_valid",
                  f"error_valid={valid} should be 1")
            check(result == expected,
                  name,
                  f"in={sample} off={offset} sp={setpoint} "
                  f"inv={invert} | got={result} expected={expected}")

            ctx.set(dut_sat.sample_valid, 0)
            await ctx.tick()

        print("\n=== error_calc saturation testbench starting (err_w=8) ===")

        # Test 14: positive overflow clamps to max_val (127)
        await apply_and_check_sat(30000, 0, 0, 0, 127, "sat_hi_no_invert")

        # Test 15: negative overflow clamps to min_val (-128)
        await apply_and_check_sat(-30000, 0, 0, 0, -128, "sat_lo_no_invert")

        # Test 16: inversion flips which rail gets hit
        # -30000 inverted -> +30000 -> saturates hi
        await apply_and_check_sat(-30000, 0, 0, 1, 127, "sat_hi_via_invert")

        # Test 17: 30000 inverted -> -30000 -> saturates lo
        await apply_and_check_sat(30000, 0, 0, 1, -128, "sat_lo_via_invert")

        # Test 18: exactly at max_val -> passthrough, no saturation
        await apply_and_check_sat(127, 0, 0, 0, 127,
                                   "boundary_passthrough_max")

        # Test 19: one past max_val -> saturates
        await apply_and_check_sat(128, 0, 0, 0, 127,
                                   "boundary_saturate_just_over")

        # Test 20: exactly at min_val -> passthrough, no saturation
        await apply_and_check_sat(-128, 0, 0, 0, -128,
                                   "boundary_passthrough_min")

        # Test 21: one past min_val -> saturates
        await apply_and_check_sat(-129, 0, 0, 0, -128,
                                   "boundary_saturate_just_under")

        print(f"\n=== RESULTS: {pass_count} passed, {fail_count} failed ===")
        if fail_count == 0:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED - check output above")

    sim2 = Simulator(dut_sat)
    sim2.add_clock(1e-8)
    sim2.add_testbench(sat_testbench)

    with sim2.write_vcd("outputs/waveforms/error_calc_sat.vcd"):
        sim2.run()


if __name__ == "__main__":
    test_error_calc()
