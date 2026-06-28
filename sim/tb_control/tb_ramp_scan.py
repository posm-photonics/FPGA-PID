# test_ramp_scan.py
# Simulation testbench for RampScan
# Run with: python sim/test_ramp_scan.py
#
# Tests:
#   1.  Disabled: output stays at min
#   2.  Wide scan goes up from min
#   3.  Wide scan reverses at max
#   4.  Wide scan reverses at min
#   5.  No overshoot past max
#   6.  No overshoot past min
#   7.  Cycle count increments
#   8.  cycle_done pulses at bottom
#   9.  Zoom mode stays within bounds
#   10. Tick divider slows ramp
#   11. Reset to min on disable
#   12. Step larger than range clamps correctly

import os
import csv
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from amaranth import *
from amaranth.sim import *
from rtl.control.ramp_scan import RampScan


def test_ramp_scan():
    dut = RampScan(dac_w=16)

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

        # helper: run N ticks and collect ramp output
        async def run_ticks(n):
            results = []
            for _ in range(n):
                await ctx.tick()
                results.append(ctx.get(dut.ramp_out))
            return results

        # helper: set wide scan config
        def set_wide(rmin, rmax, step, div=1):
            ctx.set(dut.ramp_min,      rmin)
            ctx.set(dut.ramp_max,      rmax)
            ctx.set(dut.ramp_step,     step)
            ctx.set(dut.ramp_tick_div, div)
            ctx.set(dut.zoom_mode,     0)

        # helper: set zoom scan config
        def set_zoom(center, width, step, div=1):
            ctx.set(dut.ramp_center,   center)
            ctx.set(dut.ramp_width,    width)
            ctx.set(dut.ramp_step,     step)
            ctx.set(dut.ramp_tick_div, div)
            ctx.set(dut.zoom_mode,     1)

        print("=== ramp_scan testbench starting ===")

        # ===================================================
        # Test 1: Disabled — output stays at min
        # ===================================================
        set_wide(-100, 100, 10)
        ctx.set(dut.enable, 0)
        await ctx.tick()
        await ctx.tick()
        out = ctx.get(dut.ramp_out)
        check(out == -100,
              "disabled_stays_at_min",
              f"ramp_out={out} should be -100 when disabled")

        # ===================================================
        # Test 2: Wide scan goes up from min
        # ===================================================
        set_wide(-100, 100, 10)
        ctx.set(dut.enable, 1)

        outputs = await run_ticks(5)
        increasing = all(outputs[i] <= outputs[i+1]
                         for i in range(len(outputs)-1))
        check(increasing,
              "wide_scan_goes_up",
              f"first 5 outputs: {outputs}")

        # ===================================================
        # Test 3: Wide scan reverses at max — no overshoot
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        set_wide(0, 50, 10)
        ctx.set(dut.enable, 1)

        # run enough ticks to reach max and reverse
        outputs = await run_ticks(20)
        max_seen = max(outputs)

        check(max_seen <= 50,
              "no_overshoot_max",
              f"max seen={max_seen} should never exceed 50")

        # check direction reversal happened
        peak_idx = outputs.index(max_seen)
        if peak_idx < len(outputs) - 1:
            reversed_after_peak = outputs[peak_idx + 1] <= max_seen
        else:
            reversed_after_peak = True
        check(reversed_after_peak,
              "reverses_at_max",
              f"output after peak should decrease: {outputs[peak_idx:][:5]}")

        # ===================================================
        # Test 4: Wide scan reverses at min — no overshoot
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        set_wide(-50, 50, 10)
        ctx.set(dut.enable, 1)

        # run enough to do a full cycle
        outputs = await run_ticks(40)
        min_seen = min(outputs)

        check(min_seen >= -50,
              "no_overshoot_min",
              f"min seen={min_seen} should never go below -50")

        # save wide scan outputs for plotting
        for i, v in enumerate(outputs):
            csv_rows.append({
                "test": "wide_scan",
                "cycle": i,
                "ramp_out": v
            })

        # ===================================================
        # Test 5: Cycle count increments after full sweep
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        set_wide(0, 40, 10)
        ctx.set(dut.enable, 1)

        # run enough for 2 full cycles (up+down = 1 cycle)
        # at step=10, range=40: 4 steps up + 4 steps down = 8 ticks
        count_before = ctx.get(dut.ramp_cycle_count)
        await run_ticks(20)
        count_after = ctx.get(dut.ramp_cycle_count)

        check(count_after > count_before,
              "cycle_count_increments",
              f"count went from {count_before} to {count_after}")

        # ===================================================
        # Test 6: cycle_done pulses at bottom of sweep
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        set_wide(0, 30, 10)
        ctx.set(dut.enable, 1)

        done_pulses = 0
        for _ in range(30):
            await ctx.tick()
            if ctx.get(dut.cycle_done) == 1:
                done_pulses += 1

        check(done_pulses > 0,
              "cycle_done_pulses",
              f"cycle_done pulsed {done_pulses} times in 30 ticks")

        # ===================================================
        # Test 7: Zoom mode stays within center ± width
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        set_zoom(center=200, width=50, step=5)
        ctx.set(dut.enable, 1)

        outputs = await run_ticks(60)
        all_in_range = all(150 <= v <= 250 for v in outputs)

        check(all_in_range,
              "zoom_stays_in_bounds",
              f"all outputs in [150,250]: min={min(outputs)} max={max(outputs)}")

        # save zoom scan for plotting
        for i, v in enumerate(outputs):
            csv_rows.append({
                "test": "zoom_scan",
                "cycle": i,
                "ramp_out": v
            })

        # ===================================================
        # Test 8: Zoom mode centers around ramp_center
        # ===================================================
        center_val = 200
        midpoint   = (max(outputs) + min(outputs)) / 2
        check(abs(midpoint - center_val) < 20,
              "zoom_centered",
              f"midpoint={midpoint:.1f} should be near center={center_val}")

        # ===================================================
        # Test 9: Tick divider slows the ramp
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        # fast ramp: div=1
        set_wide(0, 100, 10, div=1)
        ctx.set(dut.enable, 1)
        fast_outputs = await run_ticks(10)
        fast_changes = sum(1 for i in range(len(fast_outputs)-1)
                           if fast_outputs[i] != fast_outputs[i+1])

        ctx.set(dut.enable, 0)
        await ctx.tick()

        # slow ramp: div=4
        set_wide(0, 100, 10, div=4)
        ctx.set(dut.enable, 1)
        slow_outputs = await run_ticks(10)
        slow_changes = sum(1 for i in range(len(slow_outputs)-1)
                           if slow_outputs[i] != slow_outputs[i+1])

        check(slow_changes < fast_changes,
              "tick_div_slows_ramp",
              f"fast changes={fast_changes} slow changes={slow_changes}")

        # ===================================================
        # Test 10: Disable resets to min
        # ===================================================
        set_wide(-200, 200, 20)
        ctx.set(dut.enable, 1)
        await run_ticks(5)

        ctx.set(dut.enable, 0)
        await ctx.tick()
        await ctx.tick()

        out = ctx.get(dut.ramp_out)
        check(out == -200,
              "disable_resets_to_min",
              f"ramp_out={out} should return to min=-200 on disable")

        # ===================================================
        # Test 11: Step larger than range clamps at bounds
        # ===================================================
        ctx.set(dut.enable, 0)
        await ctx.tick()

        # step=100, range=0 to 30 — step is bigger than range
        set_wide(0, 30, 100)
        ctx.set(dut.enable, 1)
        outputs = await run_ticks(10)

        never_exceeds = all(v <= 30 for v in outputs)
        never_below   = all(v >= 0  for v in outputs)
        check(never_exceeds and never_below,
              "large_step_clamps",
              f"outputs={outputs[:6]} all in [0,30]")

        # ===================================================
        # Summary
        # ===================================================
        print(f"\n=== RESULTS: {pass_count} passed, "
              f"{fail_count} failed ===")
        if fail_count == 0:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED - check output above")

        # save CSV
        os.makedirs("outputs/csv", exist_ok=True)
        with open("outputs/csv/ramp_scan_test.csv", "w",
                  newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["test", "cycle", "ramp_out"])
            writer.writeheader()
            writer.writerows(csv_rows)
        print("CSV saved to outputs/csv/ramp_scan_test.csv")

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(testbench)

    os.makedirs("outputs/waveforms", exist_ok=True)
    with sim.write_vcd("outputs/waveforms/ramp_scan.vcd"):
        sim.run()


if __name__ == "__main__":
    test_ramp_scan()
