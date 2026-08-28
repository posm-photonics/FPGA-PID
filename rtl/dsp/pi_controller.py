# pi_controller.py
# Fixed-point PI controller for POSM MTS laser lock
# with integrated continuous relay-feedback auto-tuner
#
# Auto-tuner method: Åström-Hägglund relay feedback
#   1. Injects a relay (bang-bang) perturbation on a secondary tuning output
#      (DAC_SLOW or an auxiliary path).  The fast feedback path is NOT
#      disturbed, complying with Section 7 fast-path rules.
#   2. Measures the induced oscillation: period Tu and peak-to-peak
#      amplitude 'a' in the error signal.
#   3. Derives ultimate gain:  Ku  = 4*d / (pi * a)   [relay amplitude = d]
#   4. Applies PI Ziegler-Nichols:  Kp = 0.45*Ku,  Ki = 0.54*Ku/Tu
#   5. Updates internal gain registers with exponential moving average (EMA)
#      so gains track slow plant changes without step-kicking the loop.
#
# Architecture compliance (POSM onboarding packet v3):
#   - Auto-tuner is Category B (supervision), never inside the fast path.
#   - Gains are written into the PICore's kp/ki ports from the tuner module;
#     the PICore itself is unchanged and remains deterministic/latency-fixed.
#   - Relay output is a separate signal (relay_out) meant to drive DAC_SLOW
#     or a dedicated tuning actuator — NOT DAC_FAST.
#   - While TUNING state is active, the tuner freezes the PICore integrator
#     (hold_enable) so the relay perturbation does not corrupt integral state.
#
# Gain format: Q3.14 fixed-point (same as original)
#   real_gain = register_value / 2^14
#   example: Kp=0.5 -> store 8192
#
# N.B: Q3.14 is a fixed-point number format used to represent floating points.
#
# PICore latency: 2 clock cycles, error_valid -> control_valid.
#   Stage 1 is the multiply (Kp*e and Ki*e), registered so the DSP48E1
#   absorbs it as MREG/PREG. Stage 2 is the >>gain_frac scaling, the
#   accumulator add, the limit comparison and the output capture.
#
#   This was 1 cycle and one enormous combinational path. S3-2 predicted
#   the consequence and said "if synthesis cannot close 20x18 multiply +
#   40-bit add + 40-bit compare in 8 ns on the -1 part, add a DSP48 MREG
#   here and re-declare the latency as 2." Synthesis was finally run:
#   structural analysis of the synth_xilinx netlist measured 20 logic
#   levels and 10.02 ns of logic delay on that path, before any routing,
#   against an 8 ns budget. So the MREG was added and the latency is
#   re-declared as 2, exactly as S3-2 instructed.
#
#   The integrator recurrence is deliberately NOT pipelined. It is a
#   single-cycle feedback loop and splitting it would halve the
#   effective integral rate.
#
# WARNING -- RelayTuner / PIWithAutoTune are NOT part of the shipped
# design. top/lock_core_top.py instantiates PICore directly. Nothing in
# POSM_project_FPGALock.pdf asks for a relay auto-tuner. They are kept
# here only because sim/tb_dsp/tb_pi_controller.py imports them; the
# defects found in the audit have been fixed, but this code has never
# run on hardware. Do not swap PIWithAutoTune in for PICore without
# reading the notes in RelayTuner.elaborate() first.
#
# RelayTuner latency: Category B, not on fast path
 
from amaranth import *
from amaranth.sim import *
 
# ---------------------------------------------------------------------------
# PICore — unchanged functional behaviour, gains now driven by RelayTuner
# ---------------------------------------------------------------------------
 
