# pdh_frontend.py
# Top-level PDH integration module for the POSM FPGA MTS lock core
#
# Instantiates and wires together the PDH modulation and demodulation path:
#   1. NCO           (phase accumulator + mixer reference at demod_phase)
#   2. SineLUT       (modulation waveform at phase 0, same accumulator)
#   3. Demodulator   (mixes ADC x reference, produces I and Q)
#   4. DemodLowpass  (removes the sum-frequency term)
#   5. Mode mux      (selects PDH error vs direct ADC for error_calc)
#
# Inputs:
#   - ADC sample (signed adc_w, from adc_frontend.o_ch0)
#   - Registers: freq_word, mod_amp, demod_phase, lpf_alpha, pdh_enable
#
# Outputs:
#   - error_sample (signed err_w) to error_calc
#   - error_valid
#   - mod_out (signed mod_w) to the EOM modulation DAC
#   - i_out / q_out (signed err_w) demodulator diagnostics
#
# ===========================================================================
# ARCHITECTURAL NOTE -- READ BEFORE SHIPPING (audit finding S1-9)
# ===========================================================================
# POSM_project_FPGALock.pdf section 7.2 lists "Digital demodulation for
# the required v1 lock" under "Not allowed in the fast path", and
# section 2 freezes the primary error signal as the ANALOG-demodulated
# MTS error on ADC_CH0. Section 3.3 gives the reason: the required
# feedback bandwidth is at least 1 MHz, and pure delay costs phase
# margin directly.
#
# This block sits between the ADC front end and error_calc, and it adds
# two clock cycles of latency to the required fast path even when
# pdh_enable is 0 (the direct path is delayed to keep the mux
# latency-neutral, so that switching modes cannot change the loop
# delay). At 125 MHz that is 16 ns, about 6 degrees of phase margin at
# 1 MHz.
#
# The defects below are fixed, but whether this block belongs in the v1
# fast path at all is a scope decision for the team, not something an
# audit can settle. Either remove it from the fast path or amend the
# packet. It is left in place here so that behaviour is unchanged apart
# from the bug fixes.
# ===========================================================================

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth import *
from rtl.dsp.nco import NCO, SineLUT
from rtl.dsp.demodulator import Demodulator
from rtl.dsp.demod_lowpass import DemodLowpass
from rtl.common.sat_math import SatMath


