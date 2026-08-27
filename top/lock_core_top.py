from amaranth import *

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
from rtl.dsp.error_calc import ErrorCalc
from rtl.dsp.lock_watch import LockWatch
from rtl.dsp.pi_controller import PICore
from rtl.dsp.pdh_frontend import PDHFrontend


class LockCoreTop(Elaboratable):
    """Top-level integration for the FPGA laser-lock core.

    The fast path is kept intentionally simple: ADC formatting, guard checks,
    error calculation, PI control, output limiting, fault gating, and fast-DAC
    formatting. The slow path provides scan generation and long-term recentering.
    The supervisory FSM and register bank coordinate the sequence without duplicating
    DSP logic in the top wrapper.
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
        self.o_dac_fast = Signal(signed(16))
        self.o_dac_slow = Signal(signed(16))
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
        self.fast_output = Signal(signed(16))
        self.slow_output = Signal(signed(16))
        self.trace_ready = Signal()

    def elaborate(self, platform):
        m = Module()

        # The lock core uses one synchronous clock domain with explicit reset.
        # In Amaranth, the domain is created on its own and then externally
        # driven by the simulator or the board wrapper. Connecting the domain
        # clock to `self.clk` combinationally creates a driver conflict; the
        # board wrapper handles the actual external clock connection at the
        # module boundary instead.
        m.domains.sync = ClockDomain()
        m.d.comb += [
            ClockSignal("sync").eq(self.clk),
            ResetSignal("sync").eq(self.rst),
        ]

        # Submodules are instantiated explicitly so the hierarchy is visible.
        # ADCFrontendTop owns the ADC formatting, validity checks, and fault
        # generation for the fast path; LockCoreTop only connects the blocks.
        m.submodules.reg_bank = reg_bank = RegisterBank()
        m.submodules.adc_frontend = adc_frontend = ADCFrontendTop()
        m.submodules.error_calc = error_calc = ErrorCalc()
        m.submodules.pi_ctrl = pi_ctrl = PICore()
        m.submodules.output_limiter = output_limiter = OutputLimiter()
        m.submodules.fault_gate = fault_gate = FaultGate()
        m.submodules.dac_fast_fmt = dac_fast_fmt = DACFastFormatter()
        m.submodules.ramp_scan = ramp_scan = RampScan()
        m.submodules.slow_recenter = slow_recenter = SlowRecenter()
        m.submodules.trace_capture = trace_capture = TraceCapture()
        m.submodules.autolock = autolock = RobustAutoLock()
        m.submodules.lock_watch = lock_watch = LockWatch()
        m.submodules.lock_fsm = lock_fsm = LockFSM()
        m.submodules.pdh_frontend = pdh_frontend = PDHFrontend()

        # ------------------------------------------------------------------
        # Register bus connections
        #
        # The FPGA uses one shared register bus. Each module that owns
        # registers is connected to this bus so software can configure it
        # or read its status. Each module responds only to its own address
        # range.
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
            reg_bank.locked.eq(lock_fsm.state == LockState.LOCKED),
            reg_bank.scanning.eq((lock_fsm.state == LockState.WIDE_SCAN) | (lock_fsm.state == LockState.ZOOM_SCAN)),
            reg_bank.saturation.eq(output_limiter.o_sat),
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

            # Read data from all modules is combined into a single bus because
            # only one module should respond for any valid address.
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
            adc_frontend.i_format_mode.eq(self.i_format_mode),
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
            pdh_frontend.reset_filter.eq(self.rst),
        ]

        # Fast feedback path:
        # Wiring the PDH error output to the error calculator.
        pi_load_value = Signal(signed(40))
        lock_quality_ok = Signal()
        error_magnitude = Signal(24)
        m.d.comb += [
            error_calc.sample_in.eq(pdh_frontend.error_sample),
            error_calc.sample_valid.eq(pdh_frontend.error_valid),
            error_calc.offset.eq(0),
            error_calc.setpoint.eq(0),
            error_calc.invert_error.eq(reg_bank.error_invert),
            pi_load_value.eq(Cat(autolock.slow_lock_position, Const(0, 24)).as_signed()),
            error_magnitude.eq(Mux(error_calc.error_out[-1], -error_calc.error_out, error_calc.error_out)),
            lock_quality_ok.eq(
                adc_frontend.o_valid
                & (error_magnitude <= reg_bank.lock_error_max)
                & ~output_limiter.o_sat
                & ~fault_source
            ),
        ]

        # Implemented in this integration:
        # - PI controller block exists
        # - PI control wiring exists
        # - DAC fast formatter exists
        #
        # Not implemented yet:
        # - Kp/Ki software configuration path
        # - PI handoff loading source
        # - programmable safe output
        # - DAC fast formatter mode selection
        # - DAC slow formatter integration
        #
        # The PI controller is enabled only after the lock FSM confirms that
        # the system is ready for feedback control.
        m.d.comb += [
            pi_ctrl.error_in.eq(error_calc.error_out),
            pi_ctrl.error_valid.eq(error_calc.error_valid),
            pi_ctrl.kp.eq(reg_bank.fast_kp),
            pi_ctrl.ki.eq(reg_bank.fast_ki),
            pi_ctrl.lock_enable.eq(lock_fsm.feedback_enable),
            pi_ctrl.hold_enable.eq(reg_bank.hold_request),
            pi_ctrl.integrator_reset.eq(reg_bank.integrator_reset),
            pi_ctrl.integrator_load.eq(reg_bank.integrator_load | (lock_fsm.state == LockState.ARM_LOCK)),
            pi_ctrl.load_value.eq(pi_load_value),
            pi_ctrl.out_min.eq(reg_bank.fast_out_min),
            pi_ctrl.out_max.eq(reg_bank.fast_out_max),
            pi_ctrl.out_safe.eq(reg_bank.fast_out_safe),
        ]

        # The output limiter clamps the actuator-facing signal before it reaches
        # the final safety gate.
        m.d.comb += [
            output_limiter.i_u.eq(pi_ctrl.control_out),
            output_limiter.i_valid.eq(pi_ctrl.control_valid),
            output_limiter.i_min.eq(-32768),
            output_limiter.i_max.eq(32767),
        ]

        # FaultGate is the final safety stage and must override the fast command.
        m.d.comb += [
            fault_gate.i_u.eq(output_limiter.o_u),
            fault_gate.i_valid.eq(output_limiter.o_valid),
            fault_gate.i_fault.eq(fault_source),
        ]

        m.d.comb += [
            dac_fast_fmt.i_u.eq(fault_gate.o_u),
            dac_fast_fmt.i_valid.eq(fault_gate.o_valid),
            dac_fast_fmt.i_mode.eq(0),
        ]

        # The slow path uses the ramp generator during scan states and the
        # recentering block once the slow control loop is active. The repo does
        # not contain a separate DAC-slow formatter module, so the recenter block
        # is used as the slow-path formatter/driver in this integration.
        slow_dac_source = Signal(signed(16))
        scan_path_active = Signal()
        m.d.comb += [
            scan_path_active.eq(
                (lock_fsm.state == LockState.WIDE_SCAN)
                | (lock_fsm.state == LockState.TRACE_READY)
                | (lock_fsm.state == LockState.ZOOM_SCAN)
                | (lock_fsm.state == LockState.FEATURE_VERIFY)
                | (lock_fsm.state == LockState.RELOCK_SCAN)
            ),
            slow_dac_source.eq(Mux(scan_path_active, ramp_scan.ramp_out, slow_recenter.slow_out)),
            ramp_scan.enable.eq(lock_fsm.wide_scan_enable | lock_fsm.zoom_scan_enable),
            ramp_scan.zoom_mode.eq(lock_fsm.zoom_scan_enable),
            # The FSM decides when scanning happens; the register bank provides
            # the scan parameters and speed settings that define how it scans.
            ramp_scan.ramp_min.eq(reg_bank.ramp_min),
            ramp_scan.ramp_max.eq(reg_bank.ramp_max),
            ramp_scan.ramp_step.eq(reg_bank.ramp_step),
            ramp_scan.ramp_tick_div.eq(reg_bank.ramp_tick_div),
            ramp_scan.ramp_center.eq(reg_bank.ramp_center),
            ramp_scan.ramp_width.eq(reg_bank.ramp_width),
            autolock.scan_valid.eq(ramp_scan.ramp_valid),
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
            slow_recenter.fault_force.eq(lock_fsm.fault_state),
        ]

        # Trace capture records the scan position and the current error sample
        # without entering the fast path.
        m.d.comb += [
            trace_capture.scan_code.eq(ramp_scan.ramp_out),
            trace_capture.error_sample.eq(error_calc.error_out),
            trace_capture.sample_valid.eq(ramp_scan.cycle_done),
            trace_capture.ch1_sample.eq(adc_frontend.o_ch1),
        ]

        # The lock FSM is the system supervisor.
        # It receives commands from the register bank and status information
        # from the other modules to decide the current operating state:
        # idle, scanning, locking, locked, or fault.
        m.d.comb += [
            lock_fsm.global_enable.eq(reg_bank.global_enable),
            lock_fsm.lock_enable_request.eq(reg_bank.lock_enable_request),
            lock_fsm.hold_request.eq(reg_bank.hold_request),
            lock_fsm.fault_active.eq(fault_source),
            lock_fsm.fault_clear_request.eq(reg_bank.fault_clear_pulse),
            lock_fsm.trace_ready.eq(trace_capture.trace_ready),
            lock_fsm.feature_selected.eq(self.i_feature_selected),
            lock_fsm.zoom_complete.eq(ramp_scan.cycle_done),
            lock_fsm.autolock_success.eq(autolock.feature_match),
            lock_fsm.autolock_failed.eq(autolock.feature_failed),
            lock_fsm.lock_check_pass.eq(lock_quality_ok),
            lock_fsm.lock_check_failed.eq(~lock_quality_ok),
            lock_fsm.relock_request.eq(lock_watch.relock_request),
        ]

        # LockWatch watches the active servo loop and requests relock or fault
        # handling if the loop leaves a healthy operating region.
        m.d.comb += [
            lock_watch.enable.eq(lock_fsm.lock_watch_enable),
            lock_watch.lock_active.eq(lock_fsm.state == LockState.LOCKED),
            lock_watch.error_value.eq(error_calc.error_out),
            lock_watch.max_error.eq(1000),
            lock_watch.fast_output.eq(dac_fast_fmt.o_dac),
            lock_watch.fast_min.eq(0),
            lock_watch.fast_max.eq(65535),
            lock_watch.slow_output.eq(slow_recenter.slow_out),
            lock_watch.slow_min.eq(0),
            lock_watch.slow_max.eq(65535),
            lock_watch.fast_saturated.eq(output_limiter.o_sat),
            lock_watch.slow_saturated.eq(slow_recenter.slow_saturated),
            lock_watch.adc_valid.eq(adc_frontend.o_valid),
            lock_watch.saturation_timeout.eq(100),
            lock_watch.adc_timeout.eq(50),
            lock_watch.jump_limit.eq(1000),
            lock_watch.error_timeout.eq(200),
        ]

        # The fault vector is a simple composite of the available safety inputs.
        m.d.comb += [
            fault_vector[0].eq(adc_frontend.o_fault_flags[0]),
            fault_vector[1].eq(adc_frontend.o_fault_flags[1]),
            fault_vector[2].eq(adc_frontend.o_fault_flags[2]),
            fault_vector[3].eq(adc_frontend.o_fault_flags[3]),
            fault_vector[4].eq(adc_frontend.o_fault_flags[4]),
            fault_vector[5].eq(lock_watch.fault_request),
            fault_vector[6].eq(lock_watch.relock_request),
            fault_vector[7].eq(self.i_external_interlock),
            fault_vector[8].eq(0),
            fault_vector[9].eq(0),
            fault_vector[10].eq(0),
            fault_vector[11].eq(0),
            fault_source.eq(lock_fsm.fault_state | lock_watch.fault_request | self.i_external_interlock),
        ]

        # Top-level outputs expose the current status for software and board glue.
        m.d.comb += [
            self.o_dac_fast.eq(dac_fast_fmt.o_dac),
            self.o_dac_slow.eq(slow_dac_source),
            self.o_dac_mod.eq(pdh_frontend.mod_out),
            self.lock_state.eq(lock_fsm.state),
            self.lock_fault.eq(lock_fsm.fault_state),
            self.fast_output.eq(dac_fast_fmt.o_dac),
            self.slow_output.eq(slow_dac_source),
            self.trace_ready.eq(trace_capture.trace_ready),
        ]

        return m