class PICore(Elaboratable):
    """
    Fixed-point PI controller.
 
    Parameters
    ----------
    err_w     : int  error input width  (default 20)
    out_w     : int  DAC output width   (default 16)
    gain_w    : int  gain register width (default 18)
    gain_frac : int  fractional bits in gain Q3.14 (default 14)
    acc_w     : int  accumulator width  (default 40)
 
    Ports
    -----
    error_in         : signed input, err_w bits
    error_valid      : input, 1 bit
    kp               : signed input, gain_w bits (Q3.14)  <-- driven by tuner
    ki               : signed input, gain_w bits (Q3.14)  <-- driven by tuner
    lock_enable      : input, 1 bit
    hold_enable      : input, 1 bit  (freezes output; asserted by tuner during relay)
    integrator_reset : input, 1 bit  (clears integrator)
    integrator_load  : input, 1 bit  (loads integrator)
    load_value       : signed input, acc_w bits
    out_min          : signed input, out_w bits
    out_max          : signed input, out_w bits
    out_safe         : signed input, out_w bits
    control_out      : signed output, out_w bits
    control_valid    : output, 1 bit
    sat_hi           : output, 1 bit
    sat_lo           : output, 1 bit
    """
    """
    Flow of the PI controler
    

                    New error sample?
                       │
             No ───────┴──────► Do nothing
                       │
                      Yes
                       │
              Controller enabled?
                  │          │
                 No         Yes
                  │          │
      Output safe value   Hold enabled?
                  │          │
                  │     Yes ─┴──► Keep previous output,
                  │               don't update integrator
                  │
                  ▼
               No hold
                  │
        Windup suppression active?
             │              │
            Yes            No
             │              │
     Don't update I     I = I + Ki·e
                  │
                  ▼
         Compute PI output
                  │
      Above max? ─► Clamp to max
      Below min? ─► Clamp to min
      Otherwise ──► Output candidate
    """
 
    def __init__(self, err_w=20, out_w=16,
                 gain_w=18, gain_frac=14, acc_w=40):
        self.err_w     = err_w
        self.out_w     = out_w
        self.gain_w    = gain_w
        self.gain_frac = gain_frac
        self.acc_w     = acc_w

        # --- input ports ---
        self.error_in         = Signal(signed(err_w))
        self.error_valid      = Signal()
        self.kp               = Signal(signed(gain_w))
        self.ki               = Signal(signed(gain_w))
        self.lock_enable      = Signal()
        # hold_enable freezes the whole controller: the output stops
        # updating and the integrator stops accumulating.
        self.hold_enable      = Signal()
        # integrator_hold freezes ONLY the integrator. The proportional
        # path keeps running and the output keeps updating. This is what
        # a supervisor wants while it injects a deliberate perturbation
        # (relay tuning, a probe step): the integral state must not
        # absorb the perturbation, but the servo must stay live.
        self.integrator_hold  = Signal()
        self.integrator_reset = Signal()
        self.integrator_load  = Signal()
        # load_value is in OUTPUT (DAC code) units, not raw accumulator
        # units: loading N makes the controller output N when the error
        # is zero. The accumulator scaling is an internal detail.
        self.load_value       = Signal(signed(acc_w))
        self.out_min          = Signal(signed(out_w))
        self.out_max          = Signal(signed(out_w))
        self.out_safe         = Signal(signed(out_w))
        # Leaky-integrator coefficient (packet 11.4 FAST_INT_LEAK).
        # 0 disables the leak entirely. Otherwise the integrator decays
        # by 2^-int_leak_shift of its own value on every update, which
        # is what the CTL200 AC modulation input needs: that path cannot
        # carry true DC authority, so a pure accumulator would wind up
        # against an actuator that physically cannot respond.
        self.int_leak_shift   = Signal(5)

        # --- output ports ---
        self.control_out   = Signal(signed(out_w))
        self.control_valid = Signal()
        self.sat_hi        = Signal()
        self.sat_lo        = Signal()

    def elaborate(self, platform):
        m = Module()

        # ===================================================================
        # AUDIT FIX (S1-2) -- the most serious defect found in the audit.
        # ===================================================================
        # The previous version accumulated an ALREADY-TRUNCATED integral
        # increment:
        #
        #     i_scaled  = (error * ki) >> gain_frac     # floor division
        #     int_next  = integrator + i_scaled
        #
        # An arithmetic right shift is floor division, so it is asymmetric
        # about zero. For |error * ki| < 2^gain_frac this gives exactly 0
        # for a positive error and exactly -1 for a negative error, which
        # produced two separate failures, both reproduced in simulation:
        #
        #   * DEAD ZONE: constant error +100 with ki=128 gave
        #     100*128 = 12800, and 12800 >> 14 = 0. The integrator never
        #     moved. No integral action at all below 2^gain_frac / ki
        #     counts of error.
        #
        #   * RAIL DRIFT: a zero-mean error (+3/-3 alternating, mean
        #     exactly zero) accumulated -1 every other sample and drove
        #     the output to the negative rail in ~8000 clocks (64 us at
        #     125 MHz), where it latched. The servo could not hold a lock
        #     on any noisy signal centred on zero.
        #
        # The fix is the structure both reference sources indicate:
        # accumulate at FULL precision and shift only when reading the
        # integrator out. Linien's gateware/logic/pid.py does exactly
        # this (ki_mult is shifted by 4, not by coeff_width; int_reg
        # holds 18 fractional bits below the output LSB; int_out is
        # int_reg >> extra_width). Packet 8.5 asks for the same thing
        # when it requires a "wide internal accumulator".
        #
        # `integrator` now holds the integral term scaled by 2^gain_frac,
        # so the acc_w=40 bit width finally buys the precision it was
        # always supposed to buy.
        # ===================================================================

        integrator = Signal(signed(self.acc_w))

        mul_w = self.err_w + self.gain_w  # 38 bits

        p_term    = Signal(signed(mul_w))
        i_term    = Signal(signed(mul_w))

        # ===================================================================
        # TIMING FIX -- multiplier pipeline stage.
        # ===================================================================
        # p_term and i_term used to be combinational:
        #
        #     m.d.comb += p_term.eq(self.error_in * self.kp)
        #     m.d.comb += i_term.eq(self.error_in * self.ki)
        #
        # Both map to a DSP48E1. With the product combinational the
        # synthesiser sets MREG=0 and PREG=0, so the whole 20x18 multiply
        # and 48-bit post-adder sit on the same register-to-register path
        # as the 40-bit accumulator arithmetic that follows. Structural
        # analysis of the synth_xilinx netlist measured 20 logic levels
        # and 10.02 ns of logic delay, before routing, against 8 ns.
        #
        # Registering the product lets the DSP absorb it as MREG/PREG.
        # The multiply then becomes a self-contained register-to-register
        # hop inside the DSP tile and leaves the fabric path entirely.
        #
        # Cost: exactly one cycle of latency, declared in
        # docs/02_signal_chain.md (pi_core 1 -> 2 cycles, fast path
        # 7 -> 8 cycles = 64 ns at 125 MHz). Packet 7.3 rates 50 ns
        # "excellent" and 100 ns "still reasonable", so 64 ns stays
        # inside the band. At the loop bandwidths this servo targets
        # (tens to hundreds of kHz) 8 ns of extra delay is under a
        # thousandth of a degree of phase; the pole it adds is at
        # 20 MHz.
        #
        # What is NOT pipelined, deliberately: the integrator recurrence
        # integrator -> int_sum -> int_next -> integrator. That is a
        # single-cycle feedback loop, and splitting it would halve the
        # effective integral rate and change the tuning. It stays as one
        # cycle. i_term entering the loop is now a register output, which
        # shortens the loop path without touching its structure.
        # ===================================================================
        m.d.sync += [
            p_term.eq(self.error_in * self.kp),
            i_term.eq(self.error_in * self.ki),
        ]

        # error_valid has to be delayed by the same cycle so that the
        # integrator update and control_out capture still line up with
        # the sample the products were computed from.
        error_valid_d = Signal()
        m.d.sync += error_valid_d.eq(self.error_valid)
        p_scaled  = Signal(signed(self.acc_w))
        int_out   = Signal(signed(self.acc_w))
        candidate = Signal(signed(self.acc_w))
        int_sum   = Signal(signed(self.acc_w + 1))
        int_next  = Signal(signed(self.acc_w))
        leak      = Signal(signed(self.acc_w))

        sat_hi_comb     = Signal()
        sat_lo_comb     = Signal()
        windup_suppress = Signal()

        out_max_ext = Signal(signed(self.acc_w))
        out_min_ext = Signal(signed(self.acc_w))

        # Integrator saturation bounds, in accumulator units. Linien
        # clamps its integrator register the same way (max_pos_extra /
        # max_neg_extra). This is a second, independent guard on top of
        # the conditional integration below: conditional integration
        # alone leaves the accumulator free to grow whenever the
        # proportional term happens to keep `candidate` in range.
        int_max = Signal(signed(self.acc_w))
        int_min = Signal(signed(self.acc_w))

        # Rounding constant for the fixed-point shifts. Round-to-nearest
        # instead of floor removes the -0.5 LSB systematic bias that the
        # truncating shifts introduced on every term.
        round_half = (1 << (self.gain_frac - 1))

        m.d.comb += [
            out_max_ext.eq(self.out_max),
            out_min_ext.eq(self.out_min),

            int_max.eq(self.out_max << self.gain_frac),
            int_min.eq(self.out_min << self.gain_frac),

            # Proportional term, scaled back to output units with
            # round-to-nearest.
            p_scaled.eq((p_term + round_half) >> self.gain_frac),

            # Integrator read-out, scaled back to output units with
            # round-to-nearest.
            int_out.eq((integrator + round_half) >> self.gain_frac),

            # Optional leak toward zero (0 shift = no leak).
            leak.eq(Mux(self.int_leak_shift == 0,
                        0,
                        integrator >> self.int_leak_shift)),

            # I[n+1] = I[n] - leak + Ki*e[n], all at full precision
            int_sum.eq(integrator - leak + i_term),

            # u[n] = Kp*e[n] + I[n]
            candidate.eq(p_scaled + int_out),

            sat_hi_comb.eq(candidate > out_max_ext),
            sat_lo_comb.eq(candidate < out_min_ext),

            # Conditional integration: stop integrating only when the
            # output is already at a limit AND the new increment would
            # push it further in the same direction. Motion back toward
            # the linear region is always allowed.
            #
            # This now tests i_term (the true, unrounded increment)
            # rather than the truncated i_scaled, which used to read as
            # zero inside the dead zone and released anti-windup when it
            # should not have.
            # TIMING FIX: this used to read sat_hi_comb / sat_lo_comb,
            # the COMBINATIONAL saturation flags. That closed a feedback
            # loop
            #
            #   integrator -> int_out -> candidate -> sat_*_comb
            #              -> windup_suppress -> integrator
            #
            # which put a 40-bit add AND a 40-bit compare inside the
            # single-cycle integrator recurrence, on top of the 40-bit
            # accumulate the recurrence already needs. Measured at 19
            # logic levels.
            #
            # self.sat_hi / self.sat_lo are the same flags one cycle
            # later, already registered as module outputs. Using them
            # takes the whole candidate-and-compare chain out of the
            # recurrence and leaves only the accumulate.
            #
            # Behavioural effect: anti-windup engages one sample (8 ns)
            # after the output reaches a limit instead of in the same
            # sample. Saturation events last for thousands of samples --
            # that is the point of anti-windup -- so one sample of extra
            # integration at the moment of entry is far below the
            # accumulator's LSB in any real event. Gating integration on
            # the registered saturation flag is the standard structure;
            # TC06 covers it.
            windup_suppress.eq(
                (self.sat_hi & (i_term > 0)) |
                (self.sat_lo & (i_term < 0))
            ),
        ]

        # Hard clamp on the accumulator itself.
        with m.If(int_sum > int_max):
            m.d.comb += int_next.eq(int_max)
        with m.Elif(int_sum < int_min):
            m.d.comb += int_next.eq(int_min)
        with m.Else():
            m.d.comb += int_next.eq(int_sum)

        # Preload path: load_value arrives in output units, so scale it
        # into accumulator units and clamp it to the same bounds the
        # running accumulator obeys.
        load_scaled = Signal(signed(self.acc_w))
        load_raw    = Signal(signed(self.acc_w + 1))
        m.d.comb += load_raw.eq(self.load_value << self.gain_frac)
        with m.If(load_raw > int_max):
            m.d.comb += load_scaled.eq(int_max)
        with m.Elif(load_raw < int_min):
            m.d.comb += load_scaled.eq(int_min)
        with m.Else():
            m.d.comb += load_scaled.eq(load_raw)

        with m.If(self.integrator_reset): # useful during fault, relock, startup
            m.d.sync += integrator.eq(0)

        with m.Elif(self.integrator_load):
            m.d.sync += integrator.eq(load_scaled)

        with m.Elif(error_valid_d & self.lock_enable
                    & ~self.hold_enable & ~self.integrator_hold):
            with m.If(~windup_suppress): # Only integrate if windup_suppress = 0
                m.d.sync += integrator.eq(int_next)

        m.d.sync += self.control_valid.eq(0)
 
        with m.If(error_valid_d & self.lock_enable):
 
            with m.If(self.hold_enable):
                # The previous output remains stored.
                # So the DAC keeps receiving the previous value.
                # The controller is effectively frozen.
                m.d.sync += self.control_valid.eq(1)
 
            with m.Else():
                m.d.sync += self.control_valid.eq(1)
 
                with m.If(sat_hi_comb):
                    m.d.sync += [
                        self.control_out.eq(self.out_max),
                        self.sat_hi.eq(1),
                        self.sat_lo.eq(0),
                    ]
                with m.Elif(sat_lo_comb):
                    m.d.sync += [
                        self.control_out.eq(self.out_min),
                        self.sat_hi.eq(0),
                        self.sat_lo.eq(1),
                    ]
                with m.Else():
                    m.d.sync += [
                        # take only the lower out_w bits of candidate
                        # because DAC only accepts 16 bits
                        self.control_out.eq(candidate[:self.out_w]),

                        self.sat_hi.eq(0),
                        self.sat_lo.eq(0),
                    ]
 
        with m.Elif(~self.lock_enable):
            # If the controller isn't enabled,
            # don't run PI control.
            m.d.sync += [
                self.control_out.eq(self.out_safe),
                self.sat_hi.eq(0),
                self.sat_lo.eq(0),
                self.control_valid.eq(error_valid_d),
            ]
 
        return m
 
 
