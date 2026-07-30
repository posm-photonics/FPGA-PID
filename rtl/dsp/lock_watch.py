"""
POSM FPGA MTS Laser-Lock Core
Module: lock_watch.py

Purpose
-------
lock_watch is the security guard of the lock. It watches the locked laser 
after the lock is active. It checks if things are still healthy.
If the lock starts getting bad, it asks the system to relock or go to fault.

Architectural role
------------------
It:
    - observes lock quality
    - detects degraded operation
    - requests relock
    - requests fault handling

Timing:
    Synchronous to clk.

Reset:
    Synchronous reset.
"""

from amaranth import *
from amaranth.hdl import unsigned


class LockWatch(Elaboratable):
    # ---------------------------------------------------------
    # Constants
    # ---------------------------------------------------------

    ERROR_WIDTH = 24
    DAC_WIDTH = 16
    COUNT_WIDTH = 16


    def __init__(self):
        # -------------------------------------------------------------
        # Inputs
        # -------------------------------------------------------------

        # Clock / reset
        self.enable = Signal()

        # Lock state input
        self.lock_active = Signal()

        # Error monitoring
        self.error_value = Signal(signed(self.ERROR_WIDTH))
        self.max_error = Signal(signed(self.ERROR_WIDTH))

        # DAC monitoring
        self.fast_output = Signal(self.DAC_WIDTH)
        self.fast_min = Signal(self.DAC_WIDTH)
        self.fast_max = Signal(self.DAC_WIDTH)

        self.slow_output = Signal(self.DAC_WIDTH)
        self.slow_min = Signal(self.DAC_WIDTH)
        self.slow_max = Signal(self.DAC_WIDTH)

        # Direct limiter status
        self.fast_saturated = Signal()
        self.slow_saturated = Signal()

        # ADC validity
        self.adc_valid = Signal()

        # Configuration

        # How long can the DAC stay saturated before we declare a problem?
        self.saturation_timeout = Signal(self.COUNT_WIDTH)

        # How many missing ADC samples are allowed before declaring an ADC fault?
        self.adc_timeout = Signal(self.COUNT_WIDTH)

        # Maximum allowed sudden change in DAC output
        self.jump_limit = Signal(self.DAC_WIDTH)

        # How long can the error stay too large?
        self.error_timeout = Signal(self.COUNT_WIDTH)

        # -------------------------------------------------------------
        # Outputs
        # -------------------------------------------------------------
        self.lock_healthy = Signal()

        # The laser is probably no longer locked => Something bad happened!
        self.unlock_detected = Signal()

        self.relock_request = Signal()
        self.fault_request = Signal()


        # Diagnostics
        self.error_violation = Signal()     # The error signal is too large
        self.fast_rail_warning = Signal()   # The fast DAC output is close to its limit
        self.slow_rail_warning = Signal()   # The slow DAC output is close to its limit
        self.adc_fault_active = Signal()    # The ADC input is not reliable
        self.sat_fault_active = Signal()    # DAC saturated for too long

    def elaborate(self, platform):

        m = Module()

        # Previous DAC storage
        previous_fast = Signal(self.DAC_WIDTH)
        history_valid  = Signal()
        # Counters
        sat_counter = Signal(self.COUNT_WIDTH)
        adc_counter = Signal(self.COUNT_WIDTH)
        error_counter = Signal(self.COUNT_WIDTH)

        # Internal conditions
        error_bad = Signal()
        fast_rail = Signal()
        slow_rail = Signal()
        adc_bad = Signal()
        saturation_bad = Signal()
        output_jump = Signal()
        diff = Signal(signed(17))

        # Error absolute value
        error_abs = Signal(self.ERROR_WIDTH)

        # Get absolute value of the error
        with m.If(self.error_value < 0):
            m.d.comb += error_abs.eq(-self.error_value)
        with m.Else():
            m.d.comb += error_abs.eq(self.error_value)

        # Threshold checks
        diff = Signal(signed(17))
        abs_diff = Signal(17)

        m.d.comb += [
            error_bad.eq(error_abs > self.max_error),
            fast_rail.eq((self.fast_output <= self.fast_min) | (self.fast_output >= self.fast_max)),
            slow_rail.eq((self.slow_output <= self.slow_min) | (self.slow_output >= self.slow_max)),
            adc_bad.eq(~self.adc_valid),
            saturation_bad.eq(self.fast_saturated | self.slow_saturated),
        ]

        m.d.comb += [
            diff.eq(self.fast_output.as_signed() - previous_fast.as_signed()),
        ]
        with m.If(diff < 0):
            m.d.comb += abs_diff.eq(-diff)
        with m.Else():
            m.d.comb += abs_diff.eq(diff)
        m.d.comb += output_jump.eq(self.lock_active & history_valid & (abs_diff > self.jump_limit))

        # Diagnostics
        m.d.comb += [
            self.error_violation.eq(error_bad),
            self.fast_rail_warning.eq(fast_rail),
            self.slow_rail_warning.eq(slow_rail),
            self.adc_fault_active.eq(adc_counter >= self.adc_timeout),
            self.sat_fault_active.eq(sat_counter >= self.saturation_timeout)
        ]

        # Main monitoring process
        with m.If(~self.enable):
            m.d.sync += [
                self.lock_healthy.eq(0),
                self.unlock_detected.eq(0),
                self.relock_request.eq(0),
                self.fault_request.eq(0),
                sat_counter.eq(0),
                adc_counter.eq(0),
                error_counter.eq(0),
                history_valid.eq(0),
            ]


        with m.Else():
            # Store DAC history
            m.d.sync += previous_fast.eq(self.fast_output)
            m.d.sync += history_valid.eq(1)
            # ADC timeout counter
            with m.If(adc_bad):
                with m.If(adc_counter < self.adc_timeout):
                    m.d.sync += adc_counter.eq(adc_counter + 1)
            
            with m.Else():
                m.d.sync += adc_counter.eq(0)

            # Saturation counter
            with m.If(saturation_bad):
                with m.If(sat_counter < self.saturation_timeout):
                    m.d.sync += sat_counter.eq(sat_counter + 1)

            with m.Else():
                m.d.sync += sat_counter.eq(0)


            # Error persistence counter
            with m.If(error_bad):
                with m.If(error_counter < self.error_timeout):
                    m.d.sync += error_counter.eq(error_counter + 1)

            with m.Else():
                m.d.sync += error_counter.eq(0)

            # Decision logic
            m.d.sync += [
                self.lock_healthy.eq(
                    self.lock_active
                    &
                    ~error_bad
                    &
                    ~saturation_bad
                    &
                    ~adc_bad
                    &
                    ~output_jump
                )
            ]


            # ADC faults dominate
            with m.If(adc_counter >= self.adc_timeout):
                m.d.sync += [
                    self.fault_request.eq(1),
                    self.unlock_detected.eq(1)
                ]

            # Saturation faults
            with m.Elif(sat_counter >= self.saturation_timeout):
                m.d.sync += [
                    self.fault_request.eq(1),
                    self.unlock_detected.eq(1)
                ]

            # Error loss of lock
            with m.Elif(error_counter >= self.error_timeout):
                m.d.sync += [
                    self.relock_request.eq(1),
                    self.unlock_detected.eq(1)
                ]

            # Output jump
            with m.Elif(output_jump):
                m.d.sync += [
                    self.relock_request.eq(1),
                    self.unlock_detected.eq(1)
                ]

            with m.Else():
                m.d.sync += [
                    self.relock_request.eq(0),
                    self.unlock_detected.eq(0)
                ]

        return m