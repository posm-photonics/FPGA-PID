"""
tb_lock_fsm.py -- Amaranth testbench for lock_fsm.py

Mirrors the minimum test list given for tb_lock_fsm.sv in the onboarding
packet (Section 12.4): legal transitions, illegal commands, fault priority,
explicit clear.

Requires: amaranth >= 0.5 (uses the async testbench API: `ctx.tick()`,
`ctx.set()`, `ctx.get()`). If you're on amaranth 0.4 or earlier, swap the
`async def ...(ctx)` testbenches for the older generator-based style:

    def testbench():
        yield dut.global_enable.eq(1)
        yield
        result = yield dut.state_code
    sim.add_sync_process(testbench)

Run with:
    pip install amaranth
    python tb_lock_fsm.py
"""

from amaranth.sim import Simulator
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from rtl.control.lock_fsm import LockFSM, LockState

FAILURES = []


def check(label, got, want):
    ok = (got == want)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILURES.append(label)


async def pulse(ctx, sig):
    ctx.set(sig, 1)
    await ctx.tick()
    ctx.set(sig, 0)


async def test_reset_and_idle(ctx, dut):
    state = ctx.get(dut.state_code)
    check("reset state is IDLE", state, int(LockState.IDLE))
    check("reset fast_output_hold asserted", ctx.get(dut.fast_output_hold), 1)
    check("reset slow_output_hold asserted", ctx.get(dut.slow_output_hold), 1)
    check("reset lock_enable deasserted", ctx.get(dut.lock_enable), 0)
    check("reset fault_active deasserted", ctx.get(dut.fault_active), 0)


async def test_happy_path(ctx, dut):
    # IDLE -> WIDE_SCAN
    ctx.set(dut.global_enable, 1)
    ctx.set(dut.autolock_enable, 1)
    await ctx.tick()
    check("IDLE->WIDE_SCAN", ctx.get(dut.state_code), int(LockState.WIDE_SCAN))
    check("WIDE_SCAN asserts wide_scan_en", ctx.get(dut.wide_scan_en), 1)
    check("WIDE_SCAN releases slow_output_hold", ctx.get(dut.slow_output_hold), 0)
    check("WIDE_SCAN still holds fast_output_hold", ctx.get(dut.fast_output_hold), 1)

    # WIDE_SCAN -> TRACE_READY
    ctx.set(dut.trace_ready, 1)
    await ctx.tick()
    ctx.set(dut.trace_ready, 0)
    check("WIDE_SCAN->TRACE_READY", ctx.get(dut.state_code), int(LockState.TRACE_READY))

    # TRACE_READY -> USER_SELECT (autolock_enable already held high)
    await ctx.tick()
    check("TRACE_READY->USER_SELECT", ctx.get(dut.state_code), int(LockState.USER_SELECT))

    # USER_SELECT -> ZOOM_SCAN
    ctx.set(dut.descriptor_ready, 1)
    ctx.set(dut.lock_enable_request, 1)
    await ctx.tick()
    ctx.set(dut.descriptor_ready, 0)
    check("USER_SELECT->ZOOM_SCAN", ctx.get(dut.state_code), int(LockState.ZOOM_SCAN))
    check("ZOOM_SCAN asserts autolock_start", ctx.get(dut.autolock_start), 1)

    # ZOOM_SCAN -> FEATURE_VERIFY
    ctx.set(dut.zoom_done, 1)
    await ctx.tick()
    ctx.set(dut.zoom_done, 0)
    check("ZOOM_SCAN->FEATURE_VERIFY", ctx.get(dut.state_code), int(LockState.FEATURE_VERIFY))

    # FEATURE_VERIFY -> ARM_LOCK
    ctx.set(dut.feature_match, 1)
    await ctx.tick()
    ctx.set(dut.feature_match, 0)
    check("FEATURE_VERIFY->ARM_LOCK", ctx.get(dut.state_code), int(LockState.ARM_LOCK))

    # ARM_LOCK cycle 0: integrator_reset pulses, still in ARM_LOCK
    check("ARM_LOCK cycle0 integrator_reset", ctx.get(dut.integrator_reset), 1)
    check("ARM_LOCK cycle0 integrator_load low", ctx.get(dut.integrator_load), 0)
    await ctx.tick()
    check("ARM_LOCK stays for cycle 1", ctx.get(dut.state_code), int(LockState.ARM_LOCK))

    # ARM_LOCK cycle 1: integrator_load pulses, then -> LOCKED
    check("ARM_LOCK cycle1 integrator_load", ctx.get(dut.integrator_load), 1)
    check("ARM_LOCK cycle1 integrator_reset low", ctx.get(dut.integrator_reset), 0)
    await ctx.tick()
    check("ARM_LOCK->LOCKED", ctx.get(dut.state_code), int(LockState.LOCKED))
    check("LOCKED releases fast_output_hold", ctx.get(dut.fast_output_hold), 0)
    check("LOCKED asserts lock_enable", ctx.get(dut.lock_enable), 1)
    check("LOCKED still holds slow_output_hold", ctx.get(dut.slow_output_hold), 1)

    # LOCKED -> LOCK_WATCH
    ctx.set(dut.lock_check_pass, 1)
    await ctx.tick()
    ctx.set(dut.lock_check_pass, 0)
    check("LOCKED->LOCK_WATCH", ctx.get(dut.state_code), int(LockState.LOCK_WATCH))
    check("LOCK_WATCH releases slow_output_hold", ctx.get(dut.slow_output_hold), 0)
    check("LOCK_WATCH still locked", ctx.get(dut.locked), 1)


