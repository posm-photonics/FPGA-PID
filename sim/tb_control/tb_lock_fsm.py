"""
tb_lock_fsm.py
Testbench for lock_fsm.LockFSM (POSM FPGA MTS Laser Lock - supervisory FSM)
Run directly with: python3 tb_lock_fsm.py
"""
import sys
import os

from amaranth.sim import Simulator
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from rtl.control.lock_fsm import LockFSM, LockState


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
        #
        # AUDIT FIX: this used to assert zoom_scan_enable == 0 in
        # FEATURE_VERIFY. That was the bug, not the spec: the zoom ramp
        # stopped at exactly the moment the autolock was switched on, so
        # the verifier had no scan data to track and the error signal
        # went static underneath it. Packet 8.10 puts them together --
        # "1. Run zoom scan over selected window. 2. Track local
        # extrema." The scan therefore continues through FEATURE_VERIFY.
        ctx.set(dut.zoom_complete, 1)
        await ctx.tick()
        ctx.set(dut.zoom_complete, 0)
        assert ctx.get(dut.zoom_scan_enable) == 1
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


def test_state_output_is_same_cycle_as_decoded_outputs():
    """self.state and the m.Switch-decoded outputs (wide_scan_enable
    etc.) are both purely combinational off the same internal `state`
    register, so they update on the exact same cycle -- there is no
    one-cycle lag between them."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()

        ctx.set(dut.global_enable, 1)
        ctx.set(dut.lock_enable_request, 1)

        await ctx.tick()  # state -> WIDE_SCAN, same cycle as the output
        ctx.set(dut.lock_enable_request, 0)
        assert ctx.get(dut.wide_scan_enable) == 1
        assert int(ctx.get(dut.state)) == LockState.WIDE_SCAN.value

        print("PASS: test_state_output_is_same_cycle_as_decoded_outputs")

    run(dut, tb, "test_state_output_is_same_cycle_as_decoded_outputs")


def test_hold_freezes_state_and_releases_cleanly():
    """hold_request should freeze the sequencer exactly where it is --
    no automatic or conditional transition should occur while held,
    the frozen state's outputs should keep running, and releasing
    hold should let the FSM continue exactly where it left off."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()
        await drive_to_lock_watch(ctx, dut)
        assert ctx.get(dut.feedback_enable) == 1
        assert ctx.get(dut.lock_watch_enable) == 1

        # Hold while in LOCK_WATCH.
        ctx.set(dut.hold_request, 1)
        await ctx.tick()
        assert ctx.get(dut.hold_active) == 1
        # Frozen state's outputs keep running.
        assert ctx.get(dut.feedback_enable) == 1
        assert ctx.get(dut.lock_watch_enable) == 1

        # Try to force a transition while held -- must NOT move.
        ctx.set(dut.lock_check_failed, 1)
        await ctx.tick()
        assert ctx.get(dut.lock_watch_enable) == 1  # still LOCK_WATCH
        assert ctx.get(dut.feedback_enable) == 1
        ctx.set(dut.lock_check_failed, 0)

        # Release hold -- FSM should now resume and see the (still
        # pending) failure condition on the very next qualifying edge.
        ctx.set(dut.hold_request, 0)
        ctx.set(dut.lock_check_failed, 1)
        await ctx.tick()
        ctx.set(dut.lock_check_failed, 0)
        assert ctx.get(dut.hold_active) == 0
        assert ctx.get(dut.feedback_enable) == 0
        assert ctx.get(dut.lock_watch_enable) == 0

        print("PASS: test_hold_freezes_state_and_releases_cleanly")

    run(dut, tb, "test_hold_freezes_state_and_releases_cleanly")


def test_fault_overrides_hold():
    """A fault must still interrupt and be clearable even while
    hold_request is asserted -- hold must never block fault safety."""
    dut = new_dut()

    async def tb(ctx):
        await all_inputs_low(ctx, dut)
        await ctx.tick()
        await drive_to_wide_scan(ctx, dut)

        ctx.set(dut.hold_request, 1)
        await ctx.tick()
        assert ctx.get(dut.hold_active) == 1

        # Fault hits while held -- must override immediately.
        ctx.set(dut.fault_active, 1)
        await ctx.tick()
        assert ctx.get(dut.fault_state) == 1
        assert ctx.get(dut.hold_active) == 0  # fault, not hold, is in effect
        assert ctx.get(dut.wide_scan_enable) == 0

        # Fault must still be clearable even with hold_request held high.
        ctx.set(dut.fault_active, 0)
        ctx.set(dut.fault_clear_request, 1)
        await ctx.tick()
        ctx.set(dut.fault_clear_request, 0)
        assert ctx.get(dut.fault_state) == 0
        # Back in IDLE, now hold takes over again (hold_request still 1).
        assert ctx.get(dut.hold_active) == 1

        print("PASS: test_fault_overrides_hold")

    run(dut, tb, "test_fault_overrides_hold")


if __name__ == "__main__":
    test_reset_is_idle()
    test_full_lock_sequence()
    test_lock_watch_relock_via_relock_request()
    test_feature_verify_failure_path()
    test_fault_overrides_and_clears()
    test_global_enable_gates_start()
    test_state_output_is_same_cycle_as_decoded_outputs()
    test_hold_freezes_state_and_releases_cleanly()
    test_fault_overrides_hold()
    print("\nAll lock_fsm tests passed.")