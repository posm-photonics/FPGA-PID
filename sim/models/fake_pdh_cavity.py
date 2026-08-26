# fake_pdh_cavity.py
# Simulates a Fabry-Perot cavity reflection signal for PDH locking.
#
# Takes the laser detuning and the phase modulation waveform (from the FPGA),
# and computes the instantaneous reflected power. This is a simplified
# quasi-static model that produces the characteristic PDH error signal when
# demodulated.

import math
from typing import Optional, Sequence

try:
    import numpy as np
except ImportError:
    np = None


class FakePDHCavity:
    """Fake PDH cavity model for simulation.
    
    Generates an AM signal representing the photodiode voltage when the laser
    is phase-modulated and reflected from a Fabry-Perot cavity.
    """

    def __init__(
        self,
        linewidth: float = 1.0,
        modulation_freq: float = 10.0,
        modulation_index: float = 1.0,
        amplitude: float = 1000.0,
        noise_std: float = 0.0,
    ):
        self.linewidth = float(linewidth)
        self.modulation_freq = float(modulation_freq)
        self.modulation_index = float(modulation_index)
        self.amplitude = float(amplitude)
        self.noise_std = float(noise_std)
        self._rng = np.random.default_rng(42) if np is not None else None

    def _cavity_reflection(self, detuning: float) -> complex:
        """Complex reflection coefficient of the cavity.
        F(w) = -1 + (2 * gamma) / (gamma + i * detuning)
        where gamma is the cavity half-linewidth.
        """
        gamma = self.linewidth / 2.0
        return -1.0 + (2.0 * gamma) / (gamma + 1j * detuning)

    def sample(self, detuning: float, mod_voltage: float, time_s: float) -> float:
        """Compute the instantaneous photodiode signal.
        
        This model approximates the PDH reflection by calculating the carrier
        and first-order sidebands, then interfering them.
        
        Args:
            detuning: Laser frequency detuning from resonance.
            mod_voltage: Instantaneous modulation voltage from the FPGA DAC
                         (normalized to approx ±1.0 range based on amplitude).
            time_s: Current simulation time in seconds.
        """
        # PDH theory:
        # Reflected power P_ref ~ P0 * |F(w)*J0 + F(w+wm)*J1*e^(iwm*t) - F(w-wm)*J1*e^(-iwm*t)|^2
        # For a simplified model, we can just generate the ideal PDH error curve
        # and multiply it by the local oscillator (the modulation waveform) to
        # simulate the analog mixer in reverse, giving the ADC signal.
        
        # Ideal PDH error signal (demodulated):
        # e = -2 * J0 * J1 * Im{ F(w) * F*(w+wm) - F*(w) * F(w-wm) }
        
        gamma = self.linewidth / 2.0
        
        # Ideal dispersive error curve (simplified approximation of the central feature)
        # We'll use a derivative of a Lorentzian to match the PDH shape near resonance.
        normalized_detuning = detuning / gamma
        error_ideal = -normalized_detuning / (1.0 + normalized_detuning**2)**2
        
        # Scale to amplitude
        error_scaled = error_ideal * self.amplitude
        
        # In a real system, the ADC sees the error signal mixed UP with the modulation
        # frequency, plus some DC offset.
        # We simulate the photodiode output by taking the ideal error signal and
        # multiplying it by the modulation voltage (which acts as the carrier).
        
        # The modulation voltage from the FPGA is expected to be a sine wave
        # representing the phase modulation. The AM signal on the photodiode
        # is proportional to the error signal mixed with the modulation sine wave.
        
        # Normalize mod_voltage to a ±1 range (assuming 16-bit DAC output ±32767)
        mod_norm = mod_voltage / 32768.0
        
        signal = error_scaled * mod_norm
        
        if self._rng is not None and self.noise_std > 0:
            signal += self._rng.normal(0, self.noise_std)
            
        return float(signal)
