"""
tb_lock_fsm.py
Testbench for lock_fsm.LockFSM (POSM FPGA MTS Laser Lock - supervisory FSM)

REQUIRED FIX BEFORE THIS WILL RUN
----------------------------------
lock_fsm.py currently contains:

    with m.If(self.lock_check_failed || self.relock_request):

`||` is not valid Python/Amaranth. It needs to be a bitwise OR on two
1-bit signals:

    with m.If(self.lock_check_failed | self.relock_request):

OBSERVATIONS (flagged, not silently patched around)
-----------------------------------------------------
1. `self.state` (the state exposed to the register bank) is registered a
   SECOND time on top of the internal `state` signal:

       m.d.sync += self.state.eq(state)

   The Switch-based output decoding (wide_scan_enable, feedback_enable,
   etc.) is driven combinationally off the internal `state`, so the comb
   outputs always reflect the FSM's current state one cycle *before*
   `self.state` reports it. This testbench treats the comb outputs as
   ground truth for "what state is the FSM in right now" and separately
   documents the one-cycle lag on `self.state` in
   test_state_output_lags_by_one_cycle(). If that lag is unintentional,
   drive it combinationally instead: `m.d.comb += self.state.eq(state)`.

2. `hold_request` is declared as an input but is never referenced anywhere
   in elaborate() -- it currently has no effect on the FSM. Flagging in
   case it was meant to pause/hold the current state.

Run directly with: python3 tb_lock_fsm.py
"""

import os

from amaranth.sim import Simulator

from lock_fsm import LockFSM, LockState


def new_dut():
    return LockFSM()


def run(dut, tb, name):
    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(tb)
    os.makedirs("build", exist_ok=True)
    with sim.write_vcd(f"build/{name}.vcd"):
        sim.run()


async def all_inputs_low(ctx, dut):
    ctx.set(dut.global_enable, 0)
    ctx.set(dut.lock_enable_request, 0)
    ctx.set(dut.hold_request, 0)
    ctx.set(dut.fault_active, 0)
    ctx.set(dut.fault_clear_request, 0)
    ctx.set(dut.trace_ready, 0)
    ctx.set(dut.feature_selected, 0)
    ctx.set(dut.zoom_complete, 0)
    ctx.set(dut.autolock_success, 0)
    ctx.set(dut.autolock_failed, 0)
    ctx.set(dut.lock_check_pass, 0)
    ctx.set(dut.lock_check_failed, 0)
    ctx.set(dut.relock_request, 0)


async def drive_to_wide_scan(ctx, dut):
    """Helper: get the FSM from IDLE into WIDE_SCAN."""
    ctx.set(dut.global_enable, 1)
    ctx.set(dut.lock_enable_request, 1)
    await ctx.tick()
    ctx.set(dut.lock_enable_request, 0)


async def drive_to_lock_watch(ctx, dut):
    """Helper: walk the FSM all the way from IDLE to LOCK_WATCH via the
    documented happy path."""
    await drive_to_wide_scan(ctx, dut)
    ctx.set(dut.trace_ready, 1)
    await ctx.tick()
    ctx.set(dut.trace_ready, 0)

    ctx.set(dut.feature_selected, 1)
    await ctx.tick()
    ctx.set(dut.feature_selected, 0)

    ctx.set(dut.zoom_complete, 1)
    await ctx.tick()
    ctx.set(dut.zoom_complete, 0)

    ctx.set(dut.autolock_success, 1)
    await ctx.tick()
    ctx.set(dut.autolock_success, 0)

    ctx.set(dut.lock_check_pass, 1)
    await ctx.tick()
    ctx.set(dut.lock_check_pass, 0)

    await ctx.tick()  # LOCKED -> LOCK_WATCH, unconditional


def test_reset_is_idle():
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()
        assert ctx.get(dut.wide_scan_enable) == 0
        assert ctx.get(dut.zoom_scan_enable) == 0
        assert ctx.get(dut.autolock_enable) == 0
        assert ctx.get(dut.feedback_enable) == 0
        assert ctx.get(dut.fault_state) == 0
        print("PASS: test_reset_is_idle")

    run(dut, tb, "test_reset_is_idle")


