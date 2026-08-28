"""
tb_robust_autolock.py
Testbench for robust_autolock.RobustAutoLock (POSM feature verification
before enabling the fast lock loop).

REQUIRED FIXES BEFORE THIS WILL RUN
--------------------------------------
robust_autolock.py currently does not parse. In the order they appear:

1. Enum declaration is invalid:

       class AutoLockState(Enum, "IDLE SCAN TRACK CHECK SUCCESS RETRY FAIL"):

   amaranth.lib.enum.Enum members need explicit values, the same way
   `LockState` is written in lock_fsm.py, e.g.:

       class AutoLockState(Enum):
           IDLE    = 0
           SCAN    = 1
           TRACK   = 2
           CHECK   = 3
           SUCCESS = 4
           RETRY   = 5
           FAIL    = 6

   (Note "SCAN" is declared but never used as a state anywhere in
   elaborate() -- IDLE transitions straight to TRACK. Flagging in case a
   dedicated SCAN state was intended between them.)

2. `with m.Else:` (no parentheses) right after the top-level
   `with m.If(self.rst):` block -- needs to be `with m.Else():`.

3. In the CHECK state, and again in the RETRY state, an `.Else(` is
   chained directly onto the end of a `with m.If(...):` block:

       with m.If(...):
           m.d.sync += (state.eq(AutoLockState.SUCCESS))
       .Else(
           m.d.sync += (state.eq(AutoLockState.RETRY))
       )

   That is not valid Python. Both need to be their own `with m.Else():`
   block, e.g.:

       with m.If(...):
           m.d.sync += state.eq(AutoLockState.SUCCESS)
       with m.Else():
           m.d.sync += state.eq(AutoLockState.RETRY)

4. FUNCTIONAL BUG (not just a syntax error) in the CHECK state: `amplitude`
   and `width` are registered (m.d.sync) in the same always-block that
   immediately checks `amplitude >= self.amp_min` / width bounds using the
   *old* pre-update values of those same registers (since m.d.sync writes
   don't take effect until the next clock edge). The very first time CHECK
   is entered, amplitude/width both read as 0, so the pass/fail decision
   is made against stale data one cycle before the real numbers land.
   Recommended fix: compute amplitude/width combinationally (m.d.comb) so
   the SUCCESS/RETRY decision in the same cycle can use the freshly
   derived values, and register them separately if you still want them
   exposed for debug.

5. `expected_min_x` and `expected_max_x` (the descriptor's expected
   feature-position inputs) are never referenced anywhere in elaborate().
   Only amplitude, width, and slope are checked -- position matching
   against the descriptor is not actually verified. Flagging in case this
   was meant to be part of the CHECK criteria.

This testbench is written against the *intended*, corrected behavior
described in the module's docstring (window filtering, extrema tracking,
zero-crossing, amplitude/width/slope check, retry/fail bookkeeping), so
that it's ready to run once the module compiles. Given bug #4 above, each
test gives the DUT a couple of extra settle cycles in CHECK before
asserting the SUCCESS/RETRY/FAIL outcome, rather than assuming an exact
cycle count -- adjust wait_cycles down once you've picked a fix for #4.

Run directly with: python3 tb_robust_autolock.py
"""

import os
import sys

# AUDIT FIX: this testbench had no sys.path bootstrap and could not
# be run standalone ("ModuleNotFoundError: No module named 'rtl'"),
# contradicting README.md's claim that the repo "can be cloned and
# simulated without hidden local paths".
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))
import os

from amaranth.sim import Simulator

from rtl.autolock.robust_autolock import RobustAutoLock, AutoLockState


WINDOW_MIN = 10
WINDOW_MAX = 100

# A V-shaped (dispersive-like) error signal: falls to a minimum, crosses
# zero on the way up, then rises to a maximum. (scan_code, error_sample)
GOOD_FEATURE = [
    (10, -1000),
    (20, -3000),
    (30, -6000),   # minimum
    (40, -2000),
    (50, 1000),    # zero crossing between 40 and 50
    (60, 4000),
    (70, 8000),    # maximum
    (80, 6000),
    (90, 3000),
    (100, 1000),
]

