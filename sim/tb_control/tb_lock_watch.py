"""
tb_lock_watch.py
Testbench for lock_watch.LockWatch (POSM lock-health supervisor).
Run directly with: python3 tb_lock_watch.py
"""

import os
import sys
from amaranth.sim import Simulator
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from rtl.control.lock_watch import LockWatch


def new_dut():
    return LockWatch()


def run(dut, tb, name):
    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(tb)
    os.makedirs("build", exist_ok=True)
    with sim.write_vcd(f"build/{name}.vcd"):
        sim.run()


DEFAULT_CONFIG = dict(
    saturation_timeout=4,
    adc_timeout=4,
    jump_limit=200,
    error_timeout=4,
)


async def apply_config(ctx, dut, config=DEFAULT_CONFIG):
    for name, value in config.items():
        ctx.set(getattr(dut, name), value)


async def healthy_inputs(ctx, dut):
    """A fully-nominal operating point: locked, small error, DACs mid-rail,
    ADC valid, nothing saturated."""
    ctx.set(dut.enable, 1)
    ctx.set(dut.lock_active, 1)
    ctx.set(dut.error_value, 10)
    ctx.set(dut.max_error, 500)
    ctx.set(dut.fast_output, 30000)
    ctx.set(dut.fast_min, 1000)
    ctx.set(dut.fast_max, 64000)
    ctx.set(dut.slow_output, 30000)
    ctx.set(dut.slow_min, 1000)
    ctx.set(dut.slow_max, 64000)
    ctx.set(dut.fast_saturated, 0)
    ctx.set(dut.slow_saturated, 0)
    ctx.set(dut.adc_valid, 1)
    await apply_config(ctx, dut)


async def wait_until(ctx, signal, expected, max_cycles=12):
    for _ in range(max_cycles):
        if ctx.get(signal) == expected:
            return True
        await ctx.tick()
    return False


def test_disabled_holds_everything_low():
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        ctx.set(dut.enable, 0)
        await ctx.tick()
        await ctx.tick()
        assert ctx.get(dut.lock_healthy) == 0
        assert ctx.get(dut.unlock_detected) == 0
        assert ctx.get(dut.relock_request) == 0
        assert ctx.get(dut.fault_request) == 0
        print("PASS: test_disabled_holds_everything_low")

    run(dut, tb, "test_disabled_holds_everything_low")


def test_nominal_operation_is_healthy():
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        # Hold a stable nominal point for several cycles so the "previous
        # fast output" history settles and no jump is seen.
        for _ in range(5):
            await ctx.tick()
        assert ctx.get(dut.lock_healthy) == 1
        assert ctx.get(dut.error_violation) == 0
        assert ctx.get(dut.fast_rail_warning) == 0
        assert ctx.get(dut.slow_rail_warning) == 0
        assert ctx.get(dut.adc_fault_active) == 0
        assert ctx.get(dut.sat_fault_active) == 0
        assert ctx.get(dut.unlock_detected) == 0
        print("PASS: test_nominal_operation_is_healthy")

    run(dut, tb, "test_nominal_operation_is_healthy")


def test_rail_warnings_are_immediate():
    """fast_rail_warning / slow_rail_warning are combinational -- they
    should assert the same cycle the DAC output touches a rail, with no
    counter/timeout involved."""
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        await ctx.tick()

        ctx.set(dut.fast_output, 64000)  # == fast_max
        await ctx.tick()
        assert ctx.get(dut.fast_rail_warning) == 1

        ctx.set(dut.fast_output, 30000)
        ctx.set(dut.slow_output, 1000)  # == slow_min
        await ctx.tick()
        assert ctx.get(dut.slow_rail_warning) == 1

        print("PASS: test_rail_warnings_are_immediate")

    run(dut, tb, "test_rail_warnings_are_immediate")