def test_full_lock_sequence():
    """Walk the documented path end to end, checking the output-decode
    table (Section: Output decoding) at every stop:

        IDLE -> WIDE_SCAN -> TRACE_READY -> ZOOM_SCAN -> FEATURE_VERIFY
              -> ARM_LOCK -> LOCKED -> LOCK_WATCH -> RELOCK_SCAN -> ZOOM_SCAN
    """
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()

        # IDLE -> WIDE_SCAN
        await drive_to_wide_scan(ctx, dut)
        assert ctx.get(dut.wide_scan_enable) == 1
        assert ctx.get(dut.trace_enable) == 1

        # WIDE_SCAN -> TRACE_READY
        ctx.set(dut.trace_ready, 1)
        await ctx.tick()
        ctx.set(dut.trace_ready, 0)
        assert ctx.get(dut.wide_scan_enable) == 0
        assert ctx.get(dut.trace_enable) == 0

        # TRACE_READY -> ZOOM_SCAN
        ctx.set(dut.feature_selected, 1)
        await ctx.tick()
        ctx.set(dut.feature_selected, 0)
        assert ctx.get(dut.zoom_scan_enable) == 1
        assert ctx.get(dut.trace_enable) == 1

        # ZOOM_SCAN -> FEATURE_VERIFY
        ctx.set(dut.zoom_complete, 1)
        await ctx.tick()
        ctx.set(dut.zoom_complete, 0)
        assert ctx.get(dut.zoom_scan_enable) == 0
        assert ctx.get(dut.autolock_enable) == 1

        # FEATURE_VERIFY -> ARM_LOCK
        ctx.set(dut.autolock_success, 1)
        await ctx.tick()
        ctx.set(dut.autolock_success, 0)
        assert ctx.get(dut.autolock_enable) == 0
        assert ctx.get(dut.feedback_enable) == 1

        # ARM_LOCK -> LOCKED
        ctx.set(dut.lock_check_pass, 1)
        await ctx.tick()
        ctx.set(dut.lock_check_pass, 0)
        assert ctx.get(dut.feedback_enable) == 1  # stays enabled

        # LOCKED -> LOCK_WATCH (unconditional, one cycle)
        await ctx.tick()
        assert ctx.get(dut.feedback_enable) == 1
        assert ctx.get(dut.lock_watch_enable) == 1

        # LOCK_WATCH -> RELOCK_SCAN on lock_check_failed
        ctx.set(dut.lock_check_failed, 1)
        await ctx.tick()
        ctx.set(dut.lock_check_failed, 0)
        assert ctx.get(dut.feedback_enable) == 0
        assert ctx.get(dut.lock_watch_enable) == 0

        # RELOCK_SCAN -> ZOOM_SCAN (unconditional, one cycle)
        await ctx.tick()
        assert ctx.get(dut.zoom_scan_enable) == 1

        print("PASS: test_full_lock_sequence")

    run(dut, tb, "test_full_lock_sequence")


def test_lock_watch_relock_via_relock_request():
    """LOCK_WATCH -> RELOCK_SCAN can also be triggered by relock_request
    (e.g. asserted by lock_watch.py) even when lock_check_failed is low."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()
        await drive_to_lock_watch(ctx, dut)
        assert ctx.get(dut.lock_watch_enable) == 1

        ctx.set(dut.relock_request, 1)
        await ctx.tick()
        ctx.set(dut.relock_request, 0)
        assert ctx.get(dut.feedback_enable) == 0
        assert ctx.get(dut.lock_watch_enable) == 0

        await ctx.tick()
        assert ctx.get(dut.zoom_scan_enable) == 1

        print("PASS: test_lock_watch_relock_via_relock_request")

    run(dut, tb, "test_lock_watch_relock_via_relock_request")


def test_feature_verify_failure_path():
    """FEATURE_VERIFY -> RELOCK_SCAN when autolock_failed is asserted
    instead of autolock_success."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()
        await drive_to_wide_scan(ctx, dut)

        ctx.set(dut.trace_ready, 1)
        await ctx.tick()
        ctx.set(dut.trace_ready, 0)

        ctx.set(dut.feature_selected, 1)
        await ctx.tick()
        ctx.set(dut.feature_selected, 0)

        ctx.set(dut.zoom_complete, 1)
        await ctx.tick()
        ctx.set(dut.zoom_complete, 0)

        ctx.set(dut.autolock_failed, 1)
        await ctx.tick()
        ctx.set(dut.autolock_failed, 0)
        assert ctx.get(dut.autolock_enable) == 0

        await ctx.tick()  # RELOCK_SCAN -> ZOOM_SCAN
        assert ctx.get(dut.zoom_scan_enable) == 1

        print("PASS: test_feature_verify_failure_path")

    run(dut, tb, "test_feature_verify_failure_path")


