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
"""

from amaranth import *
from amaranth.lib.enum import Enum


class AutoLockState(Enum, "IDLE SCAN TRACK CHECK SUCCESS RETRY FAIL"):
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

    def __init__(self):
        # -------------------------------------------------------------
        # Inputs
        # -------------------------------------------------------------

        # Clock/reset
        self.rst = Signal()

        # -------------------------------------------------------------
        # SCAN STREAM FROM FPGA

        self.scan_valid = Signal()
        self.scan_done = Signal()

        # The current value of the slow DAC scan position.
        self.scan_code = Signal(16) 

        self.error_sample = Signal(signed(24))
        # -------------------------------------------------------------

        # -------------------------------------------------------------
        # DESCRIPTOR INPUTS FROM THE PC
        
        # The allowed scan region where the feature should appear. 
        self.window_min = Signal(16)
        self.window_max = Signal(16)

        # Expected position of the minimum of the feature.
        self.expected_min_x = Signal(16)

        # Expected position of the maximum of the feature.
        self.expected_max_x = Signal(16)

        # Expected zero-crossing position
        self.lock_x = Signal(16)

        # Minimum required feature amplitude.
        self.amp_min = Signal(signed(24))

        # Acceptable feature width range
        self.width_min = Signal(16)
        self.width_max = Signal(16)

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


        # Lock preparation requests before enabling PI loop
        self.slow_lock_position = Signal(16)

        self.load_offset = Signal()
        self.load_polarity = Signal()

        self.arm_lock_request = Signal()


    def elaborate(self, platform):
        m = Module()

        # State
        state = Signal(AutoLockState, reset=AutoLockState.IDLE)

        # min and Max value for the error_sample
        min_value = Signal(
            signed(24),
            reset=2**23-1
        )

        max_value = Signal(
            signed(24),
            reset=-(2**23)
        )

        min_position = Signal(16)
        max_position = Signal(16)

        # Zero crossing tracker
        previous_error = Signal(signed(24))
        zero_cross_position = Signal(16)

        # Measured values
        amplitude = Signal(signed(25))
        width = Signal(17)

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


        with m.Else:
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

                        # This is the first point/sample the module has received. 
                        # So for now, this point is both the minimum and the maximum.
                        min_value.eq(self.error_sample),
                        max_value.eq(self.error_sample),

                        min_position.eq(self.scan_code),
                        max_position.eq(self.scan_code),
                    ]

            # --------------------------------------------------------
            # TRACK
            # --------------------------------------------------------
            with m.If(state == AutoLockState.TRACK):
                m.d.sync += self.busy.eq(1)

                with m.If(self.scan_valid):

                    # Window rejection
                    with m.If(
                        (self.scan_code >= self.window_min)
                        &
                        (self.scan_code <= self.window_max)
                    ):

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

                        # Zero crossing
                        with m.If(
                            (previous_error < 0)
                            &
                            (self.error_sample >= 0)
                        ):
                            m.d.sync += (
                                zero_cross_position.eq(self.scan_code)
                            )

                        m.d.sync += (
                            previous_error.eq(self.error_sample)
                        )

                with m.If(self.scan_done):
                    m.d.sync += (
                        state.eq(AutoLockState.CHECK)
                    )


            # --------------------------------------------------------
            # CHECK
            # --------------------------------------------------------
            with m.If(state == AutoLockState.CHECK):

                m.d.sync += [
                    amplitude.eq(max_value - min_value),

                    width.eq(
                        Mux(
                            max_position >= min_position,
                            max_position-min_position,
                            min_position-max_position
                        )
                    )

                ]


                # Verification
                with m.If(
                    # amplitude
                    (amplitude >= self.amp_min)
                    &
                    # width
                    (width >= self.width_min)
                    &
                    (width <= self.width_max)
                    &
                    # slope
                    Mux(
                        self.slope_sign,
                        max_position > min_position,
                        min_position > max_position
                    )
                ):

                    m.d.sync += (
                        state.eq(AutoLockState.SUCCESS)
                    )

                .Else(
                    m.d.sync += (
                        state.eq(AutoLockState.RETRY)
                    )
                )

            # --------------------------------------------------------
            # SUCCESS
            # --------------------------------------------------------
            with m.If(state == AutoLockState.SUCCESS):

                m.d.sync += [
                    self.feature_match.eq(1),

                    self.slow_lock_position.eq(
                        self.lock_x
                    ),

                    self.load_offset.eq(1),
                    self.load_polarity.eq(1),

                    self.arm_lock_request.eq(1),

                    state.eq(AutoLockState.IDLE)
                ]

            # --------------------------------------------------------
            # RETRY
            # --------------------------------------------------------
            with m.If(state == AutoLockState.RETRY):
                with m.If(self.retry_count < self.retry_limit):
                    m.d.sync += [
                        self.retry_count.eq(self.retry_count + 1),
                        self.retry_request.eq(1),
                        state.eq(AutoLockState.IDLE)
                    ]

                .Else:
                    m.d.sync += (
                        state.eq(AutoLockState.FAIL)
                    )

            # --------------------------------------------------------
            # FAIL
            # --------------------------------------------------------
            with m.If(state == AutoLockState.FAIL):
                m.d.sync += [
                    self.feature_failed.eq(1),
                    state.eq(AutoLockState.IDLE)
                ]

        return m