async def test_feature_verify_retry(ctx, dut):
    # From LOCK_WATCH, force an unlock -> RELOCK_SCAN -> zoom_done -> FEATURE_VERIFY
    ctx.set(dut.lock_check_fail, 1)
    await ctx.tick()
    ctx.set(dut.lock_check_fail, 0)
    check("LOCK_WATCH->RELOCK_SCAN on lock_check_fail",
          ctx.get(dut.state_code), int(LockState.RELOCK_SCAN))
    check("relock_pulse strobed on entry", ctx.get(dut.relock_pulse), 1)
    await ctx.tick()
    check("relock_pulse is one cycle only", ctx.get(dut.relock_pulse), 0)

    ctx.set(dut.zoom_done, 1)
    await ctx.tick()
    ctx.set(dut.zoom_done, 0)
    check("RELOCK_SCAN->FEATURE_VERIFY", ctx.get(dut.state_code), int(LockState.FEATURE_VERIFY))

    # feature_fail without retry_exceeded -> RELOCK_SCAN (retry), not FAULT
    ctx.set(dut.feature_fail, 1)
    await ctx.tick()
    ctx.set(dut.feature_fail, 0)
    check("FEATURE_VERIFY fail (retry available) -> RELOCK_SCAN",
          ctx.get(dut.state_code), int(LockState.RELOCK_SCAN))

    # get back to FEATURE_VERIFY, then exhaust retries -> FAULT
    ctx.set(dut.zoom_done, 1)
    await ctx.tick()
    ctx.set(dut.zoom_done, 0)
    check("back in FEATURE_VERIFY", ctx.get(dut.state_code), int(LockState.FEATURE_VERIFY))

    ctx.set(dut.feature_fail, 1)
    ctx.set(dut.retry_exceeded, 1)
    await ctx.tick()
    ctx.set(dut.feature_fail, 0)
    ctx.set(dut.retry_exceeded, 0)
    check("FEATURE_VERIFY fail + retry_exceeded -> FAULT",
          ctx.get(dut.state_code), int(LockState.FAULT))
    check("autolock_failed sticky set", ctx.get(dut.autolock_failed), 1)

    # explicit clear back to IDLE
    ctx.set(dut.fault_clear_request, 1)
    await ctx.tick()
    ctx.set(dut.fault_clear_request, 0)
    check("explicit clear FAULT->IDLE", ctx.get(dut.state_code), int(LockState.IDLE))
    check("autolock_failed cleared", ctx.get(dut.autolock_failed), 0)


