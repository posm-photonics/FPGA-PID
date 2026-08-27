"""
register_bank.py

Global control/status register file for the POSM FPGA MTS lock core.
Implements the "Global control/status" block from section 11.1 of the
onboarding packet:

    0x000 VERSION       R
    0x004 CONTROL       R/W
    0x008 STATUS        R
    0x00C MODE          R/W
    0x010 FAULT_STATUS  R   (sticky)
    0x014 FAULT_ENABLE  R/W
    0x018 FAULT_CLEAR   W   (write-one-to-clear)
    0x01C DEBUG_SELECT  R/W

This module owns only the *global* registers. Per-block registers
(ADC config, error calc, fast/slow controller, ramp, trace, autolock,
lock watch, DAC config, latency) are expected to live in their own
modules (see trace_capture.py and slow_recenter.py for two examples)
and be address-decoded onto the same bus by a parent bus mux/decoder
in lock_core_top. This keeps each block's register file next to the
logic it configures, per the repo layout in section 13, while still
giving one flat byte-addressed view to the PC (section 8.1,
register_bank.sv: "Provides the PC/GUI/POSMq software contract").

Bus interface (kept deliberately simple -- a synchronous, single-
cycle-latency register-file bus, not AXI-Lite. Section 15.2 explicitly
says not to overbuild the bus at the start; wrap this in AXI-Lite
later if/when needed):

    adr    : Signal(12)      byte address, word-aligned (adr[1:0] ignored)
    dat_w  : Signal(32)      write data
    dat_r  : Signal(32)      read data, valid the cycle after `stb & ~we`
    we     : Signal(1)       write strobe (qualifies dat_w)
    stb    : Signal(1)       cycle select / read-or-write strobe

Status/fault inputs are sampled combinationally from other modules
(lock_fsm, adc_guard, output_limiter, lock_watch, robust_autolock,
etc.) and packed into STATUS / FAULT_STATUS. Sticky fault bits latch
on their `fault_in` pulse and only clear via FAULT_CLEAR writes ANDed
with FAULT_ENABLE, per the "explicit fault clear" requirement in
section 10.2.
"""

from amaranth import Module, Signal, Elaboratable, Cat, Mux, Const, signed

from .register_defs import (
    ADDR_VERSION, ADDR_CONTROL, ADDR_STATUS, ADDR_MODE,
    ADDR_FAULT_STATUS, ADDR_FAULT_ENABLE, ADDR_FAULT_CLEAR,
    ADDR_DEBUG_SELECT,
    CTRL_GLOBAL_ENABLE, CTRL_SOFT_RESET, CTRL_OUTPUTS_ENABLE,
    CTRL_LOCK_ENABLE_REQUEST, CTRL_HOLD_REQUEST, CTRL_FAULT_CLEAR_REQUEST,
    CTRL_INTEGRATOR_RESET, CTRL_INTEGRATOR_LOAD, CTRL_TRACE_CAPTURE_ENABLE,
    CTRL_AUTOLOCK_ENABLE, CTRL_SLOW_RECENTER_ENABLE,
    CTRL_ADC_TEST_PATTERN_EN, CTRL_DAC_TEST_PATTERN_EN,
    ADDR_FAST_KP, ADDR_FAST_KI, ADDR_FAST_OUT_MIN, ADDR_FAST_OUT_MAX,
    ADDR_FAST_OUT_SAFE, ADDR_RAMP_MIN, ADDR_RAMP_MAX, ADDR_RAMP_STEP,
    ADDR_RAMP_TICK_DIV, ADDR_RAMP_CENTER, ADDR_RAMP_WIDTH,
    ADDR_AUTOLOCK_WINDOW_MIN, ADDR_AUTOLOCK_WINDOW_MAX,
    ADDR_AUTOLOCK_EXPECTED_MIN_X, ADDR_AUTOLOCK_EXPECTED_MAX_X,
    ADDR_AUTOLOCK_LOCK_X, ADDR_AUTOLOCK_AMP_MIN, ADDR_AUTOLOCK_WIDTH_MIN,
    ADDR_AUTOLOCK_WIDTH_MAX, ADDR_AUTOLOCK_SLOPE_SIGN,
    ADDR_AUTOLOCK_RETRY_LIMIT,
    ADDR_PDH_CONTROL, ADDR_PDH_MOD_FREQ, ADDR_PDH_MOD_AMP,
    ADDR_PDH_DEMOD_PHASE, ADDR_PDH_LPF_ALPHA,
    PDH_CTRL_ENABLE
)

VERSION_ID = 0x0003_0000  # major=3 (matches "Version 3 Draft"), minor=0

