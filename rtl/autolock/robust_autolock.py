"""
POSM FPGA MTS Laser Lock Core
robust_autolock.py

Purpose
-------
robust_autolock is the FPGA's "is this the correct atomic feature?"
checker before safely engaging the laser feedback loop. It checks whether
the scanned spectroscopy line is the one we wanted before turning on
the laser lock.

Architectural role
------------------
This module is supervisory only.
It receives a zoom scan stream and determines whether the observed
spectroscopy feature matches a PC-provided descriptor.

Algorithm
---------
Streaming comparisons only:

- window filtering
- extrema tracking
- sign transition detection
- amplitude comparison
- width comparison
- slope verification

===========================================================================
AUDIT FIXES
===========================================================================
S2-7  The module had NO enable port. lock_fsm.autolock_enable was
      generated and connected to nothing, so the autolock ran during
      WIDE_SCAN as well as FEATURE_VERIFY. It burned through its retry
      budget against wide-scan data that could never match the
      descriptor, so by the time the FSM actually reached FEATURE_VERIFY
      retry_count was already at retry_limit and the module went
      straight to FAIL. An `enable` port now holds it in IDLE and clears
      the retry budget whenever the supervisor is not asking for a
      verification.

S2-8  Every scan-position port was UNSIGNED while lock_core_top drives
      them from ramp_scan.ramp_out, which is signed(16). Negative scan
      positions became large unsigned values (-3200 read as 62336), and
      the default ramp_min is -3200, so roughly half the scan range was
      affected. Consequences: measured_width computed in unsigned
      arithmetic gave ~61000 instead of ~4200 for a feature spanning the
      zero code (which is where a centred feature lives), and the slope
      test `max_position > min_position` inverted across the sign
      boundary. All scan positions are signed now.

      The measured zero crossing was tracked and then never used:
      slow_lock_position was set from the PC's guessed lock_x rather
      than from the crossing the FPGA actually observed, which defeats
      the point of FPGA-side verification. CHECK now requires that a
      zero crossing was seen inside the window, and the lock position is
      the measured crossing.

OPEN ITEM (not fixed here, deliberately)
      expected_min_x and expected_max_x are register-mapped descriptor
      fields (packet 11.8, "Approximate selected feature minimum" and
      maximum) that this module still does not consult. Using them needs
      a position-tolerance field that the register map does not define,
      and inventing verification semantics for a safety-relevant
      acquisition step is worse than flagging the gap. Either add a
      tolerance register per packet 11.8 and check the measured extrema
      against it, or remove the two fields from the map.
"""

from amaranth import *
from amaranth.lib.enum import Enum


class AutoLockState(Enum):
    IDLE = 0
    SCAN = 1
    TRACK = 2
    CHECK = 3
    SUCCESS = 4
    RETRY = 5
    FAIL = 6
    """
    IDLE    → waiting
    SCAN    → receiving zoom scan data
    TRACK   → tracking extrema/zero crossing
    CHECK   → comparing with the expected feature
    SUCCESS → feature matches
    RETRY   → try acquisition again
    FAIL    → give up
    """
    pass