async def test_fault_priority(ctx, dut):
    # Drive back into WIDE_SCAN, then inject a fault mid-scan: it must win
    # immediately regardless of any other requested transition.
    ctx.set(dut.global_enable, 1)
    ctx.set(dut.autolock_enable, 1)
    await ctx.tick()
    check("re-enter WIDE_SCAN", ctx.get(dut.state_code), int(LockState.WIDE_SCAN))

    # Simultaneously request trace_ready (would normally advance the FSM)
    # AND assert a fault: fault must win.
    ctx.set(dut.trace_ready, 1)
    ctx.set(dut.fault_in, 1)
    await ctx.tick()
    ctx.set(dut.trace_ready, 0)
    check("fault preempts a legal transition", ctx.get(dut.state_code), int(LockState.FAULT))
    check("FAULT forces fast_output_hold", ctx.get(dut.fast_output_hold), 1)
    check("FAULT forces slow_output_hold", ctx.get(dut.slow_output_hold), 1)
    check("FAULT forces lock_enable low", ctx.get(dut.lock_enable), 0)

    # Fault condition clears on the wire, but sticky must hold without an
    # explicit clear request (illegal/ignored command: hold_request in FAULT).
    ctx.set(dut.fault_in, 0)
    ctx.set(dut.hold_request, 1)
    await ctx.tick()
    ctx.set(dut.hold_request, 0)
    check("sticky fault ignores hold_request, stays FAULT",
          ctx.get(dut.state_code), int(LockState.FAULT))

    # Clear request while fault_in is still 0 -> back to IDLE.
    ctx.set(dut.fault_clear_request, 1)
    await ctx.tick()
    ctx.set(dut.fault_clear_request, 0)
    check("explicit clear releases FAULT", ctx.get(dut.state_code), int(LockState.IDLE))


async def test_hold_and_illegal_resume(ctx, dut):
    ctx.set(dut.global_enable, 1)
    ctx.set(dut.autolock_enable, 1)
    await ctx.tick()
    check("WIDE_SCAN entered", ctx.get(dut.state_code), int(LockState.WIDE_SCAN))

    ctx.set(dut.hold_request, 1)
    await ctx.tick()
    check("WIDE_SCAN->HOLD", ctx.get(dut.state_code), int(LockState.HOLD))
    check("HOLD forces fast_output_hold", ctx.get(dut.fast_output_hold), 1)
    check("HOLD forces slow_output_hold", ctx.get(dut.slow_output_hold), 1)

    ctx.set(dut.hold_request, 0)
    await ctx.tick()
    # Must land in IDLE, never jump straight back to WIDE_SCAN/LOCKED on its own.
    check("HOLD release goes to IDLE, not silent resume",
          ctx.get(dut.state_code), int(LockState.IDLE))
    ctx.set(dut.global_enable, 0)
    ctx.set(dut.autolock_enable, 0)
    await ctx.tick()


def build_and_run():
    dut = LockFSM()
    sim = Simulator(dut)
    sim.add_clock(1e-6)  # 1 MHz sim clock; unrelated to the 1 MHz lock bandwidth spec

    async def testbench(ctx):
        await test_reset_and_idle(ctx, dut)
        await test_happy_path(ctx, dut)
        await test_feature_verify_retry(ctx, dut)
        await test_fault_priority(ctx, dut)
        await test_hold_and_illegal_resume(ctx, dut)

    sim.add_testbench(testbench)

    with sim.write_vcd("tb_lock_fsm.vcd", "tb_lock_fsm.gtkw"):
        sim.run()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    build_and_run()