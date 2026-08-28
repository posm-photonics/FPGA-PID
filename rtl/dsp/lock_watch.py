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
        #
        # AUDIT FIX (S3-6): these were UNSIGNED, while the SAME signals
        # were reinterpreted with .as_signed() further down for the jump
        # check. One module treated one signal two different ways.
        # Driven from the two's-complement DAC path with fast_min = 0 and
        # fast_max = 65535, the unsigned rail test asserted whenever the
        # output was 0 or -1 (0xFFFF reads as 65535 unsigned), which is
        # exactly where a healthy servo sits.
        #
        # The controller domain is signed throughout, so these are signed
        # now and the rail comparison means what it says.
        self.fast_output = Signal(signed(self.DAC_WIDTH))
        self.fast_min = Signal(signed(self.DAC_WIDTH))
        self.fast_max = Signal(signed(self.DAC_WIDTH))

        self.slow_output = Signal(signed(self.DAC_WIDTH))
        self.slow_min = Signal(signed(self.DAC_WIDTH))
        self.slow_max = Signal(signed(self.DAC_WIDTH))

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

        # Maximum allowed sudden change in DAC output, measured over
        # jump_window samples rather than between adjacent clock cycles.
        self.jump_limit = Signal(self.DAC_WIDTH)

        # AUDIT FIX (S2-5): the jump detector used to compare the fast
        # output against its value on the IMMEDIATELY PRECEDING clock.
        # At 125 MHz a fast servo responding to a real disturbance
        # routinely moves more than jump_limit codes in one cycle, so the
        # detector fired constantly and requested spurious relocks.
        #
        # "Sudden control jumps" (packet 8.12) means sudden on the
        # timescale of the lock, not of the sample clock. The reference
        # sample is now taken every 2^jump_window_shift samples, so the
        # comparison spans a configurable window.
        self.jump_window_shift = Signal(5, init=8)   # 256 samples default

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

        # Previous DAC storage (sampled once per jump window)
        previous_fast = Signal(signed(self.DAC_WIDTH))
        history_valid  = Signal()
        jump_counter   = Signal(32)
        jump_tick      = Signal()
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
        # AUDIT FIX (S3-6): `diff` used to be declared here AND again a
        # few lines below, so the first declaration became a dangling,
        # undriven signal. Declared once now.
        diff = Signal(signed(self.DAC_WIDTH + 1))
        abs_diff = Signal(self.DAC_WIDTH + 2)

        # Error absolute value
        error_abs = Signal(self.ERROR_WIDTH)

        # Get absolute value of the error
        with m.If(self.error_value < 0):
            m.d.comb += error_abs.eq(-self.error_value)
        with m.Else():
            m.d.comb += error_abs.eq(self.error_value)

        # Threshold checks
        m.d.comb += [
            error_bad.eq(error_abs > self.max_error),
            # Signed comparisons now that the ports are signed.
            fast_rail.eq((self.fast_output <= self.fast_min)
                         | (self.fast_output >= self.fast_max)),
            slow_rail.eq((self.slow_output <= self.slow_min)
                         | (self.slow_output >= self.slow_max)),
            adc_bad.eq(~self.adc_valid),
            saturation_bad.eq(self.fast_saturated | self.slow_saturated),
        ]

        # Jump detection over a configurable window (see jump_window_shift).
        m.d.comb += [
            jump_tick.eq(jump_counter == 0),
            diff.eq(self.fast_output - previous_fast),
        ]
        with m.If(diff < 0):
            m.d.comb += abs_diff.eq(-diff)
        with m.Else():
            m.d.comb += abs_diff.eq(diff)
        m.d.comb += output_jump.eq(
            self.lock_active & history_valid & (abs_diff > self.jump_limit))

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
                jump_counter.eq(0),
            ]


        with m.Else():
            # Store DAC history once per jump window rather than every
            # clock (S2-5). Between refreshes previous_fast holds, so the
            # comparison spans 2^jump_window_shift samples.
            with m.If(jump_tick):
                m.d.sync += [
                    previous_fast.eq(self.fast_output),
                    history_valid.eq(1),
                    jump_counter.eq((1 << self.jump_window_shift) - 1),
                ]
            with m.Else():
                m.d.sync += jump_counter.eq(jump_counter - 1)
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