# ---------------------------------------------------------------------------
# RelayTuner — Category B supervisor, continuously adapts kp / ki
# ---------------------------------------------------------------------------
 
class RelayTuner(Elaboratable):
    """
    Åström-Hägglund relay-feedback auto-tuner.
 
    This module is Category B (supervision/acquisition).  It must never be
    placed inside the POSM fast feedback path (Section 7.2).
 
    The tuner injects a relay (bang-bang) signal onto relay_out, which should
    be routed to DAC_SLOW or a dedicated secondary actuator — never DAC_FAST.
    It observes the closed-loop error oscillation to estimate Ku and Tu, then
    continuously updates kp_out and ki_out using EMA smoothing.
 
    During an active relay cycle the tuner asserts hold_request so the PICore
    freezes its integrator, preventing relay perturbations from corrupting
    integral state.  The PICore continues to pass error_valid samples through
    with hold_enable asserted (output is held, not zeroed).
 
    Parameters
    ----------
    err_w       : int   error signal width (must match PICore.err_w)
    gain_w      : int   output gain width  (must match PICore.gain_w)
    gain_frac   : int   Q-format fractional bits (must match PICore.gain_frac)
    relay_w     : int   relay output code width (matches DAC_SLOW width)
    relay_amp   : int   relay amplitude in DAC_SLOW codes (the 'd' in Ku formula)
    min_half_periods : int  minimum half-periods before accepting Tu estimate
    ema_shift   : int   EMA weight as right-shift: alpha = 1/2^ema_shift
                        e.g. ema_shift=3 -> alpha=0.125 (slow tracking)
    kp_init     : int   initial kp in Q3.14 counts (used before first tune)
    ki_init     : int   initial ki in Q3.14 counts
 
    Ports
    -----
    error_in    : signed input, err_w bits  (same error fed to PICore)
    error_valid : input, 1 bit
    tune_enable : input, 1 bit (enable continuous background tuning)
    relay_out   : signed output, relay_w bits  -> route to DAC_SLOW
    hold_request: output, 1 bit  -> wire to PICore.hold_enable
    kp_out      : signed output, gain_w bits  -> wire to PICore.kp
    ki_out      : signed output, gain_w bits  -> wire to PICore.ki
    tuning_active : output, 1 bit  (high during relay measurement phase)
    tune_valid  : output, 1 bit   (pulses when new gains have been committed)
    ku_out      : signed output, gain_w bits  (diagnostic: latest Ku estimate)
    tu_out      : output, 32 bits             (diagnostic: latest Tu in samples)
    """
 
    def __init__(self,
                 err_w=20,
                 gain_w=18,
                 gain_frac=14,
                 relay_w=16,
                 relay_amp=512,          # DAC_SLOW codes; tune for plant
                 min_half_periods=6,     # need at least 3 full cycles
                 ema_shift=3,            # alpha = 1/8; slow, stable tracking
                 kp_init=4096,           # 0.25 in Q3.14
                 ki_init=128):           # small initial Ki
        self.err_w            = err_w
        self.gain_w           = gain_w
        self.gain_frac        = gain_frac
        self.relay_w          = relay_w
        self.relay_amp        = relay_amp
        self.min_half_periods = min_half_periods
        self.ema_shift        = ema_shift
        self.kp_init          = kp_init
        self.ki_init          = ki_init
 
        # --- input ports ---
        self.error_in    = Signal(signed(err_w))
        self.error_valid = Signal()
        self.tune_enable = Signal()
 
        # --- output ports ---
        self.relay_out      = Signal(signed(relay_w))
        self.hold_request   = Signal()
        self.kp_out         = Signal(signed(gain_w))
        self.ki_out         = Signal(signed(gain_w))
        self.tuning_active  = Signal()
        self.tune_valid     = Signal()
        self.ku_out         = Signal(signed(gain_w))
        self.tu_out         = Signal(32)  
    def elaborate(self, platform):
        m = Module()
 
        gain_frac = self.gain_frac
        # ---------------------------------------------------------------
        # Gain registers (EMA-smoothed, updated after each relay cycle)
        # ---------------------------------------------------------------
        kp_reg = Signal(signed(self.gain_w), init=self.kp_init)
        ki_reg = Signal(signed(self.gain_w), init=self.ki_init)

        m.d.comb += [
            self.kp_out.eq(kp_reg),
            self.ki_out.eq(ki_reg),
        ]
 
        # relay output register
        relay_reg = Signal(signed(self.relay_w))
        m.d.sync += self.relay_out.eq(relay_reg)
 
        # oscillation measurement registers
        half_period_counter = Signal(32)   # counts samples in current half-period
        half_period_sum     = Signal(32)   # accumulates half-period lengths
        half_period_count   = Signal(16)   # number of half-periods observed
        peak_acc            = Signal(32)   # accumulates |error| for amplitude est.
        peak_count          = Signal(32)   # sample count for peak averaging
        error_prev          = Signal(signed(self.err_w))   # for zero-crossing detect
 
        # zero-crossing flag (combinational)
        # detect sign change: prev and current have opposite signs
        zero_cross = Signal()
        m.d.comb += zero_cross.eq(
            (error_prev[-1] != self.error_in[-1]) &  # MSB = sign bit
            (error_prev != 0)
        )
 
        # ---------------------------------------------------------------
        # COMPUTE stage signals (wide arithmetic then truncate to Q3.14)
        #
        # Ku = 4*d / (pi * a)
        #   d   = relay_amp  (integer DAC codes)
        #   a   = peak_acc / peak_count  (average |error| over relay cycle)
        #   pi ≈ 103993/33102  — rational approximation, or use pi_approx below
        #
        # To stay in fixed-point without division, we do:
        #   a_est = peak_acc / peak_count  (approximated as peak_acc >> log2(peak_count))
        #     --> for simplicity we accumulate over a power-of-two window;
        #         peak_count is constrained to 2^PEAK_SHIFT samples.
        #
        # Ku in Q3.14:
        #   Ku_q = round( 4 * relay_amp * 2^gain_frac / (pi * a_est) )
        #        = round( 4 * relay_amp * 2^gain_frac * 33102 / (103993 * a_est) )
        #
        # Tu = half_period_sum / half_period_count  (in samples)
        #
        # PI Ziegler-Nichols from relay:
        #   Kp = 0.45 * Ku   =>  Kp_q = (Ku_q * 29491) >> 16   (0.45 * 2^16 = 29491)
        #   Ki per-sample = Kp / (0.85 * Tu)
        #        => Ki_q = (Kp_q * 2^gain_frac) / (0.85 * Tu * 2^gain_frac)
        #                = Kp_q / (0.85 * Tu)
        #        => Ki_q = (Kp_q * 77309) >> (16 + log2(Tu))
        #          (0.85 * 2^16 = 55705; 1/0.85 * 2^16 = 77109)
        #
        # All intermediate products use wide Signals to avoid overflow.
        # ---------------------------------------------------------------
 
        # AUDIT FIX (S2-11.3): the amplitude estimate used to be
        #     a_est = peak_acc >> PEAK_SHIFT     # PEAK_SHIFT fixed at 8
        # with a comment claiming "peak_count is constrained to
        # 2^PEAK_SHIFT samples". Nothing constrained it. peak_count was
        # accumulated and never read, and a half-period is however many
        # samples the oscillation actually takes, so dividing by a fixed
        # 256 produced a meaningless number (off by ~39x for a 10k-sample
        # half period). peak_acc could also wrap: 32 bits accumulating up
        # to 2^19 per sample overflows after ~8000 samples.
        #
        # Now: divide by the real sample count (nearest power of two, via
        # the same priority-encoder trick used for Tu below), and
        # saturate the accumulator instead of wrapping.
        PEAK_ACC_MAX = (1 << 32) - 1


        # compute-stage registers
        tu_reg  = Signal(32)
        ku_reg  = Signal(signed(self.gain_w))
 
        m.d.comb += [
            self.tu_out.eq(tu_reg),
            self.ku_out.eq(ku_reg),
        ]
 
        # wide intermediates for Ku computation
        # We use Cat() / bit slicing rather than Python division
        relay_amp_sig = Signal(32, init=self.relay_amp)
 
        # numerator:  4 * relay_amp * 33102 * 2^gain_frac
        # denominator: 103993 * a_est
        # We compute in a registered COMPUTE cycle to avoid timing pressure.
 
        # a_est = peak_acc / peak_count  (average |error|, Q0 integer codes)
        # Divisor is the real accumulated sample count, rounded down to a
        # power of two so the division is a barrel shift.
        peak_count_log2 = Signal(6)
        with m.If(peak_count[31]):
            m.d.comb += peak_count_log2.eq(31)
        for _b in range(30, 0, -1):
            with m.Elif(peak_count[_b]):
                m.d.comb += peak_count_log2.eq(_b)
        with m.Else():
            m.d.comb += peak_count_log2.eq(0)

        a_est = Signal(32)
        m.d.comb += a_est.eq(peak_acc >> peak_count_log2)

        # Saturating |error| accumulate, shared by RELAY_P and RELAY_N.
        err_abs      = Signal(self.err_w)
        peak_acc_inc = Signal(33)
        peak_acc_sat = Signal(32)
        m.d.comb += [
            err_abs.eq(Mux(self.error_in[-1], -self.error_in, self.error_in)),
            peak_acc_inc.eq(peak_acc + err_abs),
        ]
        with m.If(peak_acc_inc > PEAK_ACC_MAX):
            m.d.comb += peak_acc_sat.eq(PEAK_ACC_MAX)
        with m.Else():
            m.d.comb += peak_acc_sat.eq(peak_acc_inc)
 
        # Ku numerator = 4 * relay_amp << gain_frac  (scaled by 2^14)
        # ku_num already carries the factor of 4 and the Q3.14 scaling.
        ku_num = Signal(64)
        m.d.comb += ku_num.eq((relay_amp_sig << (gain_frac + 2)))

        # AUDIT FIX (S2-11.2): this was
        #     ku_scaled = (ku_num * 10430) >> (gain_frac + 15)
        # with a comment claiming 10430/32768 ~ 4/pi ~ 1.2732. Two
        # errors compounded:
        #   * 10430/32768 = 0.3183 = 1/pi, not 4/pi. The factor of 4 is
        #     already inside ku_num, so 1/pi is the correct constant and
        #     the COMMENT was what was wrong there.
        #   * the shift double-applied the Q3.14 scaling. ku_num is
        #     already scaled by 2^gain_frac, so shifting by
        #     gain_frac + 15 removed it again. With relay_amp = 512 this
        #     produced 652 where the correct pre-division value is
        #     1.068e7, i.e. Ku came out 2^14 times too small and the
        #     tuner would have driven kp and ki toward zero.
        #
        # Correct: Ku_q * a_est = 4 * d * 2^gain_frac / pi
        #                       = ku_num * (10430 / 2^15)
        ku_scaled = Signal(64)
        m.d.comb += ku_scaled.eq(
            (ku_num * 10430) >> 15
        )
 
        # a_est divides ku_scaled; we do this with a registered shift-divide
        # (combinational divider avoided per fast-path rules; COMPUTE is 1 cycle
        #  of registered logic in Category B — acceptable)
        #
        # ku_final = ku_scaled / a_est   -- approximated as:
        #   if a_est == 0 keep old gains (divide-by-zero guard)
        #   else use the closest power-of-two approximation:
        #     ku_final ≈ ku_scaled >> clog2(a_est)
        #
        # For continuous adaptation, exact division is not required; the EMA
        # smoothing handles cycle-to-cycle noise.  A barrel-shift approximation
        # (true for power-of-two amplitudes or near-sinusoidal oscillations)
        # is sufficient and synthesises to a short combinational path.
 
        # log2 approximation: count leading zeros in a_est (priority encoder)
        # We implement a simple 32-bit CLZ (count leading zeros) as a function.
        # Amaranth does not have a built-in CLZ, so we use a cascade of If/Elif.
        a_est_log2 = Signal(6)   # floor(log2(a_est)), max 31
 
        with m.If(a_est[31]):    m.d.comb += a_est_log2.eq(31)
        with m.Elif(a_est[30]): m.d.comb += a_est_log2.eq(30)
        with m.Elif(a_est[29]): m.d.comb += a_est_log2.eq(29)
        with m.Elif(a_est[28]): m.d.comb += a_est_log2.eq(28)
        with m.Elif(a_est[27]): m.d.comb += a_est_log2.eq(27)
        with m.Elif(a_est[26]): m.d.comb += a_est_log2.eq(26)
        with m.Elif(a_est[25]): m.d.comb += a_est_log2.eq(25)
        with m.Elif(a_est[24]): m.d.comb += a_est_log2.eq(24)
        with m.Elif(a_est[23]): m.d.comb += a_est_log2.eq(23)
        with m.Elif(a_est[22]): m.d.comb += a_est_log2.eq(22)
        with m.Elif(a_est[21]): m.d.comb += a_est_log2.eq(21)
        with m.Elif(a_est[20]): m.d.comb += a_est_log2.eq(20)
        with m.Elif(a_est[19]): m.d.comb += a_est_log2.eq(19)
        with m.Elif(a_est[18]): m.d.comb += a_est_log2.eq(18)
        with m.Elif(a_est[17]): m.d.comb += a_est_log2.eq(17)
        with m.Elif(a_est[16]): m.d.comb += a_est_log2.eq(16)
        with m.Elif(a_est[15]): m.d.comb += a_est_log2.eq(15)
        with m.Elif(a_est[14]): m.d.comb += a_est_log2.eq(14)
        with m.Elif(a_est[13]): m.d.comb += a_est_log2.eq(13)
        with m.Elif(a_est[12]): m.d.comb += a_est_log2.eq(12)
        with m.Elif(a_est[11]): m.d.comb += a_est_log2.eq(11)
        with m.Elif(a_est[10]): m.d.comb += a_est_log2.eq(10)
        with m.Elif(a_est[9]):  m.d.comb += a_est_log2.eq(9)
        with m.Elif(a_est[8]):  m.d.comb += a_est_log2.eq(8)
        with m.Elif(a_est[7]):  m.d.comb += a_est_log2.eq(7)
        with m.Elif(a_est[6]):  m.d.comb += a_est_log2.eq(6)
        with m.Elif(a_est[5]):  m.d.comb += a_est_log2.eq(5)
        with m.Elif(a_est[4]):  m.d.comb += a_est_log2.eq(4)
        with m.Elif(a_est[3]):  m.d.comb += a_est_log2.eq(3)
        with m.Elif(a_est[2]):  m.d.comb += a_est_log2.eq(2)
        with m.Elif(a_est[1]):  m.d.comb += a_est_log2.eq(1)
        with m.Else():          m.d.comb += a_est_log2.eq(0)
 
        # AUDIT FIX (S2-11.7): `ku_final` is signed(gain_w) and
        # `ku_scaled >> a_est_log2` was assigned into it with NO
        # saturation. Once the Ku scaling error above was corrected the
        # true magnitude is ~2^14 larger, so the quotient routinely
        # exceeds 2^(gain_w-1)-1 and wrapped NEGATIVE. A negative Kp/Ki
        # inverts the sign of the feedback and drives the actuator
        # straight to a rail, which is exactly what happened: the
        # closed-loop test produced kp=-6753, ki=-2026 and pinned the
        # output at out_min.
        #
        # A relay experiment can only yield a positive ultimate gain, so
        # clamp to [0, 2^(gain_w-1)-1]. Packet 4.4 is explicit about why
        # this matters: "If the controller polarity is wrong, the loop
        # becomes positive feedback and runs away."
        gain_max = (1 << (self.gain_w - 1)) - 1

        ku_quot  = Signal(64)
        ku_final = Signal(signed(self.gain_w))
        m.d.comb += ku_quot.eq(ku_scaled >> a_est_log2)
        with m.If(ku_quot > gain_max):
            m.d.comb += ku_final.eq(gain_max)
        with m.Else():
            m.d.comb += ku_final.eq(ku_quot)
 
        # ---------------------------------------------------------------
        # EMA update helper:
        #   new = old + (target - old) >> ema_shift
        #       = old * (1 - alpha) + target * alpha,  alpha = 1/2^ema_shift
        # ---------------------------------------------------------------
        def ema(m, old_reg, new_val, tmp_w=32):
            delta = Signal(signed(tmp_w))
            m.d.comb += delta.eq(new_val - old_reg)
            m.d.sync += old_reg.eq(old_reg + (delta >> self.ema_shift))
 
        # Kp_q = 0.45 * Ku_q  ~=  (Ku_q * 7373) >> 14   (0.45 * 2^14 = 7373)
        kp_wide = Signal(64)
        kp_new  = Signal(signed(self.gain_w))
        m.d.comb += kp_wide.eq((ku_final * 7373) >> gain_frac)
        with m.If(kp_wide > gain_max):
            m.d.comb += kp_new.eq(gain_max)
        with m.Else():
            m.d.comb += kp_new.eq(kp_wide)
 
        # Tu = half_period_sum / half_period_count
        # Approximated as barrel-shift division (same CLZ trick as above)
        hp_count_log2 = Signal(6)
        with m.If(half_period_count[15]):    m.d.comb += hp_count_log2.eq(15)
        with m.Elif(half_period_count[14]): m.d.comb += hp_count_log2.eq(14)
        with m.Elif(half_period_count[13]): m.d.comb += hp_count_log2.eq(13)
        with m.Elif(half_period_count[12]): m.d.comb += hp_count_log2.eq(12)
        with m.Elif(half_period_count[11]): m.d.comb += hp_count_log2.eq(11)
        with m.Elif(half_period_count[10]): m.d.comb += hp_count_log2.eq(10)
        with m.Elif(half_period_count[9]):  m.d.comb += hp_count_log2.eq(9)
        with m.Elif(half_period_count[8]):  m.d.comb += hp_count_log2.eq(8)
        with m.Elif(half_period_count[7]):  m.d.comb += hp_count_log2.eq(7)
        with m.Elif(half_period_count[6]):  m.d.comb += hp_count_log2.eq(6)
        with m.Elif(half_period_count[5]):  m.d.comb += hp_count_log2.eq(5)
        with m.Elif(half_period_count[4]):  m.d.comb += hp_count_log2.eq(4)
        with m.Elif(half_period_count[3]):  m.d.comb += hp_count_log2.eq(3)
        with m.Elif(half_period_count[2]):  m.d.comb += hp_count_log2.eq(2)
        with m.Elif(half_period_count[1]):  m.d.comb += hp_count_log2.eq(1)
        with m.Else():                       m.d.comb += hp_count_log2.eq(0)
 
        tu_est = Signal(32)
        m.d.comb += tu_est.eq(half_period_sum >> hp_count_log2)
 
        # Ki per-sample in Q3.14:
        #   Ki = 0.54 * Ku / Tu   (Ziegler-Nichols PI from relay)
        #   Ki_q = (Ku_q * 8847) >> (gain_frac + log2(Tu))
        #          where 0.54 * 2^14 = 8847
        tu_log2 = Signal(6)
        with m.If(tu_est[31]):    m.d.comb += tu_log2.eq(31)
        with m.Elif(tu_est[30]): m.d.comb += tu_log2.eq(30)
        with m.Elif(tu_est[29]): m.d.comb += tu_log2.eq(29)
        with m.Elif(tu_est[28]): m.d.comb += tu_log2.eq(28)
        with m.Elif(tu_est[27]): m.d.comb += tu_log2.eq(27)
        with m.Elif(tu_est[26]): m.d.comb += tu_log2.eq(26)
        with m.Elif(tu_est[25]): m.d.comb += tu_log2.eq(25)
        with m.Elif(tu_est[24]): m.d.comb += tu_log2.eq(24)
        with m.Elif(tu_est[23]): m.d.comb += tu_log2.eq(23)
        with m.Elif(tu_est[22]): m.d.comb += tu_log2.eq(22)
        with m.Elif(tu_est[21]): m.d.comb += tu_log2.eq(21)
        with m.Elif(tu_est[20]): m.d.comb += tu_log2.eq(20)
        with m.Elif(tu_est[19]): m.d.comb += tu_log2.eq(19)
        with m.Elif(tu_est[18]): m.d.comb += tu_log2.eq(18)
        with m.Elif(tu_est[17]): m.d.comb += tu_log2.eq(17)
        with m.Elif(tu_est[16]): m.d.comb += tu_log2.eq(16)
        with m.Elif(tu_est[15]): m.d.comb += tu_log2.eq(15)
        with m.Elif(tu_est[14]): m.d.comb += tu_log2.eq(14)
        with m.Elif(tu_est[13]): m.d.comb += tu_log2.eq(13)
        with m.Elif(tu_est[12]): m.d.comb += tu_log2.eq(12)
        with m.Elif(tu_est[11]): m.d.comb += tu_log2.eq(11)
        with m.Elif(tu_est[10]): m.d.comb += tu_log2.eq(10)
        with m.Elif(tu_est[9]):  m.d.comb += tu_log2.eq(9)
        with m.Elif(tu_est[8]):  m.d.comb += tu_log2.eq(8)
        with m.Elif(tu_est[7]):  m.d.comb += tu_log2.eq(7)
        with m.Elif(tu_est[6]):  m.d.comb += tu_log2.eq(6)
        with m.Elif(tu_est[5]):  m.d.comb += tu_log2.eq(5)
        with m.Elif(tu_est[4]):  m.d.comb += tu_log2.eq(4)
        with m.Elif(tu_est[3]):  m.d.comb += tu_log2.eq(3)
        with m.Elif(tu_est[2]):  m.d.comb += tu_log2.eq(2)
        with m.Elif(tu_est[1]):  m.d.comb += tu_log2.eq(1)
        with m.Else():           m.d.comb += tu_log2.eq(0)
 
        # Wide multiply before shift to avoid losing precision
        ki_wide = Signal(64)
        ki_quot = Signal(64)
        ki_new  = Signal(signed(self.gain_w))
        # Shift by gain_frac FIRST so the Tu division keeps its precision;
        # the previous order divided by Tu before descaling and threw
        # away low bits for long oscillation periods.
        m.d.comb += ki_wide.eq((ku_final * 8847) >> gain_frac)
        m.d.comb += ki_quot.eq(ki_wide >> tu_log2)
        with m.If(ki_quot > gain_max):
            m.d.comb += ki_new.eq(gain_max)
        with m.Else():
            m.d.comb += ki_new.eq(ki_quot)
 
        # ---------------------------------------------------------------
        # State machine
        # ---------------------------------------------------------------
        # Relay state machine
        #
        # States:
        #   IDLE    : waiting for tune_enable, outputting relay_out = 0
        #   RELAY_P : relay output = +relay_amp, watching for zero crossing
        #   RELAY_N : relay output = -relay_amp, watching for zero crossing
        #   COMPUTE : one-cycle gain calculation after enough half-periods
        # ---------------------------------------------------------------
        with m.FSM(reset="IDLE"):
 
            with m.State("IDLE"):
                m.d.sync += [
                    relay_reg.eq(0),
                    self.hold_request.eq(0),
                    self.tuning_active.eq(0),
                    self.tune_valid.eq(0),
                    half_period_counter.eq(0),
                    half_period_sum.eq(0),
                    half_period_count.eq(0),
                    peak_acc.eq(0),
                    peak_count.eq(0),
                ]
                with m.If(self.tune_enable):
                    m.next = "RELAY_P"
 
            with m.State("RELAY_P"):
                # Positive relay phase: inject +relay_amp, watch for neg zero-cross
                m.d.sync += [
                    relay_reg.eq(self.relay_amp),
                    self.hold_request.eq(1),
                    self.tuning_active.eq(1),
                    self.tune_valid.eq(0),
                ]
 
                with m.If(~self.tune_enable):
                    m.next = "IDLE"
 
                with m.Elif(self.error_valid):
                    # accumulate |error| for amplitude estimate (saturating)
                    m.d.sync += peak_acc.eq(peak_acc_sat)
                    m.d.sync += [
                        peak_count.eq(peak_count + 1),
                        half_period_counter.eq(half_period_counter + 1),
                        error_prev.eq(self.error_in),
                    ]
 
                    # zero crossing from + to - signals end of positive half-period
                    with m.If(zero_cross & self.error_in[-1]):
                        m.d.sync += [
                            half_period_sum.eq(
                                half_period_sum + half_period_counter),
                            half_period_count.eq(half_period_count + 1),
                            half_period_counter.eq(0),
                        ]
                        with m.If((half_period_count + 1) >= self.min_half_periods):
                            m.next = "COMPUTE"
                        with m.Else():
                            m.next = "RELAY_N"
 
            with m.State("RELAY_N"):
                # Negative relay phase: inject -relay_amp, watch for pos zero-cross
                m.d.sync += [
                    relay_reg.eq(-self.relay_amp),
                    self.hold_request.eq(1),
                    self.tuning_active.eq(1),
                    self.tune_valid.eq(0),
                ]
 
                with m.If(~self.tune_enable):
                    m.next = "IDLE"
 
                # AUDIT FIX (S2-11.1): the zero-crossing block and the
                # state transitions below used to be de-indented out of
                # this `Elif`, at the top level of the state. Two
                # consequences:
                #   * the accumulate ran on cycles where error_valid was
                #     low, and
                #   * `m.next` was assigned UNCONDITIONALLY, so RELAY_N
                #     lasted exactly one clock cycle and always left for
                #     RELAY_P or COMPUTE. The relay never produced a
                #     square wave; it produced a near-DC positive output
                #     with single-cycle negative blips, and the whole
                #     Tu/Ku measurement was meaningless.
                # The structure now mirrors RELAY_P exactly.
                with m.Elif(self.error_valid):
                    m.d.sync += peak_acc.eq(peak_acc_sat)
                    m.d.sync += [
                        peak_count.eq(peak_count + 1),
                        half_period_counter.eq(half_period_counter + 1),
                        error_prev.eq(self.error_in),
                    ]

                    # zero crossing from - to + signals end of negative half-period
                    with m.If(zero_cross & ~self.error_in[-1]):
                        m.d.sync += [
                            half_period_sum.eq(half_period_sum + half_period_counter),
                            half_period_count.eq(half_period_count + 1),
                            half_period_counter.eq(0),
                        ]
                        with m.If((half_period_count + 1) >= self.min_half_periods):
                            m.next = "COMPUTE"
                        with m.Else():
                            m.next = "RELAY_P"
 
            with m.State("COMPUTE"):
                # One registered cycle: compute new Ku, apply EMA to kp/ki
                # Divide-by-zero guard: only update if a_est > 0 and tu_est > 0
                with m.If((a_est > 0) & (tu_est > 0)):
                    m.d.sync += [
                        ku_reg.eq(ku_final),
                        tu_reg.eq(tu_est),
                    ]
                    # EMA on kp
                    kp_delta = Signal(signed(self.gain_w + 1))
                    m.d.comb += kp_delta.eq(kp_new - kp_reg)
                    m.d.sync += kp_reg.eq(kp_reg + (kp_delta >> self.ema_shift))
 
                    # EMA on ki
                    ki_delta = Signal(signed(self.gain_w + 1))
                    m.d.comb += ki_delta.eq(ki_new - ki_reg)
                    m.d.sync += ki_reg.eq(ki_reg + (ki_delta >> self.ema_shift))
 
                    m.d.sync += self.tune_valid.eq(1)
 
                # Reset measurement accumulators for next cycle
                m.d.sync += [
                    half_period_sum.eq(0),
                    half_period_count.eq(0),
                    half_period_counter.eq(0),
                    peak_acc.eq(0),
                    peak_count.eq(0),
                    self.hold_request.eq(0),
                    self.tuning_active.eq(0),
                ]
 
                # Continue tuning immediately if still enabled
                with m.If(self.tune_enable):
                    with m.If(relay_reg[-1]):      # relay_reg was negative -> we just left RELAY_N
                        m.next = "RELAY_P"
                    with m.Else():                  # relay_reg was positive -> we just left RELAY_P
                        m.next = "RELAY_N"
                with m.Else():
                    m.next = "IDLE"
 
        return m
 
 