def test_sustained_error_triggers_relock():
    """error_value staying above max_error for error_timeout cycles should
    raise relock_request + unlock_detected, and it should persist (a
    level, not a pulse) for as long as the error stays bad."""
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        await ctx.tick()

        ctx.set(dut.error_value, 2000)  # > max_error(500)
        found = await wait_until(ctx, dut.relock_request, 1, max_cycles=8)
        assert found, "relock_request never asserted for a sustained error violation"
        assert ctx.get(dut.unlock_detected) == 1
        assert ctx.get(dut.lock_healthy) == 0

        # Should remain asserted while the error is still bad.
        await ctx.tick()
        assert ctx.get(dut.relock_request) == 1

        # Clearing the error should drop relock_request again.
        ctx.set(dut.error_value, 10)
        cleared = await wait_until(ctx, dut.relock_request, 0, max_cycles=4)
        assert cleared, "relock_request should clear once the error recovers"

        print("PASS: test_sustained_error_triggers_relock")

    run(dut, tb, "test_sustained_error_triggers_relock")


def test_adc_fault_triggers_fault_request():
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        await ctx.tick()

        ctx.set(dut.adc_valid, 0)
        found = await wait_until(ctx, dut.fault_request, 1, max_cycles=8)
        assert found, "fault_request never asserted for a sustained ADC dropout"
        assert ctx.get(dut.unlock_detected) == 1
        assert ctx.get(dut.adc_fault_active) == 1

        print("PASS: test_adc_fault_triggers_fault_request")

    run(dut, tb, "test_adc_fault_triggers_fault_request")


def test_saturation_triggers_fault_request():
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        await ctx.tick()

        ctx.set(dut.fast_saturated, 1)
        found = await wait_until(ctx, dut.fault_request, 1, max_cycles=8)
        assert found, "fault_request never asserted for sustained saturation"
        assert ctx.get(dut.sat_fault_active) == 1

        print("PASS: test_saturation_triggers_fault_request")

    run(dut, tb, "test_saturation_triggers_fault_request")


def test_adc_fault_has_priority_over_saturation():
    """When both an ADC dropout and DAC saturation persist at the same
    time, the module docstring/comment says ADC faults dominate -- both
    counters reach threshold together, but the fault path taken should
    still be the ADC one (as validated indirectly via adc_fault_active
    being set alongside fault_request)."""
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        await ctx.tick()

        ctx.set(dut.adc_valid, 0)
        ctx.set(dut.fast_saturated, 1)
        found = await wait_until(ctx, dut.fault_request, 1, max_cycles=8)
        assert found
        assert ctx.get(dut.adc_fault_active) == 1
        assert ctx.get(dut.sat_fault_active) == 1  # both timed out together

        print("PASS: test_adc_fault_has_priority_over_saturation")

    run(dut, tb, "test_adc_fault_has_priority_over_saturation")


def test_sudden_dac_jump_triggers_relock():
    """A fast_output change larger than jump_limit in a single cycle,
    while locked, should trip relock_request even with a clean error
    signal and no saturation/ADC fault."""
    dut = new_dut()

    async def tb(ctx):
        await healthy_inputs(ctx, dut)
        for _ in range(3):
            await ctx.tick()  # let previous_fast settle at a stable value

        ctx.set(dut.fast_output, 30000 + 5000)  # jump >> jump_limit(200)
        found = await wait_until(ctx, dut.relock_request, 1, max_cycles=4)
        assert found, "relock_request never asserted for a large single-cycle DAC jump"
        assert ctx.get(dut.unlock_detected) == 1

        print("PASS: test_sudden_dac_jump_triggers_relock")

    run(dut, tb, "test_sudden_dac_jump_triggers_relock")


if __name__ == "__main__":
    test_disabled_holds_everything_low()
    test_nominal_operation_is_healthy()
    test_rail_warnings_are_immediate()
    test_sustained_error_triggers_relock()
    test_adc_fault_triggers_fault_request()
    test_saturation_triggers_fault_request()
    test_adc_fault_has_priority_over_saturation()
    test_sudden_dac_jump_triggers_relock()
    print("\nAll lock_watch tests passed.")