# Same shape, scaled way down -- amplitude will fail amp_min.
WEAK_FEATURE = [(code, err // 20) for code, err in GOOD_FEATURE]

# amplitude = 8000 - (-6000) = 14000, width = |70-30| = 40
GOOD_DESCRIPTOR = dict(
    window_min=WINDOW_MIN,
    window_max=WINDOW_MAX,
    expected_min_x=30,
    expected_max_x=70,
    lock_x=50,
    amp_min=10000,
    width_min=20,
    width_max=60,
    slope_sign=1,   # max_position (70) > min_position (30)
    retry_limit=2,
)


def new_dut():
    return RobustAutoLock()


def run(dut, tb, name):
    sim = Simulator(dut)
    sim.add_clock(1e-8)  # 100 MHz
    sim.add_testbench(tb)
    os.makedirs("build", exist_ok=True)
    with sim.write_vcd(f"build/{name}.vcd"):
        sim.run()


async def apply_descriptor(ctx, dut, desc):
    for name, value in desc.items():
        ctx.set(getattr(dut, name), value)


async def idle_bus(ctx, dut):
    ctx.set(dut.rst, 0)
    ctx.set(dut.scan_valid, 0)
    ctx.set(dut.scan_done, 0)
    ctx.set(dut.scan_code, 0)
    ctx.set(dut.error_sample, 0)


async def feed_samples(ctx, dut, samples):
    """Drive one scan_valid pulse per (scan_code, error_sample) pair,
    one clock cycle apart, then pulse scan_done."""
    for code, err in samples:
        ctx.set(dut.scan_valid, 1)
        ctx.set(dut.scan_code, code)
        ctx.set(dut.error_sample, err)
        await ctx.tick()
    ctx.set(dut.scan_valid, 0)
    ctx.set(dut.scan_done, 1)
    await ctx.tick()
    ctx.set(dut.scan_done, 0)


async def wait_until(ctx, signal, expected, max_cycles=10):
    for _ in range(max_cycles):
        if ctx.get(signal) == expected:
            return True
        await ctx.tick()
    return False


def test_reset_is_idle_and_not_busy():
    dut = new_dut()

    async def tb(ctx):
        await idle_bus(ctx, dut)
        ctx.set(dut.rst, 1)
        await ctx.tick()
        ctx.set(dut.rst, 0)
        await ctx.tick()
        assert ctx.get(dut.feature_match) == 0
        assert ctx.get(dut.feature_failed) == 0
        assert ctx.get(dut.retry_request) == 0
        assert ctx.get(dut.retry_count) == 0
        print("PASS: test_reset_is_idle_and_not_busy")

    run(dut, tb, "test_reset_is_idle_and_not_busy")


def test_successful_feature_match():
    """A clean V-shaped feature that passes amplitude/width/slope should
    end in SUCCESS: feature_match pulses, lock prep signals pulse, and
    slow_lock_position takes on lock_x."""
    dut = new_dut()

    async def tb(ctx):
        await idle_bus(ctx, dut)
        ctx.set(dut.rst, 1)
        await ctx.tick()
        ctx.set(dut.rst, 0)
        await apply_descriptor(ctx, dut, GOOD_DESCRIPTOR)
        await ctx.tick()

        await feed_samples(ctx, dut, GOOD_FEATURE)
        assert ctx.get(dut.busy) == 1

        # Let CHECK settle (see bug #4 in the file header) then look for
        # the SUCCESS pulse.
        found = await wait_until(ctx, dut.feature_match, 1, max_cycles=6)
        assert found, "feature_match never asserted for a passing feature"
        assert ctx.get(dut.load_offset) == 1
        assert ctx.get(dut.load_polarity) == 1
        assert ctx.get(dut.arm_lock_request) == 1
        assert ctx.get(dut.slow_lock_position) == GOOD_DESCRIPTOR["lock_x"]

        # feature_match is a one-cycle pulse, not a level.
        await ctx.tick()
        assert ctx.get(dut.feature_match) == 0

        print("PASS: test_successful_feature_match")

    run(dut, tb, "test_successful_feature_match")


def test_weak_feature_retries_then_fails():
    """A feature whose amplitude never clears amp_min should RETRY up to
    retry_limit times, incrementing retry_count and pulsing
    retry_request, then FAIL and pulse feature_failed on the next
    attempt."""
    dut = new_dut()
    descriptor = dict(GOOD_DESCRIPTOR)
    descriptor["retry_limit"] = 2

    async def tb(ctx):
        await idle_bus(ctx, dut)
        ctx.set(dut.rst, 1)
        await ctx.tick()
        ctx.set(dut.rst, 0)
        await apply_descriptor(ctx, dut, descriptor)
        await ctx.tick()

        # Attempt 1: retry_count 0 -> 1
        await feed_samples(ctx, dut, WEAK_FEATURE)
        found = await wait_until(ctx, dut.retry_request, 1, max_cycles=6)
        assert found, "retry_request never asserted for a failing feature"
        assert ctx.get(dut.retry_count) == 1
        assert ctx.get(dut.feature_failed) == 0

        # Attempt 2: retry_count 1 -> 2
        await feed_samples(ctx, dut, WEAK_FEATURE)
        found = await wait_until(ctx, dut.retry_request, 1, max_cycles=6)
        assert found
        assert ctx.get(dut.retry_count) == 2

        # Attempt 3: retry_limit reached -> FAIL instead of RETRY
        await feed_samples(ctx, dut, WEAK_FEATURE)
        found = await wait_until(ctx, dut.feature_failed, 1, max_cycles=6)
        assert found, "feature_failed never asserted once retry_limit was reached"

        print("PASS: test_weak_feature_retries_then_fails")

    run(dut, tb, "test_weak_feature_retries_then_fails")


def test_window_filtering_ignores_out_of_range_samples():
    """Samples outside [window_min, window_max] must not perturb the
    tracked extrema, even if their error value would otherwise be a new
    min/max."""
    dut = new_dut()

    async def tb(ctx):
        await idle_bus(ctx, dut)
        ctx.set(dut.rst, 1)
        await ctx.tick()
        ctx.set(dut.rst, 0)
        await apply_descriptor(ctx, dut, GOOD_DESCRIPTOR)
        await ctx.tick()

        samples = list(GOOD_FEATURE)
        # Inject an out-of-window sample with an extreme error value that
        # would otherwise blow away the real minimum.
        samples.insert(3, (200, -99999))

        await feed_samples(ctx, dut, samples)
        found = await wait_until(ctx, dut.feature_match, 1, max_cycles=6)
        assert found, (
            "expected the real feature to still pass verification; "
            "the out-of-window sample should have been ignored"
        )

        print("PASS: test_window_filtering_ignores_out_of_range_samples")

    run(dut, tb, "test_window_filtering_ignores_out_of_range_samples")


def test_wrong_slope_sign_fails():
    """If slope_sign expects the opposite direction from the actual
    feature, verification should fail even though amplitude/width pass."""
    dut = new_dut()
    descriptor = dict(GOOD_DESCRIPTOR)
    descriptor["slope_sign"] = 0  # expects min_position > max_position

    async def tb(ctx):
        await idle_bus(ctx, dut)
        ctx.set(dut.rst, 1)
        await ctx.tick()
        ctx.set(dut.rst, 0)
        await apply_descriptor(ctx, dut, descriptor)
        await ctx.tick()

        await feed_samples(ctx, dut, GOOD_FEATURE)
        matched = await wait_until(ctx, dut.feature_match, 1, max_cycles=6)
        assert not matched, "feature_match should not assert on wrong slope_sign"

        print("PASS: test_wrong_slope_sign_fails")

    run(dut, tb, "test_wrong_slope_sign_fails")


if __name__ == "__main__":
    test_reset_is_idle_and_not_busy()
    test_successful_feature_match()
    test_weak_feature_retries_then_fails()
    test_window_filtering_ignores_out_of_range_samples()
    test_wrong_slope_sign_fails()
    print("\nAll robust_autolock tests passed.")