# ---------------------------------------------------------------------------
# PIWithAutoTune — top-level wrapper connecting PICore + RelayTuner
# ---------------------------------------------------------------------------
 
class PIWithAutoTune(Elaboratable):
    """
    Drop-in replacement for PICore that internally integrates RelayTuner.
 
    The external interface preserves all original PICore ports (kp/ki are now
    outputs for readback, not inputs — gains are set by the tuner).
 
    Additional ports vs original PICore
    ------------------------------------
    tune_enable  : input, 1 bit  — enable continuous background relay tuning
    relay_out    : output, relay_w bits  — route to DAC_SLOW (Category B output)
    tuning_active: output, 1 bit  — high during relay measurement phase
    tune_valid   : output, 1 bit  — pulses when gains updated
    kp_readback  : output, gain_w bits  — current Kp in Q3.14 (diagnostic)
    ki_readback  : output, gain_w bits  — current Ki in Q3.14 (diagnostic)
    ku_readback  : output, gain_w bits  — latest Ku estimate (diagnostic)
    tu_readback  : output, 32 bits      — latest Tu in samples (diagnostic)
 
    Parameters
    ----------
    See PICore and RelayTuner for individual parameters.
    relay_w      : int  relay/DAC_SLOW output width (default 16)
    relay_amp    : int  relay amplitude in DAC codes (default 512)
    """
 
    def __init__(self,
                 err_w=20, out_w=16, gain_w=18, gain_frac=14, acc_w=40,
                 relay_w=16, relay_amp=512,
                 min_half_periods=6, ema_shift=3,
                 kp_init=4096, ki_init=128):
 
        self.err_w     = err_w
        self.out_w     = out_w
        self.gain_w    = gain_w
        self.gain_frac = gain_frac
        self.acc_w     = acc_w
 
        # --- original PICore input ports (pass-through) ---
        self.error_in         = Signal(signed(err_w))
        self.error_valid      = Signal()
        self.lock_enable      = Signal()
        # hold_enable from outside is OR'd with tuner's hold_request internally
        self.hold_enable      = Signal()
        self.integrator_reset = Signal()
        self.integrator_load  = Signal()
        self.load_value       = Signal(signed(acc_w))
        self.out_min          = Signal(signed(out_w))
        self.out_max          = Signal(signed(out_w))
        self.out_safe         = Signal(signed(out_w))
 
        # --- original PICore output ports ---
        self.control_out   = Signal(signed(out_w))
        self.control_valid = Signal()
        self.sat_hi        = Signal()
        self.sat_lo        = Signal()
 
        # --- auto-tune specific ports ---
        self.tune_enable   = Signal()
        self.relay_out     = Signal(signed(relay_w))
        self.tuning_active = Signal()
        self.tune_valid    = Signal()
        self.kp_readback   = Signal(signed(gain_w))
        self.ki_readback   = Signal(signed(gain_w))
        self.ku_readback   = Signal(signed(gain_w))
        self.tu_readback   = Signal(32)
 
        # store constructor args for submodule instantiation
        self._relay_w          = relay_w
        self._relay_amp        = relay_amp
        self._min_half_periods = min_half_periods
        self._ema_shift        = ema_shift
        self._kp_init          = kp_init
        self._ki_init          = ki_init
 
    def elaborate(self, platform):
        m = Module()
 
        m.submodules.pi    = pi    = PICore(
            err_w=self.err_w, out_w=self.out_w,
            gain_w=self.gain_w, gain_frac=self.gain_frac,
            acc_w=self.acc_w)
 
        m.submodules.tuner = tuner = RelayTuner(
            err_w=self.err_w,
            gain_w=self.gain_w,
            gain_frac=self.gain_frac,
            relay_w=self._relay_w,
            relay_amp=self._relay_amp,
            min_half_periods=self._min_half_periods,
            ema_shift=self._ema_shift,
            kp_init=self._kp_init,
            ki_init=self._ki_init)
 
        # --- wire tuner -> PICore gains ---
        m.d.comb += [
            pi.kp.eq(tuner.kp_out),
            pi.ki.eq(tuner.ki_out),
        ]
 
        # AUDIT FIX (S2-11.4): this used to be
        #     pi.hold_enable.eq(self.hold_enable | tuner.hold_request)
        # which froze the PICore OUTPUT for the entire relay measurement.
        # With tune_enable held high the tuner loops through the relay
        # states continuously, so the servo output was frozen essentially
        # permanently while background tuning ran. That contradicts both
        # the class docstring ("the tuner asserts hold_request so the
        # PICore freezes its integrator") and the claim that the fast
        # feedback path is not disturbed. Freezing the output IS
        # disturbing it.
        #
        # The tuner's request now drives integrator_hold, which is what
        # the docstring describes: the relay perturbation cannot corrupt
        # the integral state, but the proportional path keeps servoing.
        # Only an explicit external hold still freezes the output.
        m.d.comb += [
            pi.hold_enable.eq(self.hold_enable),
            pi.integrator_hold.eq(tuner.hold_request),
        ]
 
        # --- wire error to both ---
        m.d.comb += [
            pi.error_in.eq(self.error_in),
            pi.error_valid.eq(self.error_valid),
            tuner.error_in.eq(self.error_in),
            tuner.error_valid.eq(self.error_valid),
        ]
 
        # --- pass remaining PICore control signals through ---
        m.d.comb += [
            pi.lock_enable.eq(self.lock_enable),
            pi.integrator_reset.eq(self.integrator_reset),
            pi.integrator_load.eq(self.integrator_load),
            pi.load_value.eq(self.load_value),
            pi.out_min.eq(self.out_min),
            pi.out_max.eq(self.out_max),
            pi.out_safe.eq(self.out_safe),
        ]
 
        # --- expose PICore outputs ---
        m.d.comb += [
            self.control_out.eq(pi.control_out),
            self.control_valid.eq(pi.control_valid),
            self.sat_hi.eq(pi.sat_hi),
            self.sat_lo.eq(pi.sat_lo),
        ]
 
        # --- tuner control and diagnostics ---
        m.d.comb += [
            tuner.tune_enable.eq(self.tune_enable),
            self.relay_out.eq(tuner.relay_out),
            self.tuning_active.eq(tuner.tuning_active),
            self.tune_valid.eq(tuner.tune_valid),
            self.kp_readback.eq(tuner.kp_out),
            self.ki_readback.eq(tuner.ki_out),
            self.ku_readback.eq(tuner.ku_out),
            self.tu_readback.eq(tuner.tu_out),
        ]
 
        return m
