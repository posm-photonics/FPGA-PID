# ramp_scan.py
# Triangle ramp scan generator for POSM MTS laser lock
#
# Drives DAC_SLOW during wide scan and zoom scan modes
# before lock engagement.
#
# Two modes:
#   WIDE : sweeps ramp_min to ramp_max
#   ZOOM : sweeps ramp_center-ramp_width to ramp_center+ramp_width
#
# Speed control:
#   ramp_tick_div divides the sample clock
#   ramp advances by ramp_step every ramp_tick_div cycles
#
# Latency: 1 clock cycle
# Reset: ramp starts at ramp_min, direction up

from amaranth import *
from amaranth.sim import *


class RampScan(Elaboratable):
    """
    Triangle ramp scan generator.

    Parameters
    ----------
    dac_w : int
        DAC output width in bits (default 16)

    Ports
    -----
    enable        : input  - enables ramp generation
    zoom_mode     : input  - selects zoom scan around center
    ramp_min      : signed input, dac_w  - wide scan minimum
    ramp_max      : signed input, dac_w  - wide scan maximum
    ramp_step     : unsigned input, dac_w - step size per tick
    ramp_tick_div : unsigned input, 16 bits - clock divider
    ramp_center   : signed input, dac_w  - zoom scan center
    ramp_width    : unsigned input, dac_w - zoom scan half-width
    ramp_out      : signed output, dac_w - current ramp value
    ramp_valid    : output - ramp_out is valid
    at_min        : output - ramp is at minimum
    at_max        : output - ramp is at maximum
    cycle_done    : output - one pulse per completed scan cycle
    ramp_cycle_count : output, 32 bits - total cycles completed
    """

    def __init__(self, dac_w=16):
        self.dac_w = dac_w

        # --- input ports ---
        self.enable        = Signal()
        self.zoom_mode     = Signal() # zoom_mode = 1 → use zoom bounds instead of full scan

        # wide scan bounds
        self.ramp_min      = Signal(signed(dac_w))
        self.ramp_max      = Signal(signed(dac_w))
        self.ramp_step     = Signal(dac_w)
        self.ramp_tick_div = Signal(16)

        # zoom scan bounds
        self.ramp_center   = Signal(signed(dac_w))
        self.ramp_width    = Signal(dac_w)

        # --- output ports ---
        self.ramp_out        = Signal(signed(dac_w))
        self.ramp_valid      = Signal()
        self.at_min          = Signal()
        self.at_max          = Signal()
        self.cycle_done      = Signal()
        self.ramp_cycle_count = Signal(32)
        # AUDIT FIX (S1-4): one pulse each time the ramp actually
        # advances by a step. trace_capture's sample strobe was wired to
        # cycle_done, which pulses once per COMPLETE sweep, so the trace
        # captured one point per full scan instead of one per ramp step
        # (measured: 11 samples in 3.2 ms, trace_ready never asserted,
        # FSM stuck in WIDE_SCAN). This is the strobe trace_capture's own
        # docstring asks for: "the same cadence as ramp_scan's updates".
        self.o_tick          = Signal()

    def elaborate(self, platform):
        m = Module()

        dac_w = self.dac_w

        # -------------------------------------------------------
        # Internal state
        # -------------------------------------------------------
        ramp_value   = Signal(signed(dac_w))
        # `reset=` is deprecated in Amaranth 0.5 and removed in 0.6.
        direction_up = Signal(init=1)   # start going up
        tick_counter = Signal(16)       # clock divider counter

        # -------------------------------------------------------
        # Zoom scan computed bounds
        # zoom_lo = ramp_center - ramp_width
        # zoom_hi = ramp_center + ramp_width
        # Use wider signals to catch overflow before clamping
        # -------------------------------------------------------
        zoom_lo = Signal(signed(dac_w + 1))
        zoom_hi = Signal(signed(dac_w + 1))

        m.d.comb += [
            zoom_lo.eq(self.ramp_center - self.ramp_width),
            zoom_hi.eq(self.ramp_center + self.ramp_width),
        ]

        # -------------------------------------------------------
        # Active bounds mux
        # Selects between wide and zoom bounds
        # -------------------------------------------------------
        active_min = Signal(signed(dac_w))
        active_max = Signal(signed(dac_w))

        # AUDIT FIX (S2-4): these used to be
        #     active_min.eq(zoom_lo[:dac_w])
        #     active_max.eq(zoom_hi[:dac_w])
        # so the guard bit that the comment above says exists to "catch
        # overflow before clamping" was computed and then thrown away by
        # the slice. With ramp_center = 20000 and ramp_width = 20000,
        # zoom_hi = 40000 truncated to -25536 and active_max ended up
        # BELOW active_min. Both direction tests then fired immediately
        # and the ramp oscillated between two wrong endpoints at the full
        # tick rate: a large-amplitude square wave on the piezo.
        #
        # Clamp to the representable signed range instead of truncating.
        # Linien achieves the same thing by instantiating its sweep
        # Limit() one bit wider than the data and sign-extending min/max
        # into that guard bit.
        dac_max = (1 << (dac_w - 1)) - 1
        dac_min = -(1 << (dac_w - 1))

        zoom_lo_clamped = Signal(signed(dac_w))
        zoom_hi_clamped = Signal(signed(dac_w))

        with m.If(zoom_lo > dac_max):
            m.d.comb += zoom_lo_clamped.eq(dac_max)
        with m.Elif(zoom_lo < dac_min):
            m.d.comb += zoom_lo_clamped.eq(dac_min)
        with m.Else():
            m.d.comb += zoom_lo_clamped.eq(zoom_lo)

        with m.If(zoom_hi > dac_max):
            m.d.comb += zoom_hi_clamped.eq(dac_max)
        with m.Elif(zoom_hi < dac_min):
            m.d.comb += zoom_hi_clamped.eq(dac_min)
        with m.Else():
            m.d.comb += zoom_hi_clamped.eq(zoom_hi)

        with m.If(self.zoom_mode):
            # Zoom Bounds mode
            m.d.comb += [
                active_min.eq(zoom_lo_clamped),
                active_max.eq(zoom_hi_clamped),
            ]
        with m.Else():
            # Full scan mode
            m.d.comb += [
                active_min.eq(self.ramp_min),
                active_max.eq(self.ramp_max),
            ]

        # -------------------------------------------------------
        # Tick generation
        # ramp_tick_div = 1 means advance every cycle
        # ramp_tick_div = N means advance every N cycles
        # -------------------------------------------------------
        tick = Signal()  # one pulse when ramp should advance

        with m.If(self.ramp_tick_div <= 1):
            # advance every cycle
            m.d.comb += tick.eq(self.enable)
        with m.Else():
            # only advance when counter hits zero
            m.d.comb += tick.eq(
                self.enable & (tick_counter == 0)
            )
            with m.If(self.enable):
                with m.If(tick_counter == 0):
                    # reload counter
                    m.d.sync += tick_counter.eq(
                        self.ramp_tick_div - 1
                    )
                with m.Else():
                    # count down every cycle
                    m.d.sync += tick_counter.eq(
                        tick_counter - 1
                    )
            with m.Else():
                # reset counter when disabled
                m.d.sync += tick_counter.eq(0)

        # -------------------------------------------------------
        # Ramp state machine
        # -------------------------------------------------------
        # next ramp candidates
        ramp_up_next   = Signal(signed(dac_w + 1))
        ramp_down_next = Signal(signed(dac_w + 1))

        # AUDIT FIX (S2-4, second half): ramp_step = 0 froze the ramp.
        # ramp_up_next equalled ramp_value, the endpoint test never
        # fired, cycle_done never pulsed, and lock_fsm.zoom_complete
        # never asserted, so the FSM hung forever in a scan state (it has
        # no timeouts). ramp_step defaults to a nonzero value in the
        # register bank, but nothing stopped software writing 0.
        #
        # Clamp to a minimum of 1: the slowest legitimate scan is one
        # code per tick, and the tick divider is the correct knob for
        # slowing the scan down further.
        step_eff = Signal(dac_w)
        with m.If(self.ramp_step == 0):
            m.d.comb += step_eff.eq(1)
        with m.Else():
            m.d.comb += step_eff.eq(self.ramp_step)

        m.d.comb += [
            ramp_up_next.eq(ramp_value + step_eff),
            ramp_down_next.eq(ramp_value - step_eff),
        ]

        # cycle done pulse
        m.d.sync += self.cycle_done.eq(0)

        with m.If(~self.enable):
            # when disabled hold at active_min and wait
            m.d.sync += [
                ramp_value.eq(active_min),
                direction_up.eq(1),
                self.at_min.eq(1),
                self.at_max.eq(0),
            ]

        with m.Elif(tick): # Only update ramp on tick event.
            with m.If(direction_up):
                # going up
                with m.If(ramp_up_next >= active_max.as_signed()):
                    # reached or passed max — clamp and reverse
                    m.d.sync += [
                        ramp_value.eq(active_max),
                        direction_up.eq(0),
                        self.at_max.eq(1),
                        self.at_min.eq(0),
                    ]
                with m.Else():
                    m.d.sync += [
                        ramp_value.eq(ramp_up_next[:dac_w]),
                        self.at_max.eq(0),
                        self.at_min.eq(0),
                    ]

            with m.Else():
                # going down
                with m.If(ramp_down_next <= active_min.as_signed()):
                    # reached or passed min — clamp, reverse, count cycle
                    m.d.sync += [
                        ramp_value.eq(active_min),
                        direction_up.eq(1),
                        self.at_min.eq(1),
                        self.at_max.eq(0),
                        self.cycle_done.eq(1),
                        self.ramp_cycle_count.eq(
                            self.ramp_cycle_count + 1
                        ),
                    ]
                with m.Else():
                    m.d.sync += [
                        ramp_value.eq(ramp_down_next[:dac_w]),
                        self.at_min.eq(0),
                        self.at_max.eq(0),
                    ]

        # -------------------------------------------------------
        # Output
        # -------------------------------------------------------
        m.d.sync += [
            self.ramp_out.eq(ramp_value),
            self.ramp_valid.eq(self.enable),
            # Registered so o_tick lines up with the ramp_out sample it
            # produced, rather than leading it by a cycle.
            self.o_tick.eq(tick),
        ]

        return m