class RobustAutoLock(Elaboratable):

    def __init__(self, dac_w=16, err_w=24):
        self.dac_w = dac_w
        self.err_w = err_w

        # -------------------------------------------------------------
        # Inputs
        # -------------------------------------------------------------

        # Clock/reset
        self.rst = Signal()

        # Supervisor gate. When low the module sits in IDLE and its retry
        # budget is cleared, so a verification always starts fresh.
        # Wire this to lock_fsm.autolock_enable.
        self.enable = Signal(init=1)

        # -------------------------------------------------------------
        # SCAN STREAM FROM FPGA

        self.scan_valid = Signal()
        self.scan_done = Signal()

        # The current value of the slow DAC scan position.
        # SIGNED: ramp_scan.ramp_out is signed(16).
        self.scan_code = Signal(signed(dac_w))

        self.error_sample = Signal(signed(err_w))
        # -------------------------------------------------------------

        # -------------------------------------------------------------
        # DESCRIPTOR INPUTS FROM THE PC

        # The allowed scan region where the feature should appear.
        self.window_min = Signal(signed(dac_w))
        self.window_max = Signal(signed(dac_w))

        # Expected position of the minimum of the feature.
        # NOTE: not currently consulted -- see OPEN ITEM in the module
        # docstring.
        self.expected_min_x = Signal(signed(dac_w))

        # Expected position of the maximum of the feature.
        # NOTE: not currently consulted -- see OPEN ITEM.
        self.expected_max_x = Signal(signed(dac_w))

        # Expected zero-crossing position (PC estimate).
        self.lock_x = Signal(signed(dac_w))

        # Minimum required feature amplitude.
        self.amp_min = Signal(signed(err_w))

        # Acceptable feature width range (unsigned magnitudes in codes)
        self.width_min = Signal(dac_w)
        self.width_max = Signal(dac_w)

        # Expected direction of the error signal slope.
        self.slope_sign = Signal()

        self.retry_limit = Signal(8)
        # -------------------------------------------------------------

        # -------------------------------------------------------------
        # Outputs
        # -------------------------------------------------------------
        self.busy = Signal()

        self.feature_match = Signal()
        self.feature_failed = Signal()

        self.retry_request = Signal()
        self.retry_count = Signal(8)

        # Lock preparation requests before enabling PI loop.
        # SIGNED: this is a scan position and feeds the slow-path handoff.
        self.slow_lock_position = Signal(signed(dac_w))

        self.load_offset = Signal()
        self.load_polarity = Signal()

        self.arm_lock_request = Signal()

    def elaborate(self, platform):
        m = Module()

        dac_w, err_w = self.dac_w, self.err_w

        # State
        state = Signal(AutoLockState, init=AutoLockState.IDLE)

        # min and max value for the error_sample
        min_value = Signal(signed(err_w), init=2 ** (err_w - 1) - 1)
        max_value = Signal(signed(err_w), init=-(2 ** (err_w - 1)))

        min_position = Signal(signed(dac_w))
        max_position = Signal(signed(dac_w))

        # Zero crossing tracker
        previous_error = Signal(signed(err_w))
        prev_valid = Signal()
        zero_cross_position = Signal(signed(dac_w))
        zero_cross_seen = Signal()

        # Measured values
        amplitude = Signal(signed(err_w + 1))
        width = Signal(dac_w + 1)
        measured_amplitude = Signal(signed(err_w + 1))
        measured_width = Signal(dac_w + 1)

        m.d.comb += [
            measured_amplitude.eq(max_value - min_value),
            # Signed subtraction, unsigned magnitude out.
            measured_width.eq(
                Mux(
                    max_position >= min_position,
                    max_position - min_position,
                    min_position - max_position,
                )
            ),
        ]

        in_window = Signal()
        m.d.comb += in_window.eq(
            (self.scan_code >= self.window_min)
            & (self.scan_code <= self.window_max)
        )

        # Main FSM
        with m.If(self.rst):
            m.d.sync += [
                state.eq(AutoLockState.IDLE),

                self.feature_match.eq(0),
                self.feature_failed.eq(0),

                self.retry_request.eq(0),

                self.retry_count.eq(0),

                self.arm_lock_request.eq(0),

                self.load_offset.eq(0),
                self.load_polarity.eq(0),
            ]

        # AUDIT FIX (S2-7): when the supervisor is not asking for a
        # verification, park in IDLE and clear the retry budget. Without
        # this the module free-ran on the wide scan and arrived at
        # FEATURE_VERIFY with its retries already spent.
        with m.Elif(~self.enable):
            m.d.sync += [
                state.eq(AutoLockState.IDLE),
                self.busy.eq(0),
                self.retry_count.eq(0),
                self.retry_request.eq(0),
                self.arm_lock_request.eq(0),
                self.load_offset.eq(0),
                self.load_polarity.eq(0),
                self.feature_match.eq(0),
                self.feature_failed.eq(0),
                zero_cross_seen.eq(0),
                prev_valid.eq(0),
            ]

        with m.Else():
            # Default pulses
            m.d.sync += [
                self.retry_request.eq(0),
                self.arm_lock_request.eq(0),

                self.load_offset.eq(0),
                self.load_polarity.eq(0),

                self.feature_match.eq(0),
                self.feature_failed.eq(0),
            ]

            # --------------------------------------------------------
            # IDLE
            # --------------------------------------------------------
            with m.If(state == AutoLockState.IDLE):

                m.d.sync += self.busy.eq(0)

                with m.If(self.scan_valid):
                    m.d.sync += [
                        state.eq(AutoLockState.TRACK),

                        self.busy.eq(1),

                        # This is the first point/sample the module has
                        # received. So for now, this point is both the
                        # minimum and the maximum.
                        min_value.eq(self.error_sample),
                        max_value.eq(self.error_sample),

                        min_position.eq(self.scan_code),
                        max_position.eq(self.scan_code),

                        # Start each sweep with a clean crossing history.
                        zero_cross_seen.eq(0),
                        prev_valid.eq(0),
                    ]

            # --------------------------------------------------------
            # TRACK
            # --------------------------------------------------------
            with m.Elif(state == AutoLockState.TRACK):
                m.d.sync += self.busy.eq(1)

                with m.If(self.scan_valid):

                    # Window rejection
                    with m.If(in_window):

                        # Minimum tracking
                        with m.If(self.error_sample < min_value):
                            # new lowest point
                            m.d.sync += [
                                min_value.eq(self.error_sample),
                                min_position.eq(self.scan_code)
                            ]

                        # Maximum tracking
                        with m.If(self.error_sample > max_value):
                            # new highest point
                            m.d.sync += [
                                max_value.eq(self.error_sample),
                                max_position.eq(self.scan_code)
                            ]

                        # Zero crossing (negative -> non-negative).
                        # prev_valid guards against using a stale
                        # previous_error carried over from an earlier
                        # sweep or from outside the window.
                        with m.If(prev_valid
                                  & (previous_error < 0)
                                  & (self.error_sample >= 0)):
                            m.d.sync += [
                                zero_cross_position.eq(self.scan_code),
                                zero_cross_seen.eq(1),
                            ]

                        m.d.sync += [
                            previous_error.eq(self.error_sample),
                            prev_valid.eq(1),
                        ]

                with m.If(self.scan_done):
                    m.d.sync += (
                        state.eq(AutoLockState.CHECK)
                    )

            # --------------------------------------------------------
            # CHECK
            # --------------------------------------------------------
            with m.Elif(state == AutoLockState.CHECK):

                m.d.sync += [
                    amplitude.eq(measured_amplitude),
                    width.eq(measured_width),
                ]

                # Verification
                with m.If(
                    # amplitude
                    (measured_amplitude >= self.amp_min)
                    &
                    # width
                    (measured_width >= self.width_min)
                    &
                    (measured_width <= self.width_max)
                    &
                    # a usable lock point was actually observed
                    zero_cross_seen
                    &
                    # slope (signed comparison now)
                    Mux(
                        self.slope_sign,
                        max_position > min_position,
                        min_position > max_position
                    )
                ):

                    m.d.sync += (
                        state.eq(AutoLockState.SUCCESS)
                    )

                with m.Else():
                    m.d.sync += (
                        state.eq(AutoLockState.RETRY)
                    )

            # --------------------------------------------------------
            # SUCCESS
            # --------------------------------------------------------
            with m.Elif(state == AutoLockState.SUCCESS):

                m.d.sync += [
                    self.feature_match.eq(1),

                    # AUDIT FIX (S2-8): this used to load the PC's
                    # GUESS (lock_x). Hand off the crossing the FPGA
                    # actually measured -- that is what "robust" FPGA-side
                    # verification is for, and packet 8.10 step 4 says
                    # "Move/hold slow DAC near lock position".
                    self.slow_lock_position.eq(zero_cross_position),

                    self.load_offset.eq(1),
                    self.load_polarity.eq(1),

                    self.arm_lock_request.eq(1),

                    state.eq(AutoLockState.IDLE)
                ]

            # --------------------------------------------------------
            # RETRY
            # --------------------------------------------------------
            with m.Elif(state == AutoLockState.RETRY):
                with m.If(self.retry_count < self.retry_limit):
                    m.d.sync += [
                        self.retry_count.eq(self.retry_count + 1),
                        self.retry_request.eq(1),
                        state.eq(AutoLockState.IDLE)
                    ]

                with m.Else():
                    m.d.sync += (
                        state.eq(AutoLockState.FAIL)
                    )

            # --------------------------------------------------------
            # FAIL
            # --------------------------------------------------------
            with m.Elif(state == AutoLockState.FAIL):
                m.d.sync += [
                    self.feature_failed.eq(1),
                    state.eq(AutoLockState.IDLE)
                ]

        return m
