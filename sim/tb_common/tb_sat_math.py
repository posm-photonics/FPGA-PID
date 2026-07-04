# tb_sat_math.py
# Testbench for SatMath module and sat_math utility functions
#
# Tests:
#   Python functions: sat, sat_18to16, sat_40to16,
#                     real_to_q314, q314_to_real
#   Hardware module:  SatMath 18->16 and 40->16
#
# Run with: python sim/tb_sat_math.py

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from amaranth import *
from amaranth.sim import *
from rtl.common.sat_math import (
    SatMath,
    sat,
    sat_18to16,
    sat_40to16,
    sat_40to18,
    real_to_q314,
    q314_to_real,
)


def test_sat_math():

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

    print("=== sat_math testbench starting ===")

    # ===================================================
    # Section 1: Python sat() function
    # These run instantly with no simulation needed
    # ===================================================
    print("\n--- Python sat() function ---")

    # positive overflow
    check(sat(40000, 16) == 32767,
          "sat_pos_overflow",
          f"sat(40000,16)={sat(40000,16)} should be 32767")

    # negative overflow
    check(sat(-40000, 16) == -32768,
          "sat_neg_overflow",
          f"sat(-40000,16)={sat(-40000,16)} should be -32768")

    # zero passthrough
    check(sat(0, 16) == 0,
          "sat_zero",
          f"sat(0,16)={sat(0,16)} should be 0")

    # positive passthrough
    check(sat(100, 16) == 100,
          "sat_pos_passthrough",
          f"sat(100,16)={sat(100,16)} should be 100")

    # negative passthrough
    check(sat(-100, 16) == -100,
          "sat_neg_passthrough",
          f"sat(-100,16)={sat(-100,16)} should be -100")

    # exact max boundary — should pass through not clamp
    check(sat(32767, 16) == 32767,
          "sat_exact_max",
          f"sat(32767,16)={sat(32767,16)} should be 32767")

    # exact min boundary — should pass through not clamp
    check(sat(-32768, 16) == -32768,
          "sat_exact_min",
          f"sat(-32768,16)={sat(-32768,16)} should be -32768")

    # one above max — should clamp
    check(sat(32768, 16) == 32767,
          "sat_one_above_max",
          f"sat(32768,16)={sat(32768,16)} should be 32767")

    # one below min — should clamp
    check(sat(-32769, 16) == -32768,
          "sat_one_below_min",
          f"sat(-32769,16)={sat(-32769,16)} should be -32768")

    # ===================================================
    # Section 2: Convenience wrappers
    # ===================================================
    print("\n--- Convenience wrappers ---")

    check(sat_18to16(32768)  == 32767,
          "sat_18to16_overflow",
          f"sat_18to16(32768)={sat_18to16(32768)}")

    check(sat_18to16(-32769) == -32768,
          "sat_18to16_underflow",
          f"sat_18to16(-32769)={sat_18to16(-32769)}")

    check(sat_18to16(100) == 100,
          "sat_18to16_passthrough",
          f"sat_18to16(100)={sat_18to16(100)}")

    check(sat_40to16(999999) == 32767,
          "sat_40to16_overflow",
          f"sat_40to16(999999)={sat_40to16(999999)}")

    check(sat_40to16(-999999) == -32768,
          "sat_40to16_underflow",
          f"sat_40to16(-999999)={sat_40to16(-999999)}")

    check(sat_40to18(200000) == 131071,
          "sat_40to18_overflow",
          f"sat_40to18(200000)={sat_40to18(200000)} "
          f"should be 131071 (2^17-1)")

    # ===================================================
    # Section 3: Q3.14 gain conversion
    # ===================================================
    print("\n--- Q3.14 gain conversion ---")

    check(real_to_q314(0.5) == 8192,
          "q314_half",
          f"real_to_q314(0.5)={real_to_q314(0.5)} should be 8192")

    check(real_to_q314(1.0) == 16384,
          "q314_one",
          f"real_to_q314(1.0)={real_to_q314(1.0)} should be 16384")

    check(real_to_q314(2.0) == 32768,
          "q314_two",
          f"real_to_q314(2.0)={real_to_q314(2.0)} should be 32768")

    check(real_to_q314(0.0) == 0,
          "q314_zero",
          f"real_to_q314(0.0)={real_to_q314(0.0)} should be 0")

    check(real_to_q314(-0.5) == -8192,
          "q314_neg_half",
          f"real_to_q314(-0.5)={real_to_q314(-0.5)} should be -8192")

    check(abs(q314_to_real(8192) - 0.5) < 1e-6,
          "q314_to_real_half",
          f"q314_to_real(8192)={q314_to_real(8192)} should be 0.5")

    check(abs(q314_to_real(16384) - 1.0) < 1e-6,
          "q314_to_real_one",
          f"q314_to_real(16384)={q314_to_real(16384)} should be 1.0")

    # roundtrip: real -> q314 -> real should recover original
    for gain in [0.5, 1.0, 0.004, 0.001, -0.5, -1.0]:
        recovered = q314_to_real(real_to_q314(gain))
        error     = abs(recovered - gain)
        check(error < 0.001,
              f"q314_roundtrip_{gain}",
              f"gain={gain} recovered={recovered:.6f} "
              f"error={error:.6f}")

    # ===================================================
    # Section 4: Hardware SatMath module simulation
    # ===================================================
    print("\n--- Hardware SatMath module (18->16) ---")

    dut = SatMath(in_w=18, out_w=16)

    hw_pass = 0
    hw_fail = 0

    async def hw_testbench(ctx):
        nonlocal hw_pass, hw_fail

        def hw_check(condition, name, detail=""):
            nonlocal hw_pass, hw_fail
            if condition:
                print(f"PASS: {name} | {detail}")
                hw_pass += 1
            else:
                print(f"FAIL: {name} | {detail}")
                hw_fail += 1

        async def apply_and_check(in_val, expected_out,
                                   expected_hi, expected_lo,
                                   name):
            ctx.set(dut.value_in, in_val)
            await ctx.tick()
            out    = ctx.get(dut.value_out)
            sat_hi = ctx.get(dut.sat_hi)
            sat_lo = ctx.get(dut.sat_lo)

            hw_check(out    == expected_out,
                     f"hw_{name}_out",
                     f"in={in_val} out={out} "
                     f"expected={expected_out}")
            hw_check(sat_hi == expected_hi,
                     f"hw_{name}_sat_hi",
                     f"sat_hi={sat_hi} expected={expected_hi}")
            hw_check(sat_lo == expected_lo,
                     f"hw_{name}_sat_lo",
                     f"sat_lo={sat_lo} expected={expected_lo}")

        # positive overflow
        await apply_and_check(32768,   32767,  1, 0, "pos_overflow")

        # negative overflow
        await apply_and_check(-32769, -32768,  0, 1, "neg_overflow")

        # zero passthrough
        await apply_and_check(0,       0,      0, 0, "zero")

        # positive passthrough
        await apply_and_check(100,     100,    0, 0, "pos_passthrough")

        # negative passthrough
        await apply_and_check(-100,   -100,    0, 0, "neg_passthrough")

        # exact max — no saturation
        await apply_and_check(32767,   32767,  0, 0, "exact_max")

        # exact min — no saturation
        await apply_and_check(-32768, -32768,  0, 0, "exact_min")

        # one above max
        await apply_and_check(32768,   32767,  1, 0, "one_above_max")

        # one below min
        await apply_and_check(-32769, -32768,  0, 1, "one_below_min")

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(hw_testbench)

    os.makedirs("outputs/waveforms", exist_ok=True)
    with sim.write_vcd("outputs/waveforms/sat_math.vcd"):
        sim.run()

    pass_count += hw_pass
    fail_count += hw_fail

    # ===================================================
    # Summary
    # ===================================================
    print(f"\n=== RESULTS: {pass_count} passed, "
          f"{fail_count} failed ===")
    if fail_count == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED - check output above")


if __name__ == "__main__":
    test_sat_math()