class PDHFrontend(Elaboratable):
    """Top-level PDH modulation and demodulation frontend.

    Parameters
    ----------
    adc_w : int
        ADC sample width (default 17, matching ADCFrontendTop.o_ch0).
    err_w : int
        Internal error processing width (default 20, matching error_calc).
    mod_w : int
        Modulation DAC output width (default 16).

    Ports
    -----
    adc_sample   : signed input, adc_w bits
    adc_valid    : input, 1 bit
    freq_word    : unsigned input, 32 bits (NCO frequency)
    mod_amp      : unsigned input, 16 bits (modulation amplitude, Q2.14)
    demod_phase  : unsigned input, 32 bits (mixer phase offset)
    lpf_alpha    : unsigned input, 5 bits (LPF bandwidth)
    pdh_enable   : input, 1 bit (1=PDH mode, 0=Direct ADC mode)
    reset_filter : input, 1 bit (resets the LPF)
    phase_reset  : input, 1 bit (resets the NCO phase accumulator)

    error_sample : signed output, err_w bits (fed to error_calc)
    error_valid  : output, 1 bit
    mod_out      : signed output, mod_w bits (modulation waveform for DAC)
    i_out        : signed output, err_w bits (in-phase, diagnostic)
    q_out        : signed output, err_w bits (quadrature, diagnostic)

    Latency (adc_valid -> error_valid): 2 clock cycles in both modes.
    """

    def __init__(self, adc_w=17, err_w=20, mod_w=16):
        self.adc_w = adc_w
        self.err_w = err_w
        self.mod_w = mod_w

        # --- input ports ---
        self.adc_sample = Signal(signed(adc_w))
        self.adc_valid = Signal()

        self.freq_word = Signal(32)
        self.mod_amp = Signal(16)
        self.demod_phase = Signal(32)
        self.lpf_alpha = Signal(5)
        self.pdh_enable = Signal()
        self.reset_filter = Signal()
        self.phase_reset = Signal()

        # --- output ports ---
        # AUDIT FIX (S2-10): this used to be signed(adc_w) = 17 bits, with
        # a SatMath narrowing the 20-bit LPF result down to 17. Two
        # problems followed:
        #   * three bits of resolution were thrown away and then
        #     immediately re-widened by error_calc, and
        #   * the 17-bit saturation limit (+/-65536) is wider than the
        #     signed(16) port it was connected to in lock_core_top, so
        #     PDH error values between 32768 and 65535 truncated and
        #     WRAPPED SIGN. An inverted error turns the servo into
        #     positive feedback (packet section 4.4).
        # The error path is now err_w end to end, with no narrowing.
        self.error_sample = Signal(signed(err_w))
        self.error_valid = Signal()

        self.mod_out = Signal(signed(mod_w))

        # Quadrature channel exposed for demodulation-phase optimisation.
        # It was previously computed and discarded. Linien routes both I
        # and Q into its filter chain for exactly this reason, and packet
        # section 5.1 describes scoring slope/amplitude/symmetry to pick
        # the LO phase.
        self.i_out = Signal(signed(err_w))
        self.q_out = Signal(signed(err_w))

    def elaborate(self, platform):
        m = Module()

        # -------------------------------------------------------------
        # Submodules
        # -------------------------------------------------------------
        m.submodules.nco = nco = NCO(phase_w=32, lut_depth=256, out_w=16)
        m.submodules.mod_lut = mod_lut = SineLUT(
            phase_w=32, lut_depth=256, out_w=16)
        m.submodules.demod = demod = Demodulator(
            adc_w=self.adc_w, ref_w=16, out_w=self.err_w, shift=13)
        m.submodules.lpf = lpf = DemodLowpass(
            in_w=self.err_w, out_w=self.err_w, acc_w=40, max_alpha=20)

        # -------------------------------------------------------------
        # Oscillator
        #
        # AUDIT FIX (S1-8): the modulation output and the mixer reference
        # must come from the SAME accumulator at INDEPENDENT phases.
        # Previously both were taken from nco.o_sin, which already had
        # demod_phase applied, so their relative phase was fixed at zero
        # and PDH_DEMOD_PHASE did nothing at all.
        #
        #   nco     -> mixer reference at (phase + demod_phase)
        #   mod_lut -> modulation waveform at (phase + 0)
        #
        # Both LUTs see the same cycle-N accumulator value and register
        # their result on cycle N+1, so they are sample-aligned.
        # -------------------------------------------------------------
        m.d.comb += [
            nco.freq_word.eq(self.freq_word),
            nco.phase_offset.eq(self.demod_phase),
            nco.phase_reset.eq(self.phase_reset),
            mod_lut.phase.eq(nco.o_phase),
        ]

        # -------------------------------------------------------------
        # Modulation output scaling
        #
        # mod_lut.o_sin is 16-bit signed, amplitude +/-32766.
        # mod_amp is 16-bit unsigned, interpreted as Q2.14.
        # Product is 32-bit signed; >>14 leaves a range of +/-131071,
        # which needs 18 bits.
        #
        # AUDIT FIX: the old code did (mod_product >> 14)[:mod_w], a bare
        # truncation. Any mod_amp above 16384 (Q2.14 > 1.0) overflowed
        # 16 bits and WRAPPED, mangling the EOM drive waveform with sign
        # flips. mod_amp is a 16-bit register documented as Q2.14, so
        # values up to ~4.0 are reachable by design. Saturate instead.
        # -------------------------------------------------------------
        mod_product = Signal(signed(32))
        mod_scaled = Signal(signed(32))
        m.d.comb += [
            mod_product.eq(mod_lut.o_sin * self.mod_amp),
            mod_scaled.eq(mod_product >> 14),
        ]

        m.submodules.sat_mod = sat_mod = SatMath(32, self.mod_w)
        m.d.comb += sat_mod.value_in.eq(mod_scaled)

        # Pipeline the modulation output (1 cycle).
        m.d.sync += self.mod_out.eq(sat_mod.value_out)

        # -------------------------------------------------------------
        # Mixer reference alignment
        #
        # mod_out is registered one cycle after mod_lut.o_sin. Delay the
        # mixer reference by the same one cycle so that, inside the FPGA,
        # the reference corresponds to the modulation sample actually
        # leaving the device. Without this the reference led the emitted
        # modulation by one clock: 8 ns at 125 MHz, which is 28.8 degrees
        # at a 10 MHz modulation frequency, and it was not correctable
        # because demod_phase was inert.
        #
        # demod_phase is now left to compensate only the physical round
        # trip through the EOM, cavity, photodiode and ADC.
        #
        # This costs nothing on the ADC -> error path: it is a delay on
        # the reference input to the mixer, not a pipeline stage in the
        # signal path.
        # -------------------------------------------------------------
        ref_sin_d = Signal(signed(16))
        ref_cos_d = Signal(signed(16))
        m.d.sync += [
            ref_sin_d.eq(nco.o_sin),
            ref_cos_d.eq(nco.o_cos),
        ]

        # -------------------------------------------------------------
        # Demodulator
        # -------------------------------------------------------------
        m.d.comb += [
            demod.adc_in.eq(self.adc_sample),
            demod.adc_valid.eq(self.adc_valid),
            demod.ref_sin.eq(ref_sin_d),
            demod.ref_cos.eq(ref_cos_d),
            self.i_out.eq(demod.i_out),
            self.q_out.eq(demod.q_out),
        ]

        # -------------------------------------------------------------
        # Low-pass filter
        # Use the in-phase (I) channel for the error signal.
        # -------------------------------------------------------------
        m.d.comb += [
            lpf.sample_in.eq(demod.i_out),
            lpf.sample_valid.eq(demod.out_valid),
            lpf.alpha_shift.eq(self.lpf_alpha),
            lpf.reset_filter.eq(self.reset_filter),
        ]

        # -------------------------------------------------------------
        # Mode mux
        #
        # The direct ADC path is delayed to match the PDH path so that
        # toggling pdh_enable cannot change the fast-loop latency.
        #
        # PDH path latency from adc_valid:
        #   Demodulator: 1 cycle
        #   LPF:         1 cycle
        #   Total:       2 cycles
        # -------------------------------------------------------------
        adc_sample_d1 = Signal(signed(self.adc_w))
        adc_valid_d1 = Signal()
        adc_sample_d2 = Signal(signed(self.adc_w))
        adc_valid_d2 = Signal()

        m.d.sync += [
            adc_sample_d1.eq(self.adc_sample),
            adc_valid_d1.eq(self.adc_valid),
            adc_sample_d2.eq(adc_sample_d1),
            adc_valid_d2.eq(adc_valid_d1),
        ]

        with m.If(self.pdh_enable):
            m.d.comb += [
                # Both are err_w wide now: no narrowing, no wrap.
                self.error_sample.eq(lpf.sample_out),
                self.error_valid.eq(lpf.out_valid),
            ]
        with m.Else():
            m.d.comb += [
                # adc_w -> err_w is a widening assignment: Amaranth
                # sign-extends, so this is lossless.
                self.error_sample.eq(adc_sample_d2),
                self.error_valid.eq(adc_valid_d2),
            ]

        return m