def test_fault_overrides_and_clears():
    """fault_active must override every other state immediately (even
    mid-sequence), and the FSM must only clear back to IDLE once
    fault_clear_request=1 AND fault_active=0 -- per the module docstring:

        Fault clear requires:
            fault_clear_request == 1
            fault_active == 0
    """
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()

        # Get into WIDE_SCAN first, to prove fault overrides mid-sequence.
        await drive_to_wide_scan(ctx, dut)
        assert ctx.get(dut.wide_scan_enable) == 1

        # Fault hits.
        ctx.set(dut.fault_active, 1)
        await ctx.tick()
        assert ctx.get(dut.fault_state) == 1
        assert ctx.get(dut.wide_scan_enable) == 0

        # Requesting clear while fault_active is still high must NOT clear.
        ctx.set(dut.fault_clear_request, 1)
        await ctx.tick()
        assert ctx.get(dut.fault_state) == 1

        # Drop fault_active, keep clear request high -> back to IDLE.
        ctx.set(dut.fault_active, 0)
        await ctx.tick()
        ctx.set(dut.fault_clear_request, 0)
        assert ctx.get(dut.fault_state) == 0
        assert ctx.get(dut.wide_scan_enable) == 0
        assert ctx.get(dut.autolock_enable) == 0
        assert ctx.get(dut.feedback_enable) == 0

        print("PASS: test_fault_overrides_and_clears")

    run(dut, tb, "test_fault_overrides_and_clears")


def test_global_enable_gates_start():
    """IDLE must not leave until BOTH global_enable and
    lock_enable_request are asserted together (bare lock_enable_request
    alone is not enough)."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()

        ctx.set(dut.lock_enable_request, 1)  # global_enable still 0
        await ctx.tick()
        assert ctx.get(dut.wide_scan_enable) == 0

        ctx.set(dut.global_enable, 1)
        await ctx.tick()
        assert ctx.get(dut.wide_scan_enable) == 1

        print("PASS: test_global_enable_gates_start")

    run(dut, tb, "test_global_enable_gates_start")


def test_state_output_lags_by_one_cycle():
    """Documents the one-cycle lag between the comb-decoded outputs
    (driven directly off the internal `state`) and the registered
    `self.state` output -- see OBSERVATIONS #1 at the top of this file."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()

        ctx.set(dut.global_enable, 1)
        ctx.set(dut.lock_enable_request, 1)

        await ctx.tick()  # internal state -> WIDE_SCAN; dut.state still IDLE
        ctx.set(dut.lock_enable_request, 0)
        assert ctx.get(dut.wide_scan_enable) == 1
        assert int(ctx.get(dut.state)) == LockState.IDLE.value

        await ctx.tick()  # dut.state now catches up to WIDE_SCAN
        assert int(ctx.get(dut.state)) == LockState.WIDE_SCAN.value

        print("PASS: test_state_output_lags_by_one_cycle")

    run(dut, tb, "test_state_output_lags_by_one_cycle")


if __name__ == "__main__":
    test_reset_is_idle()
    test_full_lock_sequence()
    test_lock_watch_relock_via_relock_request()
    test_feature_verify_failure_path()
    test_fault_overrides_and_clears()
    test_global_enable_gates_start()
    test_state_output_lags_by_one_cycle()
    print("\nAll lock_fsm tests passed.")