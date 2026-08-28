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
    ADDR_ERROR_CONFIG, ADDR_LOCK_ERROR_MAX, ERROR_CFG_INVERT,
    ADDR_PDH_CONTROL, ADDR_PDH_MOD_FREQ, ADDR_PDH_MOD_AMP,
    ADDR_PDH_DEMOD_PHASE, ADDR_PDH_LPF_ALPHA,
    PDH_CTRL_ENABLE,
    ADDR_ERROR_SETPOINT, ADDR_ERROR_OFFSET,
    ADDR_ADC_CONFIG, ADDR_ADC_GUARD_COUNT, ADC_CFG_FORMAT_MODE,
    ADC_CFG_FAULT_ENABLE,
    ADDR_FAST_INT_LEAK,
    ADDR_LOCK_CHECK_DELAY, ADDR_LOCK_MAX_ERROR, ADDR_LOCK_MAX_SAT_COUNT,
    ADDR_LOCK_ADC_TIMEOUT, ADDR_LOCK_ERROR_TIMEOUT, ADDR_LOCK_JUMP_LIMIT,
    ADDR_LOCK_JUMP_WINDOW, ADDR_LOCK_STATE_TIMEOUT, ADDR_LOCK_RELOCK_LIMIT,
    ADDR_DAC_CONFIG, DAC_CFG_FAST_OFFSET_BIN, DAC_CFG_SLOW_OFFSET_BIN,
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
        # NOTE: `reset=` is deprecated in Amaranth 0.5 and REMOVED in 0.6.
        # All of these now use `init=` so the repo survives the next
        # Amaranth release (see docs/Requirements.txt).
        self.fast_kp = Signal(signed(18), init=0)
        self.fast_ki = Signal(signed(18), init=0)
        # Conservative default fast output clamp: +/-3200 (about +/-0.1 V)
        # rather than full scale, so an unconfigured system cannot make a
        # full-scale step into a laser or piezo driver.
        self.fast_out_min = Signal(signed(16), init=-3200)
        self.fast_out_max = Signal(signed(16), init=3200)
        self.fast_out_safe = Signal(signed(16), init=0)
        # Leaky integrator (packet 11.4 FAST_INT_LEAK). 0 = no leak.
        self.fast_int_leak = Signal(5, init=0)

        # AUDIT FIX: the conservative scan default was applied to
        # ramp_min only. ramp_max was left at full positive scale
        # (+32767, about +1 V), so the "safe default" was asymmetric and
        # an unconfigured wide scan still slammed the slow DAC to +1 V on
        # the first enable -- exactly the failure the ramp_min change was
        # written to prevent. Symmetric now.
        self.ramp_min = Signal(signed(16), init=-3200)
        self.ramp_max = Signal(signed(16), init=3200)
        self.ramp_step = Signal(16, init=32)
        self.ramp_tick_div = Signal(16, init=32)
        self.ramp_center = Signal(signed(16), init=0)
        self.ramp_width = Signal(16, init=1024)

        # AUDIT FIX (S2-8): the autolock scan-position fields are SIGNED.
        # They are compared against ramp_scan.ramp_out, which is
        # signed(16); as unsigned they broke for every negative scan
        # position, and the default ramp_min is negative.
        self.autolock_window_min = Signal(signed(16), init=-32768)
        self.autolock_window_max = Signal(signed(16), init=32767)
        self.autolock_expected_min_x = Signal(signed(16), init=0)
        self.autolock_expected_max_x = Signal(signed(16), init=0)
        self.autolock_lock_x = Signal(signed(16), init=0)
        self.autolock_amp_min = Signal(signed(24), init=0)
        self.autolock_width_min = Signal(16, init=0)
        self.autolock_width_max = Signal(16, init=65535)
        self.autolock_slope_sign = Signal(init=0)
        self.autolock_retry_limit = Signal(8, init=3)

        # --- Error calculation (packet 11.3) ---
        self.error_invert = Signal(init=0)
        self.error_setpoint = Signal(signed(20), init=0)
        self.error_offset = Signal(signed(20), init=0)

        # --- ADC configuration ---
        # Format mode defaults to 1 (two's-complement passthrough), which
        # is what the Red Pitaya board wrapper needs after its own
        # sign/invert conversion.
        self.adc_format_mode = Signal(init=1)
        # Gates the ADC guard flags into fault_source (S3-5).
        self.adc_fault_enable = Signal(init=1)
        # Stuck-sample threshold. At 125 MSPS a quiet input holding one
        # code for a few hundred samples is normal, so the old effective
        # threshold of 16 produced routine false positives.
        self.adc_guard_count = Signal(16, init=4096)

        # --- Lock check / lock watch (packet 11.9) ---
        # AUDIT FIX (S2-5): these were hardcoded constants in
        # lock_core_top. Defaults chosen to be permissive enough that a
        # normal lock acquisition does not trip them:
        #   * lock_error_max was 25 counts against a 20-bit signed error
        #     path (0.005% of range) and could never be satisfied.
        #   * saturation timeout was 100 cycles = 800 ns, far shorter
        #     than a real acquisition transient, and it escalated
        #     straight to an unrecoverable fault.
        self.lock_error_max = Signal(24, init=4096)
        self.lock_check_delay = Signal(32, init=12500)      # 100 us at 125 MHz
        self.lock_max_sat_count = Signal(32, init=1250000)  # 10 ms
        self.lock_adc_timeout = Signal(32, init=1250)       # 10 us
        self.lock_error_timeout = Signal(32, init=125000)   # 1 ms
        self.lock_jump_limit = Signal(16, init=4096)
        self.lock_jump_window = Signal(5, init=8)           # 256 samples
        self.lock_state_timeout = Signal(32, init=1 << 28)  # ~2.1 s
        self.lock_relock_limit = Signal(8, init=8)

        # --- DAC configuration (packet 11.10) ---
        self.dac_fast_offset_bin = Signal(init=0)
        self.dac_slow_offset_bin = Signal(init=0)

        # --- PDH configuration hooks ---
        self.pdh_enable = Signal()
        self.pdh_mod_freq = Signal(32, init=0)
        self.pdh_mod_amp = Signal(16, init=0)
        self.pdh_demod_phase = Signal(32, init=0)
        self.pdh_lpf_alpha = Signal(5, init=8)

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
        error_config = Signal(32)
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
            self.error_invert.eq(error_config[ERROR_CFG_INVERT]),
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

        # fault_clear_request in CONTROL is a self-clearing pulse bit,
        # exposed for modules that want a "clear everything" trigger
        # distinct from the masked FAULT_CLEAR write above.
        fault_clear_req_r = Signal()
        m.d.sync += fault_clear_req_r.eq(control[CTRL_FAULT_CLEAR_REQUEST])
        m.d.comb += self.fault_clear_pulse.eq(
            control[CTRL_FAULT_CLEAR_REQUEST] & ~fault_clear_req_r
        )

        # AUDIT FIX (S3-3, part 1): fault_sticky had TWO sync drivers --
        # the masked update, and an unconditional `fault_sticky.eq(0)`
        # inside `with m.If(self.fault_clear_pulse)`. The later statement
        # won, so a CONTROL fault-clear pulse wiped every sticky bit
        # regardless of FAULT_ENABLE, contradicting both this module's
        # docstring and packet 10.2 ("do not silently auto-recover").
        # It also cleared a fault that was still asserting on that very
        # cycle, which then re-latched immediately: a glitch that could
        # momentarily drop the fault indication.
        #
        # Single driver now. A global clear wipes the latched history but
        # immediately re-latches anything still actively asserting, so a
        # live fault cannot be cleared away.
        with m.If(self.fault_clear_pulse):
            m.d.sync += fault_sticky.eq(self.fault_in & fault_enable)
        with m.Else():
            m.d.sync += fault_sticky.eq(
                (fault_sticky & ~(clear_mask & fault_enable))
                | (self.fault_in & fault_enable)
            )

        # ---------------- CONTROL: single driver ----------------
        #
        # AUDIT FIX (S3-3, part 2): `control` had THREE sync drivers --
        # the bus write, the fault-clear-bit self-clear, and the
        # soft-reset self-clear. Source order gave the soft-reset clear
        # highest priority, so a software write to CONTROL that landed on
        # the same cycle as a pending self-clear was SILENTLY DISCARDED
        # and replaced by `control & ~SOFT_RESET`. A lost control write is
        # exactly the kind of intermittent behaviour that is impossible
        # to reproduce from a bench.
        #
        # Restructured into one driver: take the written value if a write
        # is happening, otherwise hold, then mask off any self-clearing
        # bits that are due. The write is never lost.
        soft_reset_r = Signal()
        m.d.sync += soft_reset_r.eq(control[CTRL_SOFT_RESET])
        soft_reset_done = Signal()
        m.d.comb += soft_reset_done.eq(control[CTRL_SOFT_RESET] & soft_reset_r)

        ctrl_write = Signal()
        m.d.comb += ctrl_write.eq(
            self.stb & self.we & (word_adr == (ADDR_CONTROL >> 2)))

        control_base = Signal(32)
        m.d.comb += control_base.eq(Mux(ctrl_write, self.dat_w, control))

        control_clear = Signal(32)
        m.d.comb += control_clear.eq(
            Mux(self.fault_clear_pulse, 1 << CTRL_FAULT_CLEAR_REQUEST, 0)
            | Mux(soft_reset_done, 1 << CTRL_SOFT_RESET, 0)
        )

        m.d.sync += control.eq(control_base & ~control_clear)

        # ---------------- write decode ----------------
        with m.If(self.stb & self.we):
            with m.Switch(word_adr):
                # ADDR_CONTROL is handled by the single driver above.
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
                with m.Case(ADDR_ERROR_CONFIG >> 2):
                    m.d.sync += error_config.eq(self.dat_w)
                with m.Case(ADDR_LOCK_ERROR_MAX >> 2):
                    m.d.sync += self.lock_error_max.eq(self.dat_w[:24])
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

                # --- Error calculation (packet 11.3) ---
                with m.Case(ADDR_ERROR_SETPOINT >> 2):
                    m.d.sync += self.error_setpoint.eq(self.dat_w[:20].as_signed())
                with m.Case(ADDR_ERROR_OFFSET >> 2):
                    m.d.sync += self.error_offset.eq(self.dat_w[:20].as_signed())

                # --- ADC configuration ---
                with m.Case(ADDR_ADC_CONFIG >> 2):
                    m.d.sync += [
                        self.adc_format_mode.eq(
                            self.dat_w[ADC_CFG_FORMAT_MODE]),
                        self.adc_fault_enable.eq(
                            self.dat_w[ADC_CFG_FAULT_ENABLE]),
                    ]
                with m.Case(ADDR_ADC_GUARD_COUNT >> 2):
                    m.d.sync += self.adc_guard_count.eq(self.dat_w[:16])

                # --- Fast controller extras (packet 11.4) ---
                with m.Case(ADDR_FAST_INT_LEAK >> 2):
                    m.d.sync += self.fast_int_leak.eq(self.dat_w[:5])

                # --- Lock check / lock watch (packet 11.9) ---
                with m.Case(ADDR_LOCK_CHECK_DELAY >> 2):
                    m.d.sync += self.lock_check_delay.eq(self.dat_w)
                with m.Case(ADDR_LOCK_MAX_ERROR >> 2):
                    m.d.sync += self.lock_error_max.eq(self.dat_w[:24])
                with m.Case(ADDR_LOCK_MAX_SAT_COUNT >> 2):
                    m.d.sync += self.lock_max_sat_count.eq(self.dat_w)
                with m.Case(ADDR_LOCK_ADC_TIMEOUT >> 2):
                    m.d.sync += self.lock_adc_timeout.eq(self.dat_w)
                with m.Case(ADDR_LOCK_ERROR_TIMEOUT >> 2):
                    m.d.sync += self.lock_error_timeout.eq(self.dat_w)
                with m.Case(ADDR_LOCK_JUMP_LIMIT >> 2):
                    m.d.sync += self.lock_jump_limit.eq(self.dat_w[:16])
                with m.Case(ADDR_LOCK_JUMP_WINDOW >> 2):
                    m.d.sync += self.lock_jump_window.eq(self.dat_w[:5])
                with m.Case(ADDR_LOCK_STATE_TIMEOUT >> 2):
                    m.d.sync += self.lock_state_timeout.eq(self.dat_w)
                with m.Case(ADDR_LOCK_RELOCK_LIMIT >> 2):
                    m.d.sync += self.lock_relock_limit.eq(self.dat_w[:8])

                # --- DAC configuration (packet 11.10) ---
                with m.Case(ADDR_DAC_CONFIG >> 2):
                    m.d.sync += [
                        self.dac_fast_offset_bin.eq(
                            self.dat_w[DAC_CFG_FAST_OFFSET_BIN]),
                        self.dac_slow_offset_bin.eq(
                            self.dat_w[DAC_CFG_SLOW_OFFSET_BIN]),
                    ]
                # FAULT_CLEAR (write-only) handled combinationally above;
                # CONTROL has its own single driver above;
                # VERSION / STATUS / FAULT_STATUS ignore writes.

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
            with m.Case(ADDR_ERROR_CONFIG >> 2):
                m.d.comb += self.dat_r.eq(error_config)
            with m.Case(ADDR_LOCK_ERROR_MAX >> 2):
                m.d.comb += self.dat_r.eq(self.lock_error_max)
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

            # ===========================================================
            # AUDIT FIX (S2-1): 21 R/W registers had a WRITE decode and
            # no READ decode, so every one of them read back as 0.
            # Confirmed in simulation: FAST_KP written 8192 read back 0,
            # FAST_OUT_MAX written 5000 read back 0, RAMP_STEP written 64
            # read back 0, AUTOLOCK_LOCK_X written 1234 read back 0.
            #
            # register_defs.py marks all of them R/W and packet section 11
            # states plainly: "Every writable configuration value should
            # be readable." The GUI declares all of them writable and
            # would have shown 0 for each after any refresh.
            # ===========================================================
            with m.Case(ADDR_FAST_KP >> 2):
                m.d.comb += self.dat_r.eq(self.fast_kp)
            with m.Case(ADDR_FAST_KI >> 2):
                m.d.comb += self.dat_r.eq(self.fast_ki)
            with m.Case(ADDR_FAST_OUT_MIN >> 2):
                m.d.comb += self.dat_r.eq(self.fast_out_min)
            with m.Case(ADDR_FAST_OUT_MAX >> 2):
                m.d.comb += self.dat_r.eq(self.fast_out_max)
            with m.Case(ADDR_FAST_OUT_SAFE >> 2):
                m.d.comb += self.dat_r.eq(self.fast_out_safe)
            with m.Case(ADDR_FAST_INT_LEAK >> 2):
                m.d.comb += self.dat_r.eq(self.fast_int_leak)

            with m.Case(ADDR_RAMP_MIN >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_min)
            with m.Case(ADDR_RAMP_MAX >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_max)
            with m.Case(ADDR_RAMP_STEP >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_step)
            with m.Case(ADDR_RAMP_TICK_DIV >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_tick_div)
            with m.Case(ADDR_RAMP_CENTER >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_center)
            with m.Case(ADDR_RAMP_WIDTH >> 2):
                m.d.comb += self.dat_r.eq(self.ramp_width)

            with m.Case(ADDR_AUTOLOCK_WINDOW_MIN >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_window_min)
            with m.Case(ADDR_AUTOLOCK_WINDOW_MAX >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_window_max)
            with m.Case(ADDR_AUTOLOCK_EXPECTED_MIN_X >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_expected_min_x)
            with m.Case(ADDR_AUTOLOCK_EXPECTED_MAX_X >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_expected_max_x)
            with m.Case(ADDR_AUTOLOCK_LOCK_X >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_lock_x)
            with m.Case(ADDR_AUTOLOCK_AMP_MIN >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_amp_min)
            with m.Case(ADDR_AUTOLOCK_WIDTH_MIN >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_width_min)
            with m.Case(ADDR_AUTOLOCK_WIDTH_MAX >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_width_max)
            with m.Case(ADDR_AUTOLOCK_SLOPE_SIGN >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_slope_sign)
            with m.Case(ADDR_AUTOLOCK_RETRY_LIMIT >> 2):
                m.d.comb += self.dat_r.eq(self.autolock_retry_limit)

            # --- Error calculation ---
            with m.Case(ADDR_ERROR_SETPOINT >> 2):
                m.d.comb += self.dat_r.eq(self.error_setpoint)
            with m.Case(ADDR_ERROR_OFFSET >> 2):
                m.d.comb += self.dat_r.eq(self.error_offset)

            # --- ADC configuration ---
            with m.Case(ADDR_ADC_CONFIG >> 2):
                m.d.comb += self.dat_r.eq(
                    Cat(self.adc_format_mode, self.adc_fault_enable))
            with m.Case(ADDR_ADC_GUARD_COUNT >> 2):
                m.d.comb += self.dat_r.eq(self.adc_guard_count)

            # --- Lock check / lock watch ---
            with m.Case(ADDR_LOCK_CHECK_DELAY >> 2):
                m.d.comb += self.dat_r.eq(self.lock_check_delay)
            with m.Case(ADDR_LOCK_MAX_ERROR >> 2):
                m.d.comb += self.dat_r.eq(self.lock_error_max)
            with m.Case(ADDR_LOCK_MAX_SAT_COUNT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_max_sat_count)
            with m.Case(ADDR_LOCK_ADC_TIMEOUT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_adc_timeout)
            with m.Case(ADDR_LOCK_ERROR_TIMEOUT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_error_timeout)
            with m.Case(ADDR_LOCK_JUMP_LIMIT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_jump_limit)
            with m.Case(ADDR_LOCK_JUMP_WINDOW >> 2):
                m.d.comb += self.dat_r.eq(self.lock_jump_window)
            with m.Case(ADDR_LOCK_STATE_TIMEOUT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_state_timeout)
            with m.Case(ADDR_LOCK_RELOCK_LIMIT >> 2):
                m.d.comb += self.dat_r.eq(self.lock_relock_limit)

            # --- DAC configuration ---
            with m.Case(ADDR_DAC_CONFIG >> 2):
                m.d.comb += self.dat_r.eq(
                    Cat(self.dac_fast_offset_bin, self.dac_slow_offset_bin))

            with m.Default():
                m.d.comb += self.dat_r.eq(0)

        return m
