# pdh_frontend.py
# Top-level PDH integration module for the POSM FPGA MTS lock core
#
# Instantiates and wires together the PDH modulation and demodulation path:
#   1. NCO (generates sin/cos)
#   2. Demodulator (mixes ADC × NCO)
#   3. Low-pass filter (removes sum-frequency)
#   4. Mode mux (selects PDH vs direct ADC for error_calc)
#
# Inputs:
#   - ADC sample (signed 17b, from adc_frontend.o_ch0)
#   - Registers: freq_word, mod_amp, demod_phase, lpf_alpha, pdh_enable
#
# Outputs:
#   - error_sample (signed 17b) to error_calc
#   - error_valid
#   - mod_out (signed 16b) to new DAC fast formatter / dedicated EOM DAC

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth import *
from rtl.dsp.nco import NCO
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
    
    error_sample : signed output, adc_w bits (fed to error_calc)
    error_valid  : output, 1 bit
    mod_out      : signed output, mod_w bits (modulation waveform for DAC)
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

        # --- output ports ---
        # The output back to error_calc needs to match its expected input width (adc_w)
        self.error_sample = Signal(signed(adc_w))
        self.error_valid = Signal()
        
        self.mod_out = Signal(signed(mod_w))

    def elaborate(self, platform):
        m = Module()

        # -------------------------------------------------------------
        # Submodules
        # -------------------------------------------------------------
        
        m.submodules.nco = nco = NCO(phase_w=32, lut_depth=256, out_w=16)
        m.submodules.demod = demod = Demodulator(adc_w=self.adc_w, ref_w=16, out_w=self.err_w, shift=13)
        m.submodules.lpf = lpf = DemodLowpass(in_w=self.err_w, out_w=self.err_w, acc_w=40, max_alpha=20)
        
        # We need a saturator to fit the 20-bit LPF output back into the 17-bit error_sample
        m.submodules.sat_err = sat_err = SatMath(self.err_w, self.adc_w)

        # -------------------------------------------------------------
        # Wiring
        # -------------------------------------------------------------
        
        # NCO
        m.d.comb += [
            nco.freq_word.eq(self.freq_word),
            # In our architecture, the NCO generates the reference FOR THE MIXER.
            # The phase offset is applied to the NCO output, aligning it with the ADC signal.
            nco.phase_offset.eq(self.demod_phase),
        ]

        # Modulation Output Scaling
        # nco.o_sin is 16-bit signed, amplitude ±32767.
        # mod_amp is 16-bit unsigned, treated as Q2.14.
        # Product is 32-bit signed. Right shift by 14.
        mod_product = Signal(signed(32))
        m.d.comb += mod_product.eq(nco.o_sin * self.mod_amp)
        
        # Pipeline the modulation output (1 cycle latency)
        m.d.sync += self.mod_out.eq((mod_product >> 14)[:self.mod_w].as_signed())

        # Demodulator
        m.d.comb += [
            demod.adc_in.eq(self.adc_sample),
            demod.adc_valid.eq(self.adc_valid),
            demod.ref_sin.eq(nco.o_sin),
            demod.ref_cos.eq(nco.o_cos),
        ]

        # LPF
        # Use in-phase (I) channel for the error signal
        m.d.comb += [
            lpf.sample_in.eq(demod.i_out),
            lpf.sample_valid.eq(demod.out_valid),
            lpf.alpha_shift.eq(self.lpf_alpha),
            lpf.reset_filter.eq(self.reset_filter),
        ]

        # Saturate LPF output (err_w = 20) back to error_calc input (adc_w = 17)
        # Note: The LPF output has already been truncated from 40 to 20 bits.
        # Depending on the overall system gain, this signal might need scaling,
        # but the right-shift in the Demodulator handles the bulk of it.
        # We clamp it here just to be safe.
        m.d.comb += sat_err.value_in.eq(lpf.sample_out)

        # Mode Mux
        # Choose between the direct ADC sample and the PDH demodulated error.
        # The direct path must be delayed to match the latency of the PDH path
        # if synchronous downstream logic cares, but error_calc just waits for valid.
        
        # PDH path latency from adc_valid:
        #   Demodulator: 1 cycle
        #   LPF:         1 cycle
        # Total latency: 2 cycles.
        
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
                self.error_sample.eq(sat_err.value_out),
                self.error_valid.eq(lpf.out_valid),
            ]
        with m.Else():
            m.d.comb += [
                self.error_sample.eq(adc_sample_d2),
                self.error_valid.eq(adc_valid_d2),
            ]

        return m
