# test_pi_core.py
# Simulation testbench for PICore
# Run with: python sim/test_pi_core.py

from amaranth import *
from amaranth.sim import *
import sys
import os
import csv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rtl.dsp.pi_core import PICore

# Q3.14 gain helpers
GAIN_FRAC = 14
def to_q314(real_gain):
    return int(real_gain * (2 ** GAIN_FRAC))

KP_HALF  = to_q314(0.5)
KP_ONE   = to_q314(1.0)
KI_SMALL = to_q314(0.004)
ZERO_G   = 0


def test_pi_core():
    dut = PICore(err_w=20, out_w=16, gain_w=18, gain_frac=14, acc_w=40)

    pass_count = 0
    fail_count = 0
    csv_rows   = []

    def check(condition, test_name, detail=""):
        nonlocal pass_count, fail_count
        if condition:
            print(f"PASS: {test_name} | {detail}")
            pass_count += 1
        else:
            print(f"FAIL: {test_name} | {detail}")
            fail_count += 1

    async def testbench(ctx):

        # helper: send one error sample
        async def send_error(error):
            ctx.set(dut.error_in,    error)
            ctx.set(dut.error_valid, 1)
            await ctx.tick()
            ctx.set(dut.error_valid, 0)
            await ctx.tick()

        # helper: reset dut to clean state
        async def do_reset():
            ctx.set(dut.lock_enable,      0)
            ctx.set(dut.hold_enable,      0)
            ctx.set(dut.integrator_reset, 1)
            ctx.set(dut.integrator_load,  0)
            ctx.set(dut.error_valid,      0)
            ctx.set(dut.error_in,         0)
            ctx.set(dut.out_min,         -32767)
            ctx.set(dut.out_max,          32767)
            ctx.set(dut.out_safe,         0)
            await ctx.tick()
            ctx.set(dut.integrator_reset, 0)
            await ctx.tick()

        print("=== pi_core testbench starting ===")

        # ===================================================
        # Test 1: P-only response
        # ki=0, kp=1.0, error=100 -> output ~ 100
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_ONE)
        ctx.set(dut.ki,          ZERO_G)
        ctx.set(dut.lock_enable, 1)

        await send_error(100)
        out = ctx.get(dut.control_out)
        check(out > 0,
              "p_only_positive",
              f"output={out} should be positive for error=100")

        # ===================================================
        # Test 2: I-only accumulates over time
        # kp=0, ki=small, feed same error 10 times
        # output should grow each cycle
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          ZERO_G)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)

        outputs = []
        for _ in range(10):
            await send_error(100)
            outputs.append(ctx.get(dut.control_out))

        growing = all(outputs[i] <= outputs[i+1]
                      for i in range(len(outputs)-1))
        check(growing,
              "i_only_accumulates",
              f"outputs over 10 cycles: {outputs}")

        # save to csv for plotting
        for i, v in enumerate(outputs):
            csv_rows.append({"test": "i_only", "cycle": i, "output": v})

        # ===================================================
        # Test 3: PI positive step grows over time
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_HALF)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)

        for _ in range(5):
            await send_error(512)
        out_5 = ctx.get(dut.control_out)

        for _ in range(15):
            await send_error(512)
        out_20 = ctx.get(dut.control_out)

        check(out_20 > out_5,
              "pi_positive_step_grows",
              f"out@5={out_5} out@20={out_20}")

        # ===================================================
        # Test 4: Negative step gives negative output
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_HALF)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)

        for _ in range(20):
            await send_error(-512)
        out = ctx.get(dut.control_out)
        check(out < 0,
              "pi_negative_step",
              f"output={out} should be negative")

        # ===================================================
        # Test 5: Clamp high
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_ONE)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)
        ctx.set(dut.out_max,     100)

        for _ in range(30):
            await send_error(32767)
        out    = ctx.get(dut.control_out)
        sat_hi = ctx.get(dut.sat_hi)

        check(out == 100,
              "clamp_high",
              f"output={out} should be clamped to 100")
        check(sat_hi == 1,
              "sat_hi_flag",
              f"sat_hi={sat_hi} should be 1")

        ctx.set(dut.out_max, 32767)

        # ===================================================
        # Test 6: Clamp low
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_ONE)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)
        ctx.set(dut.out_min,    -100)

        for _ in range(30):
            await send_error(-32767)
        out    = ctx.get(dut.control_out)
        sat_lo = ctx.get(dut.sat_lo)

        check(out == -100,
              "clamp_low",
              f"output={out} should be clamped to -100")
        check(sat_lo == 1,
              "sat_lo_flag",
              f"sat_lo={sat_lo} should be 1")

        ctx.set(dut.out_min, -32767)

        # ===================================================
        # Test 7: Anti-windup
        # Drive into saturation then reverse error
        # Output should respond quickly
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_HALF)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)
        ctx.set(dut.out_max,     200)

        for _ in range(50):
            await send_error(32767)
        out_before = ctx.get(dut.control_out)

        for _ in range(5):
            await send_error(-512)
        out_after = ctx.get(dut.control_out)

        check(out_after < out_before,
              "anti_windup",
              f"output dropped from {out_before} to {out_after}")

        ctx.set(dut.out_max, 32767)

        # ===================================================
        # Test 8: Hold mode freezes output
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_ONE)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)

        for _ in range(5):
            await send_error(200)
        held_val = ctx.get(dut.control_out)

        ctx.set(dut.hold_enable, 1)
        for _ in range(5):
            await send_error(200)
        out = ctx.get(dut.control_out)

        check(out == held_val,
              "hold_mode",
              f"output stayed at {held_val} during hold")

        ctx.set(dut.hold_enable, 0)

        # ===================================================
        # Test 9: Integrator reset
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          ZERO_G)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 1)

        for _ in range(20):
            await send_error(200)

        ctx.set(dut.integrator_reset, 1)
        await ctx.tick()
        ctx.set(dut.integrator_reset, 0)

        await send_error(200)
        out = ctx.get(dut.control_out)
        check(out == 0,
              "integrator_reset",
              f"output={out} should be 0 after reset with kp=0")

        # ===================================================
        # Test 10: Zero gains give zero output
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          ZERO_G)
        ctx.set(dut.ki,          ZERO_G)
        ctx.set(dut.lock_enable, 1)

        for _ in range(5):
            await send_error(32767)
        out = ctx.get(dut.control_out)
        check(out == 0,
              "zero_gains",
              f"output={out} should be 0 with both gains zero")

        # ===================================================
        # Test 11: Lock disable outputs safe code
        # ===================================================
        await do_reset()
        ctx.set(dut.kp,          KP_ONE)
        ctx.set(dut.ki,          KI_SMALL)
        ctx.set(dut.lock_enable, 0)
        ctx.set(dut.out_safe,    66)

        await send_error(32767)
        out = ctx.get(dut.control_out)
        check(out == 66,
              "lock_disable_safe",
              f"output={out} should be safe code 66")

        # ===================================================
        # Summary
        # ===================================================
        print(f"\n=== RESULTS: {pass_count} passed, "
              f"{fail_count} failed ===")
        if fail_count == 0:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED - check output above")

        # save CSV for plotting
        os.makedirs("outputs/csv", exist_ok=True)
        with open("outputs/csv/pi_core_test.csv", "w", newline="") as f:
            writer = csv.DictWriter(f,
                         fieldnames=["test", "cycle", "output"])
            writer.writeheader()
            writer.writerows(csv_rows)
        print("\nCSV saved to outputs/csv/pi_core_test.csv")

    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(testbench)

    os.makedirs("outputs/waveforms", exist_ok=True)
    with sim.write_vcd("outputs/waveforms/pi_core.vcd"):
        sim.run()


if __name__ == "__main__":
    test_pi_core()
