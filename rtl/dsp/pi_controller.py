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
# PICore latency: 2 clock cycles (unchanged)
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
        self.hold_enable      = Signal()
        self.integrator_reset = Signal()
        self.integrator_load  = Signal()
        self.load_value       = Signal(signed(acc_w))
        self.out_min          = Signal(signed(out_w))
        self.out_max          = Signal(signed(out_w))
        self.out_safe         = Signal(signed(out_w))
 
        # --- output ports ---
        self.control_out   = Signal(signed(out_w))
        self.control_valid = Signal()
        self.sat_hi        = Signal()
        self.sat_lo        = Signal()
 
    def elaborate(self, platform):
        m = Module()
 
        integrator = Signal(signed(self.acc_w))
 
        mul_w = self.err_w + self.gain_w  # 38 bits
 
        p_term    = Signal(signed(mul_w))
        i_term    = Signal(signed(mul_w))
        p_scaled  = Signal(signed(self.acc_w))
        i_scaled  = Signal(signed(self.acc_w))
        candidate = Signal(signed(self.acc_w))
        int_next  = Signal(signed(self.acc_w))
 
        sat_hi_comb     = Signal()
        sat_lo_comb     = Signal()
        windup_suppress = Signal()
 
        out_max_ext = Signal(signed(self.acc_w))
        out_min_ext = Signal(signed(self.acc_w))
 
        m.d.comb += [
            out_max_ext.eq(self.out_max),
            out_min_ext.eq(self.out_min),

            # ( Kp * e[n] )
            p_term.eq(self.error_in * self.kp),

            # ( Ki * e[n] )
            i_term.eq(self.error_in * self.ki),

            # scale it back to recover the proprer bits
            p_scaled.eq(p_term >> self.gain_frac),
            i_scaled.eq(i_term >> self.gain_frac),

            # I[n+1] = I[n] + ( Ki * e[n] )
            int_next.eq(integrator + i_scaled),

            # u[n] = ( Kp * e[n] ) + I[n]
            candidate.eq(p_scaled + integrator),
 
            sat_hi_comb.eq(candidate > out_max_ext),
            sat_lo_comb.eq(candidate < out_min_ext),

            # windup_suppress = Should I stop integrating right now?
            # Only blocks it if it wants to increase in the same direction
            # it is at its limit. If it is going in the opposite direction
            # let it be.
            windup_suppress.eq(
                (sat_hi_comb & (i_scaled > 0)) |
                (sat_lo_comb & (i_scaled < 0))
            ),
        ]
 
        with m.If(self.integrator_reset): # useful during fault, relock, startup
            m.d.sync += integrator.eq(0)
 
        with m.Elif(self.integrator_load):
            m.d.sync += integrator.eq(self.load_value)
 
        with m.Elif(self.error_valid & self.lock_enable & ~self.hold_enable):
            with m.If(~windup_suppress): # Only integrate if windup_suppress = 0
                m.d.sync += integrator.eq(int_next)
 
        m.d.sync += self.control_valid.eq(0)
 
        with m.If(self.error_valid & self.lock_enable):
 
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
                self.control_valid.eq(self.error_valid),
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
 
        # peak shift: accumulate over 2^PEAK_SHIFT samples per half-period
        PEAK_SHIFT = 8   # 256 samples per half-period measurement window
 
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
 
        # a_est = peak_acc >> PEAK_SHIFT  (average |error|, Q0 integer codes)
        a_est = Signal(32)
        m.d.comb += a_est.eq(peak_acc >> PEAK_SHIFT)
 
        # Ku numerator = 4 * relay_amp << gain_frac  (scaled by 2^14)
        ku_num = Signal(64)
        m.d.comb += ku_num.eq((relay_amp_sig << (gain_frac + 2)))
 
        # Ku_q = ku_num / (pi_approx/4 * a_est)
        # We approximate: Ku_q = ku_num * 4 / (pi * a_est)
        #   using pi ~ 355/113 -> 4/pi ~ 452/355 ~ 14366 / (2^13 * pi_approx)
        # Simplified fixed-point approach:
        #   Ku_q = (ku_num * 10430) >> (gain_frac + 15)
        #   where 10430/32768 ≈ 4/pi ≈ 1.2732
        ku_scaled = Signal(64)
        m.d.comb += ku_scaled.eq(
            (ku_num * 10430) >> (gain_frac + 15)
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
 
        ku_final = Signal(signed(self.gain_w))
        m.d.comb += ku_final.eq(ku_scaled >> a_est_log2)
 
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
        kp_new = Signal(signed(self.gain_w))
        m.d.comb += kp_new.eq((ku_final * 7373) >> gain_frac)
 
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
        ki_new  = Signal(signed(self.gain_w))
        m.d.comb += ki_wide.eq((ku_final * 8847) >> tu_log2)
        m.d.comb += ki_new.eq(ki_wide >> gain_frac)
 
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
                    # accumulate |error| for amplitude estimate
                    with m.If(self.error_in[-1]):   # negative (MSB=1 in 2's comp)
                        m.d.sync += peak_acc.eq(peak_acc + (-self.error_in))
                    with m.Else():
                        m.d.sync += peak_acc.eq(peak_acc + self.error_in)
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
 
                with m.Elif(self.error_valid):
                    with m.If(self.error_in[-1]):
                        m.d.sync += peak_acc.eq(peak_acc + (-self.error_in))
                    with m.Else():
                        m.d.sync += peak_acc.eq(peak_acc + self.error_in)
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
                with m.If((half_period_count + 1) >= self.min_half_periods):  # <-- add +1
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
 
        # --- wire tuner -> PICore hold (OR with external hold) ---
        m.d.comb += pi.hold_enable.eq(self.hold_enable | tuner.hold_request)
 
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