# Number of independent sticky fault bits tracked here. Concrete
# meaning (ADC overrange, stuck rail, saturation timeout, autolock
# failed, lock check failed, external interlock, ...) is assigned by
# the caller when wiring fault_in / fault_enable; this module just
# implements the sticky-latch-with-explicit-clear mechanics from
# section 10.2.
NUM_FAULTS = 12


class RegisterBank(Elaboratable):
    def __init__(self, num_faults=NUM_FAULTS):
        self.num_faults = num_faults

        # --- bus ---
        self.adr   = Signal(12)
        self.dat_w = Signal(32)
        self.dat_r = Signal(32)
        self.we    = Signal()
        self.stb   = Signal()

        # --- CONTROL: decoded output bits (comb, live view of register) ---
        self.global_enable        = Signal()
        self.soft_reset           = Signal()
        self.outputs_enable       = Signal()
        self.lock_enable_request  = Signal()
        self.hold_request         = Signal()
        self.integrator_reset     = Signal()
        self.integrator_load      = Signal()
        self.trace_capture_enable = Signal()
        self.autolock_enable      = Signal()
        self.slow_recenter_enable = Signal()
        self.adc_test_pattern_en  = Signal()
        self.dac_test_pattern_en  = Signal()

        # fault_clear_request is a one-cycle pulse, not a level: the
        # bit is write-only-effective, auto-clears itself the cycle
        # after being set so software doesn't have to clear it back.
        self.fault_clear_pulse    = Signal()

        # --- MODE: passthrough R/W register (interpreted by lock_fsm) ---
        self.mode = Signal(8)

        # --- Placeholder configuration hooks for the fast loop and scan path ---
        # These are intentionally minimal wiring points so the top-level wrapper
        # can use register-bank signals without introducing a larger redesign.
        # They are expected to be mapped onto the software register map later.
        self.fast_kp = Signal(signed(18), reset=0)
        self.fast_ki = Signal(signed(18), reset=0)
        self.fast_out_min = Signal(signed(16), reset=-32768)
        self.fast_out_max = Signal(signed(16), reset=32767)
        self.fast_out_safe = Signal(signed(16), reset=0)
        self.ramp_min = Signal(signed(16), reset=-32768)
        self.ramp_max = Signal(signed(16), reset=32767)
        self.ramp_step = Signal(16, reset=32)
        self.ramp_tick_div = Signal(16, reset=32)
        self.ramp_center = Signal(signed(16), reset=0)
        self.ramp_width = Signal(16, reset=1024)
        self.autolock_window_min = Signal(16, reset=0)
        self.autolock_window_max = Signal(16, reset=65535)
        self.autolock_expected_min_x = Signal(16, reset=0)
        self.autolock_expected_max_x = Signal(16, reset=0)
        self.autolock_lock_x = Signal(16, reset=0)
        self.autolock_amp_min = Signal(signed(24), reset=0)
        self.autolock_width_min = Signal(16, reset=0)
        self.autolock_width_max = Signal(16, reset=65535)
        self.autolock_slope_sign = Signal(reset=0)
        self.autolock_retry_limit = Signal(8, reset=3)

        # --- PDH configuration hooks ---
        self.pdh_enable = Signal()
        self.pdh_mod_freq = Signal(32, reset=0)
        self.pdh_mod_amp = Signal(16, reset=0)
        self.pdh_demod_phase = Signal(32, reset=0)
        self.pdh_lpf_alpha = Signal(5, reset=8)

        # --- STATUS: inputs from the rest of the design (comb in) ---
        self.state          = Signal(4)   # from lock_fsm
        self.locked          = Signal()
        self.scanning         = Signal()
        self.saturation       = Signal()
        self.trace_ready      = Signal()
        self.fault_active     = Signal()

        # --- FAULT_STATUS: sticky fault inputs ---
        # fault_in[i] is a pulse ("this fault condition occurred").
        # It latches into a sticky bit that only clears via
        # FAULT_CLEAR & FAULT_ENABLE (section 10.2: "do not silently
        # auto-recover ... require deliberate handling").
        self.fault_in = Signal(self.num_faults)

        # --- DEBUG_SELECT passthrough ---
        self.debug_select = Signal(8)

    def elaborate(self, platform):
        m = Module()

        control      = Signal(32)
        pdh_control  = Signal(32)
        fault_enable = Signal(self.num_faults)
        fault_sticky = Signal(self.num_faults)

        word_adr = self.adr[2:]  # word address (drop byte-within-word bits)

        # ---------------- CONTROL bit decode (comb) ----------------
        m.d.comb += [
            self.global_enable.eq(control[CTRL_GLOBAL_ENABLE]),
            self.soft_reset.eq(control[CTRL_SOFT_RESET]),
            self.outputs_enable.eq(control[CTRL_OUTPUTS_ENABLE]),
            self.lock_enable_request.eq(control[CTRL_LOCK_ENABLE_REQUEST]),
            self.hold_request.eq(control[CTRL_HOLD_REQUEST]),
            self.integrator_reset.eq(control[CTRL_INTEGRATOR_RESET]),
            self.integrator_load.eq(control[CTRL_INTEGRATOR_LOAD]),
            self.trace_capture_enable.eq(control[CTRL_TRACE_CAPTURE_ENABLE]),
            self.autolock_enable.eq(control[CTRL_AUTOLOCK_ENABLE]),
            self.slow_recenter_enable.eq(control[CTRL_SLOW_RECENTER_ENABLE]),
            self.adc_test_pattern_en.eq(control[CTRL_ADC_TEST_PATTERN_EN]),
            self.dac_test_pattern_en.eq(control[CTRL_DAC_TEST_PATTERN_EN]),
        ]

        # ---------------- PDH decode (comb) ----------------
        m.d.comb += [
            self.pdh_enable.eq(pdh_control[PDH_CTRL_ENABLE]),
        ]

        # ---------------- STATUS pack (comb, read-only) ----------------
        status_word = Signal(32)
        m.d.comb += status_word.eq(Cat(
            self.state,          # [3:0]
            self.locked,         # [4]
            self.scanning,       # [5]
            self.saturation,     # [6]
            self.trace_ready,    # [7]
            self.fault_active,   # [8]
        ))

        # ---------------- sticky fault latch + explicit clear -------
        clear_pulse = Signal()
        with m.If(self.stb & self.we & (word_adr == (ADDR_FAULT_CLEAR >> 2))):
            m.d.comb += clear_pulse.eq(1)

        clear_mask = Signal(self.num_faults)
        m.d.comb += clear_mask.eq(Mux(clear_pulse,
                                       self.dat_w[:self.num_faults],
                                       0))

        m.d.sync += fault_sticky.eq(
            (fault_sticky & ~(clear_mask & fault_enable))
            | (self.fault_in & fault_enable)
        )

        # fault_clear_request in CONTROL is a self-clearing pulse bit,
        # exposed for modules that want a "clear everything" trigger
        # distinct from the masked FAULT_CLEAR write above.
        fault_clear_req_r = Signal()
        m.d.sync += fault_clear_req_r.eq(control[CTRL_FAULT_CLEAR_REQUEST])
        m.d.comb += self.fault_clear_pulse.eq(
            control[CTRL_FAULT_CLEAR_REQUEST] & ~fault_clear_req_r
        )
        with m.If(self.fault_clear_pulse):
            m.d.sync += control.eq(control & ~(1 << CTRL_FAULT_CLEAR_REQUEST))
            m.d.sync += fault_sticky.eq(0)

        # ---------------- write decode ----------------
        with m.If(self.stb & self.we):
            with m.Switch(word_adr):
                with m.Case(ADDR_CONTROL >> 2):
                    m.d.sync += control.eq(self.dat_w)
                with m.Case(ADDR_MODE >> 2):
                    m.d.sync += self.mode.eq(self.dat_w[:8])
                with m.Case(ADDR_FAULT_ENABLE >> 2):
                    m.d.sync += fault_enable.eq(self.dat_w[:self.num_faults])
                with m.Case(ADDR_DEBUG_SELECT >> 2):
                    m.d.sync += self.debug_select.eq(self.dat_w[:8])
                with m.Case(ADDR_FAST_KP >> 2):
                    m.d.sync += self.fast_kp.eq(self.dat_w[:18].as_signed())
                with m.Case(ADDR_FAST_KI >> 2):
                    m.d.sync += self.fast_ki.eq(self.dat_w[:18].as_signed())
                with m.Case(ADDR_FAST_OUT_MIN >> 2):
                    m.d.sync += self.fast_out_min.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_FAST_OUT_MAX >> 2):
                    m.d.sync += self.fast_out_max.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_FAST_OUT_SAFE >> 2):
                    m.d.sync += self.fast_out_safe.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_RAMP_MIN >> 2):
                    m.d.sync += self.ramp_min.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_RAMP_MAX >> 2):
                    m.d.sync += self.ramp_max.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_RAMP_STEP >> 2):
                    m.d.sync += self.ramp_step.eq(self.dat_w[:16])
                with m.Case(ADDR_RAMP_TICK_DIV >> 2):
                    m.d.sync += self.ramp_tick_div.eq(self.dat_w[:16])
                with m.Case(ADDR_RAMP_CENTER >> 2):
                    m.d.sync += self.ramp_center.eq(self.dat_w[:16].as_signed())
                with m.Case(ADDR_RAMP_WIDTH >> 2):
                    m.d.sync += self.ramp_width.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_WINDOW_MIN >> 2):
                    m.d.sync += self.autolock_window_min.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_WINDOW_MAX >> 2):
                    m.d.sync += self.autolock_window_max.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_EXPECTED_MIN_X >> 2):
                    m.d.sync += self.autolock_expected_min_x.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_EXPECTED_MAX_X >> 2):
                    m.d.sync += self.autolock_expected_max_x.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_LOCK_X >> 2):
                    m.d.sync += self.autolock_lock_x.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_AMP_MIN >> 2):
                    m.d.sync += self.autolock_amp_min.eq(self.dat_w[:24].as_signed())
                with m.Case(ADDR_AUTOLOCK_WIDTH_MIN >> 2):
                    m.d.sync += self.autolock_width_min.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_WIDTH_MAX >> 2):
                    m.d.sync += self.autolock_width_max.eq(self.dat_w[:16])
                with m.Case(ADDR_AUTOLOCK_SLOPE_SIGN >> 2):
                    m.d.sync += self.autolock_slope_sign.eq(self.dat_w[0])
                with m.Case(ADDR_AUTOLOCK_RETRY_LIMIT >> 2):
                    m.d.sync += self.autolock_retry_limit.eq(self.dat_w[:8])
                with m.Case(ADDR_PDH_CONTROL >> 2):
                    m.d.sync += pdh_control.eq(self.dat_w)
                with m.Case(ADDR_PDH_MOD_FREQ >> 2):
                    m.d.sync += self.pdh_mod_freq.eq(self.dat_w)
                with m.Case(ADDR_PDH_MOD_AMP >> 2):
                    m.d.sync += self.pdh_mod_amp.eq(self.dat_w[:16])
                with m.Case(ADDR_PDH_DEMOD_PHASE >> 2):
                    m.d.sync += self.pdh_demod_phase.eq(self.dat_w)
                with m.Case(ADDR_PDH_LPF_ALPHA >> 2):
                    m.d.sync += self.pdh_lpf_alpha.eq(self.dat_w[:5])
                # FAULT_CLEAR (write-only) handled combinationally above;
                # VERSION / STATUS / FAULT_STATUS ignore writes.

        # soft_reset self-clears one cycle after being set, so
        # software doesn't have to write it back to zero.
        soft_reset_r = Signal()
        m.d.sync += soft_reset_r.eq(control[CTRL_SOFT_RESET])
        with m.If(control[CTRL_SOFT_RESET] & soft_reset_r):
            m.d.sync += control.eq(control & ~(1 << CTRL_SOFT_RESET))

        # ---------------- read mux ----------------
        with m.Switch(word_adr):
            with m.Case(ADDR_VERSION >> 2):
                m.d.comb += self.dat_r.eq(VERSION_ID)
            with m.Case(ADDR_CONTROL >> 2):
                m.d.comb += self.dat_r.eq(control)
            with m.Case(ADDR_STATUS >> 2):
                m.d.comb += self.dat_r.eq(status_word)
            with m.Case(ADDR_MODE >> 2):
                m.d.comb += self.dat_r.eq(self.mode)
            with m.Case(ADDR_FAULT_STATUS >> 2):
                m.d.comb += self.dat_r.eq(fault_sticky)
            with m.Case(ADDR_FAULT_ENABLE >> 2):
                m.d.comb += self.dat_r.eq(fault_enable)
            with m.Case(ADDR_DEBUG_SELECT >> 2):
                m.d.comb += self.dat_r.eq(self.debug_select)
            with m.Case(ADDR_PDH_CONTROL >> 2):
                m.d.comb += self.dat_r.eq(pdh_control)
            with m.Case(ADDR_PDH_MOD_FREQ >> 2):
                m.d.comb += self.dat_r.eq(self.pdh_mod_freq)
            with m.Case(ADDR_PDH_MOD_AMP >> 2):
                m.d.comb += self.dat_r.eq(self.pdh_mod_amp)
            with m.Case(ADDR_PDH_DEMOD_PHASE >> 2):
                m.d.comb += self.dat_r.eq(self.pdh_demod_phase)
            with m.Case(ADDR_PDH_LPF_ALPHA >> 2):
                m.d.comb += self.dat_r.eq(self.pdh_lpf_alpha)
            with m.Default():
                m.d.comb += self.dat_r.eq(0)

        return m
