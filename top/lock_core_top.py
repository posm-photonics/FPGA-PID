from amaranth import *
from amaranth.lib.cdc import FFSynchronizer

from rtl.adc.adc_frontend_top import ADCFrontendTop
from rtl.autolock.robust_autolock import RobustAutoLock
from rtl.bus.register_bank import RegisterBank
from rtl.control.fault_gate import FaultGate
from rtl.control.lock_fsm import LockFSM, LockState
from rtl.control.output_limiter import OutputLimiter
from rtl.control.ramp_scan import RampScan
from rtl.control.slow_recenter import SlowRecenter
from rtl.control.trace_capture import TraceCapture
from rtl.dac.dac_fast_formatter import DACFastFormatter
from rtl.dac.dac_slow_formatter import DACSlowFormatter
from rtl.dsp.error_calc import ErrorCalc
from rtl.dsp.lock_watch import LockWatch
from rtl.dsp.pi_controller import PICore
from rtl.dsp.pdh_frontend import PDHFrontend


class LockCoreTop(Elaboratable):
    """Top-level integration for the FPGA laser-lock core.

    The fast path is kept intentionally simple: ADC formatting, guard
    checks, error calculation, PI control, output limiting, fault gating,
    and fast-DAC formatting. The slow path provides scan generation,
    long-term recentering, limiting and its own fault gating. The
    supervisory FSM and register bank coordinate the sequence without
    duplicating DSP logic in the top wrapper.

    ===================================================================
    AUDIT NOTE
    ===================================================================
    This file carried the majority of the defects found in the pre-ship
    audit, for one reason: it had never been simulated. The clock-domain
    construct below (S3-10) made sim/tb_lock_core_top.py raise
    DriverConflict, so the only full-system testbench in the repo could
    not run and every bug living at a module boundary went unexercised.
    The individual modules were well tested; the assembly was not tested
    at all.

    Fixes applied here are tagged S1-x / S2-x / S3-x inline.
    """

    def __init__(self):
        # External clock and reset for the single synchronous clock domain.
        self.clk = Signal()
        self.rst = Signal()

        # Physical ADC interface exposed directly to the board wrapper.
        self.i_adc_ch0 = Signal(16)
        self.i_adc_ch1 = Signal(16)
        self.i_adc_valid = Signal()
        self.i_adc_overrange_ch0 = Signal()
        self.i_adc_overrange_ch1 = Signal()
        self.i_format_mode = Signal()
        self.i_external_interlock = Signal() # external safety signal coming from hardware.
        self.i_feature_selected = Signal()

        # Final outputs exposed to the board wrapper.
        # Unsigned: these are DAC CODES, whose encoding is selected by
        # DAC_CONFIG. Declaring them signed (as before) invited the board
        # wrapper to do arithmetic on a value that is not a number.
        self.o_dac_fast = Signal(16)
        self.o_dac_slow = Signal(16)
        self.o_dac_mod  = Signal(signed(16))  # PDH modulation waveform

        # Shared memory-mapped register interface: Software -->(to) FPGA
        self.adr = Signal(12)       # register address bus

        self.dat_w = Signal(32)     # data being written into the register
                                    # from software to FPGA

        self.dat_r = Signal(32)     # data being read from the register
                                    # so from FPGA back to software

        self.we = Signal()          # write enable: tells the FPGA
                                    # "This bus transaction is a write"

        self.stb = Signal()         # stb : strobe or this transaction is valid

        # Supervisory status exposed to software and board glue.
        self.lock_state = Signal(4)
        self.lock_fault = Signal()
        self.fast_output = Signal(16)
        self.slow_output = Signal(16)
        self.trace_ready = Signal()
        # Heartbeat: a free-running counter bit, meant for an LED or a
        # spare pin. During bring-up this is the one-bit answer to "is
        # the core clocked and out of reset?", which from the bench
        # otherwise looks identical to "the register map is broken".
        # scripts/build_posm_red_pitaya.tcl asks the integrator to
        # hand-wire .rst(~adc_rstn); getting that inversion wrong holds
        # the whole core in reset with no other symptom.
        self.o_heartbeat = Signal()

    def elaborate(self, platform):
        m = Module()

        # ------------------------------------------------------------------
        # Clock domain
        #
        # AUDIT FIX (S3-10): this used to be
        #
        #     m.domains.sync = ClockDomain()
        #     m.d.comb += [ClockSignal("sync").eq(self.clk),
        #                  ResetSignal("sync").eq(self.rst)]
        #
        # Driving ClockSignal from combinational logic makes it
        # impossible to attach a simulation clock -- Amaranth raises
        # "DriverConflict: Clock signal is already driven by
        # combinational logic" -- which is why the full-system testbench
        # had never run. walkthrough.md recorded that failure and called
        # it a test-environment quirk. It was not. In synthesis the same
        # construct routes a clock through general fabric instead of a
        # global buffer.
        #
        # The domain's clock is now left for the parent (board wrapper)
        # or the testbench to drive, which is the normal Amaranth
        # arrangement. self.clk stays in the port list so the generated
        # Verilog keeps the same interface for red_pitaya_top.v.
        # ------------------------------------------------------------------
        m.domains.sync = ClockDomain()
        m.d.comb += ResetSignal("sync").eq(self.rst)

        heartbeat_ctr = Signal(24)
        m.d.sync += heartbeat_ctr.eq(heartbeat_ctr + 1)
        m.d.comb += self.o_heartbeat.eq(heartbeat_ctr[-1])

        # Submodules are instantiated explicitly so the hierarchy is visible.
        m.submodules.reg_bank = reg_bank = RegisterBank()
        m.submodules.adc_frontend = adc_frontend = ADCFrontendTop()
        # AUDIT FIX (S2-10): ErrorCalc used to be instantiated with its
        # defaults (adc_w=16) while pdh_frontend.error_sample was 17 bits.
        # The assignment truncated the MSB, so PDH error values outside
        # +/-32767 WRAPPED SIGN and inverted the feedback -- packet 4.4:
        # "If the controller polarity is wrong, the loop becomes positive
        # feedback and runs away." The error path is 20 bits end to end
        # now, matching err_w.
        m.submodules.error_calc = error_calc = ErrorCalc(adc_w=20, err_w=20)
        m.submodules.pi_ctrl = pi_ctrl = PICore()
        m.submodules.output_limiter = output_limiter = OutputLimiter()
        m.submodules.fault_gate = fault_gate = FaultGate()
        m.submodules.dac_fast_fmt = dac_fast_fmt = DACFastFormatter()
        # AUDIT FIX (S1-10): DACSlowFormatter existed in rtl/dac/ and was
        # never instantiated. The slow DAC output went straight from the
        # ramp/recenter mux to the top-level port with no limiter, no
        # formatter and no fault gate, so on a fault the fast DAC went
        # safe while the slow DAC -- the one driving the piezo -- kept
        # driving whatever it happened to have.
        m.submodules.dac_slow_fmt = dac_slow_fmt = DACSlowFormatter(
            controller_width=24, dac_width=16)
        m.submodules.slow_limiter = slow_limiter = OutputLimiter(width=24)
        m.submodules.ramp_scan = ramp_scan = RampScan()
        m.submodules.slow_recenter = slow_recenter = SlowRecenter()
        m.submodules.trace_capture = trace_capture = TraceCapture()
        m.submodules.autolock = autolock = RobustAutoLock()
        m.submodules.lock_watch = lock_watch = LockWatch()
        m.submodules.lock_fsm = lock_fsm = LockFSM()
        m.submodules.pdh_frontend = pdh_frontend = PDHFrontend()

        # ------------------------------------------------------------------
        # Asynchronous input synchronisers
        #
        # AUDIT FIX (S3-1): i_external_interlock and i_feature_selected
        # are asynchronous board inputs. The interlock previously fanned
        # out COMBINATIONALLY, through fault_source, into three separate
        # registers at once: the FSM state register, the fault gate, and
        # slow_recenter.fault_force. An asynchronous signal driving
        # several flip-flops through combinational logic can be captured
        # differently by each of them on the same edge, so the system
        # could land in a half-faulted state where the FSM says FAULT but
        # the DAC did not go safe, or the reverse. There was not a single
        # synchroniser anywhere in the repository.
        #
        # This is the classic source of intermittent, non-reproducible
        # hardware behaviour, and it was sitting on the safety interlock.
        # ------------------------------------------------------------------
        external_interlock = Signal()
        feature_selected = Signal()
        m.submodules.sync_interlock = FFSynchronizer(
            self.i_external_interlock, external_interlock)
        m.submodules.sync_feature = FFSynchronizer(
            self.i_feature_selected, feature_selected)

        # ------------------------------------------------------------------
        # Register bus connections
        #
        # Each module that owns registers is connected to this bus so
        # software can configure it or read its status. Each module
        # responds only to its own address range and drives 0 otherwise,
        # so the read data can be OR-combined.
        # ------------------------------------------------------------------
        read_data = Signal(32)
        fault_vector = Signal(12)
        fault_source = Signal()

        m.d.comb += [
            reg_bank.adr.eq(self.adr),
            reg_bank.dat_w.eq(self.dat_w),
            reg_bank.we.eq(self.we),
            reg_bank.stb.eq(self.stb),
            reg_bank.state.eq(lock_fsm.state),
            # AUDIT FIX (S3-6): LOCKED is a SINGLE-CYCLE state -- the FSM
            # advances unconditionally to LOCK_WATCH -- so this status bit
            # was high for exactly one clock in the lifetime of a lock and
            # the GUI's lock indicator never lit. Report locked across
            # both steady lock states.
            reg_bank.locked.eq((lock_fsm.state == LockState.LOCKED)
                               | (lock_fsm.state == LockState.LOCK_WATCH)),
            reg_bank.scanning.eq((lock_fsm.state == LockState.WIDE_SCAN)
                                 | (lock_fsm.state == LockState.ZOOM_SCAN)),
            reg_bank.saturation.eq(output_limiter.o_sat | dac_fast_fmt.o_sat),
            reg_bank.trace_ready.eq(trace_capture.trace_ready),
            reg_bank.fault_active.eq(lock_fsm.fault_state),
            reg_bank.fault_in.eq(fault_vector),
            slow_recenter.adr.eq(self.adr),
            slow_recenter.dat_w.eq(self.dat_w),
            slow_recenter.we.eq(self.we),
            slow_recenter.stb.eq(self.stb),
            trace_capture.adr.eq(self.adr),
            trace_capture.dat_w.eq(self.dat_w),
            trace_capture.we.eq(self.we),
            trace_capture.stb.eq(self.stb),

            read_data.eq(reg_bank.dat_r | slow_recenter.dat_r | trace_capture.dat_r),
            self.dat_r.eq(read_data),
        ]

        # Connect the physical ADC interface to the ADC front-end.
        m.d.comb += [
            adc_frontend.i_ch0.eq(self.i_adc_ch0),
            adc_frontend.i_ch1.eq(self.i_adc_ch1),
            adc_frontend.i_valid.eq(self.i_adc_valid),
            adc_frontend.i_overrange_ch0.eq(self.i_adc_overrange_ch0),
            adc_frontend.i_overrange_ch1.eq(self.i_adc_overrange_ch1),
            # Format mode is a register now (packet 11.2) rather than only
            # a top-level pin. The pin still wins if the board wrapper
            # asserts it, so existing board wiring keeps working.
            adc_frontend.i_format_mode.eq(self.i_format_mode
                                          | reg_bank.adc_format_mode),
            adc_frontend.i_guard_threshold.eq(reg_bank.adc_guard_count),
        ]

        # PDH Frontend
        m.d.comb += [
            pdh_frontend.adc_sample.eq(adc_frontend.o_ch0),
            pdh_frontend.adc_valid.eq(adc_frontend.o_valid),
            pdh_frontend.freq_word.eq(reg_bank.pdh_mod_freq),
            pdh_frontend.mod_amp.eq(reg_bank.pdh_mod_amp),
            pdh_frontend.demod_phase.eq(reg_bank.pdh_demod_phase),
            pdh_frontend.lpf_alpha.eq(reg_bank.pdh_lpf_alpha),
            pdh_frontend.pdh_enable.eq(reg_bank.pdh_enable),
            # Clear the filter whenever the servo is not running, so a
            # stale accumulator cannot kick the loop at lock engagement.
            pdh_frontend.reset_filter.eq(self.rst | ~lock_fsm.feedback_enable),
            pdh_frontend.phase_reset.eq(self.rst),
        ]

        # ------------------------------------------------------------------
        # Fast feedback path
        # ------------------------------------------------------------------
        pi_load_value = Signal(signed(40))
        lock_quality_ok = Signal()
        lock_check_pass = Signal()
        error_magnitude = Signal(24)

        m.d.comb += [
            error_calc.sample_in.eq(pdh_frontend.error_sample),
            error_calc.sample_valid.eq(pdh_frontend.error_valid),
            # AUDIT FIX (S3-4): offset and setpoint were HARDWIRED TO 0
            # with no register behind either. Packet 4.3 Eq. 13 defines
            # the error as p*(x - ERROR_OFFSET - ERROR_SETPOINT), and
            # section 4.3 is explicit that the DC/background offset is
            # real. With the offset pinned at zero the zero crossing is
            # not at zero error, so the servo holds the wrong point.
            error_calc.offset.eq(reg_bank.error_offset),
            error_calc.setpoint.eq(reg_bank.error_setpoint),
            error_calc.invert_error.eq(reg_bank.error_invert),

            # AUDIT FIX (S1-7): this used to be
            #     Cat(autolock.slow_lock_position, Const(0, 24)).as_signed()
            # Amaranth's Cat puts its FIRST argument in the LOW bits, so
            # that produced a zero-extended value whose top bit is always
            # 0: every negative lock position loaded as a large positive
            # number (-1000 became +64536). slow_lock_position is signed
            # now and a plain widening assignment sign-extends correctly.
            #
            # Note this is still a SLOW-path scan code preloaded into the
            # FAST integrator. Per packet 3.4 those drive different
            # actuators (CTL200 DC input vs AC input), so the units do not
            # really match. The slow handoff below is what actually parks
            # the slow actuator; this preload is retained for behavioural
            # continuity and flagged in the audit as worth revisiting.
            pi_load_value.eq(autolock.slow_lock_position),

            error_magnitude.eq(Mux(error_calc.error_out[-1],
                                   -error_calc.error_out,
                                   error_calc.error_out)),
            lock_quality_ok.eq(
                adc_frontend.o_valid
                & (error_magnitude <= reg_bank.lock_error_max)
                & ~output_limiter.o_sat
                & ~fault_source
            ),
        ]

        # ------------------------------------------------------------------
        # Lock check persistence
        #
        # AUDIT FIX (S2-6): lock_check_pass and lock_check_failed used to
        # be lock_quality_ok and its exact complement, evaluated
        # combinationally EVERY clock. In LOCK_WATCH a single noisy sample
        # above threshold dropped the FSM straight into RELOCK_SCAN, which
        # at 125 MHz guarantees false unlocks. Packet 9.2 step 8 requires
        # the opposite ("FPGA waits a configured delay and checks
        # error/output metrics") and LOCK_CHECK_DELAY (0x224) exists in
        # the canonical map for precisely this. Linien gates its
        # equivalent decision on `waited_long_enough` for the same reason.
        #
        # The quality condition must now hold continuously for
        # lock_check_delay samples before the lock is declared good.
        # ------------------------------------------------------------------
        lock_ok_counter = Signal(32)
        with m.If(~lock_fsm.feedback_enable | ~lock_quality_ok):
            m.d.sync += lock_ok_counter.eq(0)
        with m.Elif(lock_ok_counter < reg_bank.lock_check_delay):
            m.d.sync += lock_ok_counter.eq(lock_ok_counter + 1)

        m.d.comb += lock_check_pass.eq(
            lock_quality_ok & (lock_ok_counter >= reg_bank.lock_check_delay))

        # ------------------------------------------------------------------
        # ARM_LOCK entry pulse
        #
        # AUDIT FIX (S1-6): integrator_load used to be driven by the LEVEL
        # (state == ARM_LOCK). integrator_load has priority over
        # integration inside PICore, so while the FSM sat in ARM_LOCK the
        # integrator was reloaded on every single clock and could never
        # accumulate: the controller was proportional-only in exactly the
        # state whose exit condition requires the error to converge. If P
        # alone could not null the error the FSM stayed in ARM_LOCK
        # forever -- and, before S1-5, with no timeout to escape.
        # ------------------------------------------------------------------
        arm_lock_prev = Signal()
        arm_lock_now = Signal()
        arm_lock_pulse = Signal()
        m.d.comb += arm_lock_now.eq(lock_fsm.state == LockState.ARM_LOCK)
        m.d.sync += arm_lock_prev.eq(arm_lock_now)
        m.d.comb += arm_lock_pulse.eq(arm_lock_now & ~arm_lock_prev)

        m.d.comb += [
            pi_ctrl.error_in.eq(error_calc.error_out),
            pi_ctrl.error_valid.eq(error_calc.error_valid),
            pi_ctrl.kp.eq(reg_bank.fast_kp),
            pi_ctrl.ki.eq(reg_bank.fast_ki),
            pi_ctrl.int_leak_shift.eq(reg_bank.fast_int_leak),
            pi_ctrl.lock_enable.eq(lock_fsm.feedback_enable),
            pi_ctrl.hold_enable.eq(reg_bank.hold_request),
            pi_ctrl.integrator_reset.eq(reg_bank.integrator_reset),
            pi_ctrl.integrator_load.eq(reg_bank.integrator_load | arm_lock_pulse),
            pi_ctrl.load_value.eq(pi_load_value),
            pi_ctrl.out_min.eq(reg_bank.fast_out_min),
            pi_ctrl.out_max.eq(reg_bank.fast_out_max),
            pi_ctrl.out_safe.eq(reg_bank.fast_out_safe),
        ]

        # The output limiter clamps the actuator-facing signal before it
        # reaches the final safety gate. It reads the same programmable
        # limits as the PI clamp, which makes it a real independent stage.
        m.d.comb += [
            output_limiter.i_u.eq(pi_ctrl.control_out),
            output_limiter.i_valid.eq(pi_ctrl.control_valid),
            output_limiter.i_min.eq(reg_bank.fast_out_min),
            output_limiter.i_max.eq(reg_bank.fast_out_max),
        ]

        # FaultGate is the final safety stage and overrides the fast command.
        m.d.comb += [
            fault_gate.i_u.eq(output_limiter.o_u),
            fault_gate.i_valid.eq(output_limiter.o_valid),
            # AUDIT FIX (S3-4): outputs_enable (CONTROL bit 2, "Allows DAC
            # outputs to leave safe code") was decoded by the register
            # bank and connected to absolutely nothing. It is a master
            # output enable and it did not work.
            fault_gate.i_fault.eq(fault_source | ~reg_bank.outputs_enable),
            # AUDIT FIX (S2-3): the safe code was a compile-time constant.
            # Packet 10.2 requires a fault to force DAC_FAST to
            # FAST_OUT_SAFE, which is a register.
            fault_gate.i_safe_code.eq(reg_bank.fast_out_safe),
        ]

        m.d.comb += [
            dac_fast_fmt.i_u.eq(fault_gate.o_u),
            dac_fast_fmt.i_valid.eq(fault_gate.o_valid),
            # AUDIT FIX (S3-4): was hardcoded to 0, with a comment
            # admitting the mode selection was unimplemented.
            dac_fast_fmt.i_mode.eq(reg_bank.dac_fast_offset_bin),
        ]

        # ------------------------------------------------------------------
        # Slow path
        #
        # AUDIT FIX (S3-8), scan-to-lock handoff:
        # ARM_LOCK was not in scan_path_active, so at the
        # FEATURE_VERIFY -> ARM_LOCK transition the mux switched instantly
        # from the ramp (parked on the verified feature) to
        # slow_recenter.slow_out, which is 0 because nothing had loaded
        # its accumulator. The slow DAC stepped discontinuously from the
        # feature position to mid-scale at the exact moment lock was
        # attempted: the laser jumped off the feature. Packet 9.3 is
        # explicit -- "The scan-to-lock transition must not kick the
        # laser" -- and the acceptance checklist lists it separately.
        #
        # The verified lock position is now latched at ARM_LOCK entry and
        # the post-scan command is that base PLUS the recenter
        # correction, matching packet Eq. 25 where the recenter term is a
        # correction to the operating point rather than the whole command.
        # ------------------------------------------------------------------
        slow_lock_base = Signal(signed(16))
        with m.If(arm_lock_pulse):
            m.d.sync += slow_lock_base.eq(autolock.slow_lock_position)

        slow_command = Signal(signed(24))
        scan_path_active = Signal()
        m.d.comb += [
            scan_path_active.eq(
                (lock_fsm.state == LockState.WIDE_SCAN)
                | (lock_fsm.state == LockState.TRACE_READY)
                | (lock_fsm.state == LockState.ZOOM_SCAN)
                | (lock_fsm.state == LockState.FEATURE_VERIFY)
                | (lock_fsm.state == LockState.RELOCK_SCAN)
            ),
            slow_command.eq(Mux(scan_path_active,
                                ramp_scan.ramp_out,
                                slow_lock_base + slow_recenter.slow_out)),

            ramp_scan.enable.eq(lock_fsm.wide_scan_enable
                                | lock_fsm.zoom_scan_enable),
            ramp_scan.zoom_mode.eq(lock_fsm.zoom_scan_enable),
            ramp_scan.ramp_min.eq(reg_bank.ramp_min),
            ramp_scan.ramp_max.eq(reg_bank.ramp_max),
            ramp_scan.ramp_step.eq(reg_bank.ramp_step),
            ramp_scan.ramp_tick_div.eq(reg_bank.ramp_tick_div),
            ramp_scan.ramp_center.eq(reg_bank.ramp_center),
            ramp_scan.ramp_width.eq(reg_bank.ramp_width),
        ]

        # Slow-path limiter -> formatter, mirroring the fast path so the
        # slow actuator gets the same class of protection.
        m.d.comb += [
            slow_limiter.i_u.eq(slow_command),
            slow_limiter.i_valid.eq(adc_frontend.o_valid),
            slow_limiter.i_min.eq(slow_recenter.o_out_min),
            slow_limiter.i_max.eq(slow_recenter.o_out_max),

            dac_slow_fmt.i_command.eq(slow_limiter.o_u),
            dac_slow_fmt.i_output_enable.eq(reg_bank.outputs_enable),
            dac_slow_fmt.i_hold.eq(reg_bank.hold_request),
            dac_slow_fmt.i_fault_active.eq(fault_source),
            dac_slow_fmt.i_safe_code.eq(slow_recenter.o_out_safe),
            dac_slow_fmt.i_min_code.eq(slow_recenter.o_out_min),
            dac_slow_fmt.i_max_code.eq(slow_recenter.o_out_max),
            dac_slow_fmt.i_offset_binary.eq(reg_bank.dac_slow_offset_bin),
        ]

        # ------------------------------------------------------------------
        # Autolock
        #
        # AUDIT FIX (S2-7): the module had no enable port and therefore
        # ran during WIDE_SCAN too, burning its retry budget against data
        # that could never match the descriptor, so it arrived at
        # FEATURE_VERIFY already exhausted and went straight to FAIL --
        # which then fed the (previously unbounded) relock loop.
        #
        # Second half of the same fix: scan_valid was wired to
        # ramp_scan.ramp_valid, which is `enable` registered and therefore
        # high on EVERY clock rather than once per ramp step, so the
        # zero-crossing detector saw full-rate fast-loop noise instead of
        # the scan trace. It is driven from the ramp tick now.
        # ------------------------------------------------------------------
        m.d.comb += [
            autolock.enable.eq(lock_fsm.autolock_enable
                               & reg_bank.autolock_enable),
            autolock.scan_valid.eq(ramp_scan.o_tick),
            autolock.scan_done.eq(ramp_scan.cycle_done),
            autolock.scan_code.eq(ramp_scan.ramp_out),
            autolock.error_sample.eq(error_calc.error_out),
            autolock.window_min.eq(reg_bank.autolock_window_min),
            autolock.window_max.eq(reg_bank.autolock_window_max),
            autolock.expected_min_x.eq(reg_bank.autolock_expected_min_x),
            autolock.expected_max_x.eq(reg_bank.autolock_expected_max_x),
            autolock.lock_x.eq(reg_bank.autolock_lock_x),
            autolock.amp_min.eq(reg_bank.autolock_amp_min),
            autolock.width_min.eq(reg_bank.autolock_width_min),
            autolock.width_max.eq(reg_bank.autolock_width_max),
            autolock.slope_sign.eq(reg_bank.autolock_slope_sign),
            autolock.retry_limit.eq(reg_bank.autolock_retry_limit),
            autolock.rst.eq(self.rst),

            slow_recenter.dac_fast_in.eq(output_limiter.o_u),
            slow_recenter.sample_valid.eq(adc_frontend.o_valid),
            slow_recenter.fault_force.eq(fault_source),
        ]

        # ------------------------------------------------------------------
        # Autolock result latching
        #
        # AUDIT FIX (S2-7): feature_match and feature_failed are
        # single-cycle pulses and the FSM samples them combinationally, so
        # a pulse arriving on any cycle the FSM was not already in
        # FEATURE_VERIFY was lost forever and the FSM hung (it had no
        # timeout either). Latch them, and clear the latch whenever the
        # autolock is not enabled so each verification starts clean.
        # ------------------------------------------------------------------
        autolock_success_l = Signal()
        autolock_failed_l = Signal()
        with m.If(~lock_fsm.autolock_enable):
            m.d.sync += [autolock_success_l.eq(0), autolock_failed_l.eq(0)]
        with m.Else():
            with m.If(autolock.feature_match):
                m.d.sync += autolock_success_l.eq(1)
            with m.If(autolock.feature_failed):
                m.d.sync += autolock_failed_l.eq(1)

        # Trace capture records the scan position and the current error
        # sample without entering the fast path.
        m.d.comb += [
            trace_capture.scan_code.eq(ramp_scan.ramp_out),
            trace_capture.error_sample.eq(error_calc.error_out),
            # AUDIT FIX (S1-4): this was ramp_scan.cycle_done, which
            # pulses once per COMPLETE sweep, so the buffer filled at one
            # point per full scan. Measured on the old integration: 11
            # samples in 3.2 ms, trace_ready never asserted, FSM stuck in
            # WIDE_SCAN for the whole run.
            #
            # The CONTROL trace-capture enable bit is also wired now; it
            # was decoded by the register bank and connected to nothing,
            # so the design had two trace enables and the documented one
            # was dead.
            trace_capture.sample_valid.eq(ramp_scan.o_tick
                                          & reg_bank.trace_capture_enable),
            trace_capture.ch1_sample.eq(adc_frontend.o_ch1),
        ]

        # ------------------------------------------------------------------
        # Lock FSM
        # ------------------------------------------------------------------
        m.d.comb += [
            lock_fsm.global_enable.eq(reg_bank.global_enable),
            lock_fsm.lock_enable_request.eq(reg_bank.lock_enable_request),
            lock_fsm.hold_request.eq(reg_bank.hold_request),
            lock_fsm.fault_active.eq(fault_source),
            lock_fsm.fault_clear_request.eq(reg_bank.fault_clear_pulse),
            lock_fsm.trace_ready.eq(trace_capture.trace_ready),
            lock_fsm.feature_selected.eq(feature_selected),
            lock_fsm.zoom_complete.eq(ramp_scan.cycle_done),
            lock_fsm.autolock_success.eq(autolock_success_l),
            lock_fsm.autolock_failed.eq(autolock_failed_l),
            lock_fsm.lock_check_pass.eq(lock_check_pass),
            # AUDIT FIX (S2-6): the failure path is the watchdog's
            # filtered opinion, not an instantaneous complement of the
            # pass condition.
            lock_fsm.lock_check_failed.eq(lock_watch.unlock_detected),
            lock_fsm.relock_request.eq(lock_watch.relock_request),
            lock_fsm.state_timeout.eq(reg_bank.lock_state_timeout),
            lock_fsm.relock_limit.eq(reg_bank.lock_relock_limit),
        ]

        # ------------------------------------------------------------------
        # LockWatch
        #
        # AUDIT FIX (S2-5): every threshold here used to be a hardcoded
        # constant (max_error 1000, saturation_timeout 100 = 800 ns,
        # adc_timeout 50, jump_limit 1000, error_timeout 200), none of
        # which appeared anywhere in the register map, so retuning the
        # safety watchdog needed a resynthesis. The 800 ns saturation
        # timeout in particular escalated straight to a fault on ordinary
        # lock-acquisition transients, and the fault was unrecoverable
        # (S1-3). All five are registers now.
        #
        # AUDIT FIX (S3-6): lock_active was (state == LOCKED), a
        # single-cycle state, so lock_healthy read 0 for the entire time
        # the system was genuinely locked and output_jump was never
        # evaluated at all.
        # ------------------------------------------------------------------
        m.d.comb += [
            lock_watch.enable.eq(lock_fsm.lock_watch_enable),
            lock_watch.lock_active.eq(
                (lock_fsm.state == LockState.LOCKED)
                | (lock_fsm.state == LockState.LOCK_WATCH)),
            lock_watch.error_value.eq(error_calc.error_out),
            lock_watch.max_error.eq(reg_bank.lock_error_max),
            # Signed comparisons against the real actuator limits, not
            # unsigned 0..65535 constants.
            lock_watch.fast_output.eq(fault_gate.o_u),
            lock_watch.fast_min.eq(reg_bank.fast_out_min),
            lock_watch.fast_max.eq(reg_bank.fast_out_max),
            lock_watch.slow_output.eq(slow_limiter.o_u),
            lock_watch.slow_min.eq(slow_recenter.o_out_min),
            lock_watch.slow_max.eq(slow_recenter.o_out_max),
            lock_watch.fast_saturated.eq(output_limiter.o_sat),
            lock_watch.slow_saturated.eq(slow_recenter.slow_saturated),
            lock_watch.adc_valid.eq(adc_frontend.o_valid),
            lock_watch.saturation_timeout.eq(reg_bank.lock_max_sat_count),
            lock_watch.adc_timeout.eq(reg_bank.lock_adc_timeout),
            lock_watch.jump_limit.eq(reg_bank.lock_jump_limit),
            lock_watch.jump_window_shift.eq(reg_bank.lock_jump_window),
            lock_watch.error_timeout.eq(reg_bank.lock_error_timeout),
        ]

        # ------------------------------------------------------------------
        # Fault vector and fault source
        #
        # AUDIT FIX (S1-3) -- the single most serious integration defect.
        # fault_source used to include lock_fsm.fault_state, which is
        # (state == FAULT). That fed lock_fsm.fault_active, which
        # unconditionally forces state = FAULT. The result was a
        # combinational latch closed through two modules: once the FSM
        # entered FAULT, fault_active was permanently 1, the
        # `m.Elif(state == FAULT)` branch that handles fault_clear_request
        # became unreachable, and FAULT could never be left. Confirmed in
        # simulation: after releasing the interlock, five clear attempts
        # through both documented mechanisms all failed and only a
        # hardware reset recovered. Packet 10.2 requires explicit clear;
        # explicit clear was implemented and unreachable.
        #
        # fault_source is now the OR of the CAUSES of a fault. The FSM
        # latches the state itself, which is what it is for.
        #
        # AUDIT FIX (S3-5): the ADC guard flags reached fault_vector (the
        # sticky status word) but NOT fault_source, so ADC overrange, a
        # stuck ADC and missing valid did not force a safe output at all,
        # even though packet 10.1 lists all three as fault sources. They
        # are included now, gated by a dedicated enable so an operator can
        # still disable an unwanted source without a rebuild.
        # ------------------------------------------------------------------
        # Only the UNAMBIGUOUS ADC faults force a safe output: overrange
        # (bits 0/1) and a missing valid stream (bit 4).
        #
        # The stuck-sample flags (bits 2/3) stay in FAULT_STATUS for
        # diagnosis but deliberately do NOT force a fault. Stuck
        # detection is a heuristic, and a static ADC reading is only
        # suspicious if the input is supposed to be moving. In this
        # design it frequently is not: while the FSM waits between scans
        # the ramp parks and the error signal is legitimately constant
        # for as long as the wait lasts. Routing the heuristic into
        # fault_source made the system fault during normal acquisition
        # -- observed in the integration test, ~3900 cycles into
        # FEATURE_VERIFY with a perfectly healthy ADC.
        adc_fault_any = Signal()
        m.d.comb += adc_fault_any.eq(
            adc_frontend.o_fault_flags[0]
            | adc_frontend.o_fault_flags[1]
            | adc_frontend.o_fault_flags[4]
        )

        m.d.comb += [
            fault_vector[0].eq(adc_frontend.o_fault_flags[0]),
            fault_vector[1].eq(adc_frontend.o_fault_flags[1]),
            fault_vector[2].eq(adc_frontend.o_fault_flags[2]),
            fault_vector[3].eq(adc_frontend.o_fault_flags[3]),
            fault_vector[4].eq(adc_frontend.o_fault_flags[4]),
            fault_vector[5].eq(lock_watch.fault_request),
            # AUDIT FIX: a relock request is an expected, recoverable
            # event, not a fault. It used to latch a sticky fault bit.
            fault_vector[6].eq(0),
            fault_vector[7].eq(external_interlock),
            fault_vector[8].eq(lock_fsm.timeout_fault),
            fault_vector[9].eq(lock_fsm.relock_exhausted),
            fault_vector[10].eq(0),
            fault_vector[11].eq(0),

            fault_source.eq(
                lock_watch.fault_request
                | external_interlock
                | (adc_fault_any & reg_bank.adc_fault_enable)
            ),
        ]

        # ------------------------------------------------------------------
        # Top-level outputs
        # ------------------------------------------------------------------
        m.d.comb += [
            self.o_dac_fast.eq(dac_fast_fmt.o_dac),
            self.o_dac_slow.eq(dac_slow_fmt.o_dac),
            self.o_dac_mod.eq(pdh_frontend.mod_out),
            self.lock_state.eq(lock_fsm.state),
            self.lock_fault.eq(lock_fsm.fault_state),
            self.fast_output.eq(dac_fast_fmt.o_dac),
            self.slow_output.eq(dac_slow_fmt.o_dac),
            self.trace_ready.eq(trace_capture.trace_ready),
        ]

        return m
