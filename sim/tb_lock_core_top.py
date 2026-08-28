"""
tb_lock_core_top.py

Full-system integration testbench for top.lock_core_top.LockCoreTop.

===========================================================================
WHY THIS FILE WAS REWRITTEN
===========================================================================
The previous version could not run at all. LockCoreTop drove
ClockSignal("sync") from combinational logic, so attaching a simulation
clock raised

    amaranth.hdl._ir.DriverConflict: Clock signal is already driven by
    combinational logic

walkthrough.md recorded that failure, described it as a test-environment
quirk, and shipped anyway with the claim that "the RTL logic itself
synthesizes cleanly". It did not: the design did not even elaborate.

Because this was the only full-system testbench in the repository, every
defect living at a module boundary went unexercised. The pre-ship audit
found eleven ship-blockers in lock_core_top.py alone, and this file would
have caught most of them.

The old testbench also asserted NOTHING: it drove stimulus for 440 cycles
and checked no output, so even once it ran it could not have failed.

This version implements the system simulation packet section 12.2 asks
for -- scan, trace capture, feature selection, zoom, autolock verify,
handoff, lock, watch -- plus one directed regression test per confirmed
audit finding, so a reintroduced bug fails the suite instead of reaching
hardware.
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from amaranth.sim import Simulator

from top.lock_core_top import LockCoreTop
from rtl.bus.register_defs import (
    ADDR_CONTROL,
    ADDR_FAST_KP, ADDR_FAST_KI, ADDR_FAST_OUT_MIN, ADDR_FAST_OUT_MAX,
    ADDR_FAST_OUT_SAFE,
    ADDR_RAMP_MIN, ADDR_RAMP_MAX, ADDR_RAMP_STEP, ADDR_RAMP_TICK_DIV,
    ADDR_RAMP_CENTER, ADDR_RAMP_WIDTH,
    ADDR_AUTOLOCK_WINDOW_MIN, ADDR_AUTOLOCK_WINDOW_MAX,
    ADDR_AUTOLOCK_LOCK_X, ADDR_AUTOLOCK_AMP_MIN,
    ADDR_AUTOLOCK_WIDTH_MIN, ADDR_AUTOLOCK_WIDTH_MAX,
    ADDR_AUTOLOCK_SLOPE_SIGN, ADDR_AUTOLOCK_RETRY_LIMIT,
    ADDR_TRACE_CONFIG, ADDR_TRACE_LENGTH, ADDR_TRACE_START,
    ADDR_TRACE_STATUS, ADDR_TRACE_WRITE_PTR,
    ADDR_LOCK_CHECK_DELAY, ADDR_LOCK_MAX_ERROR, ADDR_LOCK_MAX_SAT_COUNT,
    ADDR_LOCK_ADC_TIMEOUT, ADDR_LOCK_ERROR_TIMEOUT, ADDR_LOCK_JUMP_LIMIT,
    ADDR_LOCK_STATE_TIMEOUT,
    ADDR_SLOW_CTRL_CONFIG, ADDR_SLOW_OUT_MIN, ADDR_SLOW_OUT_MAX,
    CTRL_GLOBAL_ENABLE, CTRL_LOCK_ENABLE_REQUEST, CTRL_OUTPUTS_ENABLE,
    CTRL_FAULT_CLEAR_REQUEST, CTRL_TRACE_CAPTURE_ENABLE,
    CTRL_AUTOLOCK_ENABLE,
    SLOW_CFG_TICK_DIV_SHIFT,
)

CLK = 8e-9  # 125 MHz

STATE_NAMES = {
    0: "IDLE", 1: "WIDE_SCAN", 2: "TRACE_READY", 3: "USER_SELECT",
    4: "ZOOM_SCAN", 5: "FEATURE_VERIFY", 6: "ARM_LOCK", 7: "LOCKED",
    8: "LOCK_WATCH", 9: "RELOCK_SCAN", 10: "FAULT",
}

CTRL_RUN = (1 << CTRL_GLOBAL_ENABLE) | (1 << CTRL_OUTPUTS_ENABLE)
CTRL_ACQUIRE = (CTRL_RUN
                | (1 << CTRL_LOCK_ENABLE_REQUEST)
                | (1 << CTRL_TRACE_CAPTURE_ENABLE)
                | (1 << CTRL_AUTOLOCK_ENABLE))


def s16(v):
    return v - (1 << 16) if v >= (1 << 15) else v


async def wr(ctx, dut, addr, value):
    ctx.set(dut.adr, addr)
    ctx.set(dut.dat_w, value & 0xFFFFFFFF)
    ctx.set(dut.we, 1)
    ctx.set(dut.stb, 1)
    await ctx.tick()
    ctx.set(dut.we, 0)
    ctx.set(dut.stb, 0)
    await ctx.tick()


async def rd(ctx, dut, addr):
    ctx.set(dut.adr, addr)
    ctx.set(dut.we, 0)
    ctx.set(dut.stb, 1)
    await ctx.tick()
    value = ctx.get(dut.dat_r)
    ctx.set(dut.stb, 0)
    await ctx.tick()
    return value


async def release_reset(ctx, dut):
    ctx.set(dut.rst, 1)
    ctx.set(dut.i_adc_valid, 0)
    # Plain per-cycle ticks: TickTrigger.repeat() raises DomainReset if
    # the domain is held in reset during the wait.
    for _ in range(4):
        await ctx.tick()
    ctx.set(dut.rst, 0)
    ctx.set(dut.i_adc_valid, 1)
    ctx.set(dut.i_format_mode, 1)     # two's-complement passthrough
    for _ in range(2):
        await ctx.tick()


def run(dut, testbench):
    sim = Simulator(dut)
    sim.add_clock(CLK)
    sim.add_testbench(testbench)
    sim.run()


# ===========================================================================
# Plant model
# ===========================================================================
class FakePlant:
    """Laser + MTS spectroscopy, as seen by ADC_CH0.

    Detuning is set by the slow DAC (coarse scan) plus the fast DAC (fine
    correction). The error signal is a dispersive lineshape with a zero
    crossing at the feature centre: the shape packet 1.3 and 4.3
    describe, with opposite signs either side of the lock point.
    """

    def __init__(self, feature_center=600, width=400, amplitude=6000,
                 fast_gain=0.2):
        self.feature_center = feature_center
        self.width = width
        self.amplitude = amplitude
        self.fast_gain = fast_gain

    def error(self, slow_code, fast_code):
        detuning = (slow_code - self.feature_center) - self.fast_gain * fast_code
        x = detuning / self.width
        # Dispersive: linear through zero, rolling off either side.
        return int(self.amplitude * x / (1.0 + x * x))


async def configure_acquisition(ctx, dut):
    """Common scan / autolock / servo configuration for the lock tests."""
    # Scan: short and fast so the tests run quickly.
    await wr(ctx, dut, ADDR_RAMP_MIN, 0xFFFFF830)     # -2000
    await wr(ctx, dut, ADDR_RAMP_MAX, 2000)
    await wr(ctx, dut, ADDR_RAMP_STEP, 64)
    await wr(ctx, dut, ADDR_RAMP_TICK_DIV, 1)
    await wr(ctx, dut, ADDR_RAMP_CENTER, 600)
    await wr(ctx, dut, ADDR_RAMP_WIDTH, 1200)

    # Autolock descriptor matching the modelled feature.
    await wr(ctx, dut, ADDR_AUTOLOCK_WINDOW_MIN, 0xFFFFF830)
    await wr(ctx, dut, ADDR_AUTOLOCK_WINDOW_MAX, 2000)
    await wr(ctx, dut, ADDR_AUTOLOCK_AMP_MIN, 1000)
    await wr(ctx, dut, ADDR_AUTOLOCK_WIDTH_MIN, 32)
    await wr(ctx, dut, ADDR_AUTOLOCK_WIDTH_MAX, 4000)
    await wr(ctx, dut, ADDR_AUTOLOCK_SLOPE_SIGN, 1)
    await wr(ctx, dut, ADDR_AUTOLOCK_LOCK_X, 600)
    await wr(ctx, dut, ADDR_AUTOLOCK_RETRY_LIMIT, 20)

    # Servo.
    # Gains chosen for the modelled plant: its error slope is about
    # 3 counts of error per count of fast DAC, so Kp = 0.0625 gives a
    # loop gain near 0.19 and a comfortably stable loop. (Kp = 0.25 with
    # the raw plant gain puts the loop gain near 3.75 and it oscillates,
    # which is a property of these test numbers, not of the RTL.)
    await wr(ctx, dut, ADDR_FAST_KP, 1024)             # 0.0625 in Q3.14
    await wr(ctx, dut, ADDR_FAST_KI, 16)
    await wr(ctx, dut, ADDR_FAST_OUT_MIN, 0xFFFFE0C0)  # -8000
    await wr(ctx, dut, ADDR_FAST_OUT_MAX, 8000)
    await wr(ctx, dut, ADDR_FAST_OUT_SAFE, 0)

    # Watchdog: permissive enough that ordinary acquisition transients do
    # not trip it, which the old hardcoded 800 ns saturation timeout did.
    await wr(ctx, dut, ADDR_LOCK_MAX_ERROR, 3000)
    await wr(ctx, dut, ADDR_LOCK_CHECK_DELAY, 16)
    await wr(ctx, dut, ADDR_LOCK_STATE_TIMEOUT, 200000)
    await wr(ctx, dut, ADDR_LOCK_MAX_SAT_COUNT, 100000)
    await wr(ctx, dut, ADDR_LOCK_ADC_TIMEOUT, 1000)
    await wr(ctx, dut, ADDR_LOCK_ERROR_TIMEOUT, 100000)
    await wr(ctx, dut, ADDR_LOCK_JUMP_LIMIT, 20000)

    # Slow path.
    await wr(ctx, dut, ADDR_SLOW_OUT_MIN, 0xFFFFE0C0)
    await wr(ctx, dut, ADDR_SLOW_OUT_MAX, 8000)
    await wr(ctx, dut, ADDR_SLOW_CTRL_CONFIG, 12 << SLOW_CFG_TICK_DIV_SHIFT)

    # Trace.
    await wr(ctx, dut, ADDR_TRACE_CONFIG, 1)
    await wr(ctx, dut, ADDR_TRACE_LENGTH, 64)

    await wr(ctx, dut, ADDR_CONTROL, CTRL_ACQUIRE)
    await wr(ctx, dut, ADDR_TRACE_START, 1)


# ===========================================================================
# TEST 1 -- register readback (regression guard for S2-1)
# ===========================================================================
def test_register_readback():
    """Every R/W register must read back what was written.

    21 registers had a write decode and no read decode, so they all read
    back 0. Packet section 11: "Every writable configuration value should
    be readable."
    """
    dut = LockCoreTop()

    async def tb(ctx):
        await release_reset(ctx, dut)

        cases = [
            ("FAST_KP", ADDR_FAST_KP, 8192),
            ("FAST_KI", ADDR_FAST_KI, 133),
            ("FAST_OUT_MAX", ADDR_FAST_OUT_MAX, 4000),
            ("RAMP_MAX", ADDR_RAMP_MAX, 2000),
            ("RAMP_STEP", ADDR_RAMP_STEP, 64),
            ("RAMP_TICK_DIV", ADDR_RAMP_TICK_DIV, 2),
            ("RAMP_CENTER", ADDR_RAMP_CENTER, 600),
            ("RAMP_WIDTH", ADDR_RAMP_WIDTH, 900),
            ("AUTOLOCK_LOCK_X", ADDR_AUTOLOCK_LOCK_X, 1234),
            ("AUTOLOCK_WIDTH_MIN", ADDR_AUTOLOCK_WIDTH_MIN, 10),
            ("AUTOLOCK_WIDTH_MAX", ADDR_AUTOLOCK_WIDTH_MAX, 5000),
            ("AUTOLOCK_RETRY_LIMIT", ADDR_AUTOLOCK_RETRY_LIMIT, 5),
            ("LOCK_MAX_ERROR", ADDR_LOCK_MAX_ERROR, 4096),
            ("LOCK_CHECK_DELAY", ADDR_LOCK_CHECK_DELAY, 32),
            ("LOCK_STATE_TIMEOUT", ADDR_LOCK_STATE_TIMEOUT, 1000000),
        ]

        for _, addr, value in cases:
            await wr(ctx, dut, addr, value)

        for name, addr, value in cases:
            got = await rd(ctx, dut, addr)
            assert got == value, \
                f"{name}: wrote {value}, read back {got} (S2-1 regression)"

        print(f"PASS: test_register_readback ({len(cases)} registers verified)")

    run(dut, tb)


# ===========================================================================
# TEST 2 -- fault clear (regression guard for S1-3)
# ===========================================================================
def test_fault_is_recoverable():
    """FAULT must be escapable through the documented clear path.

    fault_source used to include lock_fsm.fault_state, closing a
    combinational latch through two modules: entering FAULT made
    fault_active permanently 1, which made the fault_clear_request branch
    unreachable. Only a hardware reset recovered. Packet 10.2 requires
    explicit clear; explicit clear existed and was unreachable.
    """
    dut = LockCoreTop()

    async def tb(ctx):
        await release_reset(ctx, dut)
        await wr(ctx, dut, ADDR_CONTROL, CTRL_RUN)
        await ctx.tick().repeat(10)

        ctx.set(dut.i_external_interlock, 1)
        await ctx.tick().repeat(20)
        state = ctx.get(dut.lock_state)
        assert state == 10, \
            f"interlock should force FAULT, got {STATE_NAMES.get(state)}"
        assert ctx.get(dut.lock_fault) == 1

        ctx.set(dut.i_external_interlock, 0)
        await ctx.tick().repeat(20)
        await wr(ctx, dut, ADDR_CONTROL,
                 CTRL_RUN | (1 << CTRL_FAULT_CLEAR_REQUEST))
        await ctx.tick().repeat(20)

        state = ctx.get(dut.lock_state)
        assert state != 10, (
            "FAULT could not be cleared after the cause was removed "
            "(S1-3 regression: fault_state must not feed fault_source)"
        )
        print(f"PASS: test_fault_is_recoverable "
              f"(recovered to {STATE_NAMES.get(state)})")

    run(dut, tb)


# ===========================================================================
# TEST 3 -- trace capture fills (regression guard for S1-4)
# ===========================================================================
def test_trace_capture_fills():
    """The trace buffer must fill at the ramp-step rate.

    trace_capture.sample_valid was wired to ramp_scan.cycle_done, which
    pulses once per COMPLETE sweep, so the buffer captured one point per
    entire scan. Measured on the old integration: 11 samples in 3.2 ms,
    trace_ready never asserted, FSM stuck in WIDE_SCAN.
    """
    dut = LockCoreTop()
    plant = FakePlant()

    async def tb(ctx):
        await release_reset(ctx, dut)
        await configure_acquisition(ctx, dut)

        ready = 0
        for _ in range(8000):
            slow = s16(ctx.get(dut.slow_output))
            fast = s16(ctx.get(dut.fast_output))
            ctx.set(dut.i_adc_ch0, plant.error(slow, fast) & 0xFFFF)
            await ctx.tick()
            if ctx.get(dut.trace_ready):
                ready = 1
                break

        wr_ptr = await rd(ctx, dut, ADDR_TRACE_WRITE_PTR)
        status = await rd(ctx, dut, ADDR_TRACE_STATUS)

        assert ready == 1, (
            f"trace never completed in 8000 cycles (write_ptr={wr_ptr}, "
            f"status={status:#05b}); S1-4 regression: sample_valid must "
            "strobe per ramp step, not per completed sweep"
        )
        print(f"PASS: test_trace_capture_fills (64-point trace ready)")

    run(dut, tb)


# ===========================================================================
# TEST 4 -- acquisition sequence (packet 12.2)
# ===========================================================================
def test_acquisition_reaches_lock():
    """Full sequence: WIDE_SCAN -> ... -> LOCKED / LOCK_WATCH.

    This is the system simulation packet section 12.2 requires, and the
    acceptance-checklist item that could not previously be attempted at
    all because the top level did not elaborate.
    """
    dut = LockCoreTop()
    plant = FakePlant()

    async def tb(ctx):
        await release_reset(ctx, dut)
        await configure_acquisition(ctx, dut)

        seen = []
        last = None
        reached = False
        for _ in range(200000):
            slow = s16(ctx.get(dut.slow_output))
            fast = s16(ctx.get(dut.fast_output))
            ctx.set(dut.i_adc_ch0, plant.error(slow, fast) & 0xFFFF)
            # The operator picks a feature once the trace is ready.
            ctx.set(dut.i_feature_selected, ctx.get(dut.trace_ready))
            await ctx.tick()

            st = ctx.get(dut.lock_state)
            if st != last:
                seen.append(STATE_NAMES.get(st, st))
                last = st
            if st in (7, 8):
                reached = True
                break

        print("  state trace:", " -> ".join(seen))
        assert reached, (
            f"never reached LOCKED/LOCK_WATCH; ended in "
            f"{STATE_NAMES.get(ctx.get(dut.lock_state))}"
        )
        print("PASS: test_acquisition_reaches_lock")

    run(dut, tb)


# ===========================================================================
# TEST 5 -- closed-loop convergence (regression guard for S1-2 / S1-6)
# ===========================================================================
def test_closed_loop_converges():
    """Once locked, the error must stay bounded and the servo must hold.

    Two defects made this impossible before:
      * S1-2: the PI integrator truncated Ki*e BEFORE accumulating, so it
        had a dead zone for small errors and drifted to a rail on a
        zero-mean error (reproduced: rail in 64 us).
      * S1-6: integrator_load was held as a LEVEL in ARM_LOCK, reloading
        the integrator every clock so it could never accumulate.
    """
    dut = LockCoreTop()
    plant = FakePlant()

    async def tb(ctx):
        await release_reset(ctx, dut)
        await configure_acquisition(ctx, dut)

        errors = []
        locked_for = 0
        for _ in range(300000):
            slow = s16(ctx.get(dut.slow_output))
            fast = s16(ctx.get(dut.fast_output))
            err = plant.error(slow, fast)
            ctx.set(dut.i_adc_ch0, err & 0xFFFF)
            ctx.set(dut.i_feature_selected, ctx.get(dut.trace_ready))
            await ctx.tick()

            if ctx.get(dut.lock_state) in (7, 8):
                locked_for += 1
                errors.append(abs(err))
                if locked_for > 20000:
                    break

        assert locked_for > 1000, \
            f"never held lock long enough to measure (locked_for={locked_for})"

        quarter = len(errors) // 4
        early = sum(errors[:quarter]) / max(quarter, 1)
        late = sum(errors[-quarter:]) / max(quarter, 1)
        final_fast = s16(ctx.get(dut.fast_output))
        print(f"  |error| early={early:.1f}  late={late:.1f}  "
              f"locked_samples={locked_for}  final_fast={final_fast}")

        # The integral term must drive the error to zero and HOLD it.
        # The old PI could not: for small errors the truncated increment
        # was 0 for positive and -1 for negative, so it either sat in a
        # dead zone or ratcheted to a rail (measured: rail in 64 us on a
        # zero-mean error).
        assert late <= 2, (
            f"steady-state error did not converge ({early:.1f} -> "
            f"{late:.1f}); S1-2 regression: the integrator must null the "
            "error, not sit in a dead zone"
        )
        assert late < early, (
            f"error did not improve while locked ({early:.1f} -> {late:.1f})"
        )
        assert abs(final_fast) < 7900, (
            f"fast output parked at the rail ({final_fast}); "
            "S1-2 regression: truncation drift"
        )
        print("PASS: test_closed_loop_converges")

    run(dut, tb)


# ===========================================================================
# TEST 6 -- acquisition timeout (regression guard for S1-5)
# ===========================================================================
def test_acquisition_times_out():
    """A scan that can never complete must not hang forever.

    Every waiting state used to wait indefinitely with no escape: the
    audit measured the FSM sitting in WIDE_SCAN for an entire 400,000
    cycle run. With a ramp too slow to ever finish, the FSM must now
    escalate to FAULT rather than hang.
    """
    dut = LockCoreTop()

    async def tb(ctx):
        await release_reset(ctx, dut)

        await wr(ctx, dut, ADDR_RAMP_MIN, 0xFFFF8000)   # -32768
        await wr(ctx, dut, ADDR_RAMP_MAX, 32767)
        await wr(ctx, dut, ADDR_RAMP_STEP, 1)
        await wr(ctx, dut, ADDR_RAMP_TICK_DIV, 1000)
        await wr(ctx, dut, ADDR_LOCK_STATE_TIMEOUT, 5000)
        await wr(ctx, dut, ADDR_TRACE_CONFIG, 1)
        await wr(ctx, dut, ADDR_TRACE_LENGTH, 4000)
        await wr(ctx, dut, ADDR_CONTROL, CTRL_ACQUIRE)
        await wr(ctx, dut, ADDR_TRACE_START, 1)

        faulted = False
        for _ in range(40000):
            await ctx.tick()
            if ctx.get(dut.lock_state) == 10:
                faulted = True
                break

        assert faulted, (
            "acquisition never timed out; S1-5 regression: waiting states "
            "must have a bounded dwell"
        )
        print("PASS: test_acquisition_times_out")

    run(dut, tb)


if __name__ == "__main__":
    test_register_readback()
    test_fault_is_recoverable()
    test_trace_capture_fills()
    test_acquisition_reaches_lock()
    test_closed_loop_converges()
    test_acquisition_times_out()
    print("\nAll lock_core_top integration tests passed.")
