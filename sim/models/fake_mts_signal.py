from __future__ import annotations

import math
from typing import Optional, Sequence, Union

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised in minimal environments
    np = None

from .simulation_config import SpectroscopyConfig
from .plotting import save_line_plot


class FakeMTSSignal:
    """Model the analog-demodulated MTS spectroscopy signal seen by the ADC."""

    def __init__(self, config: Optional[Union[SpectroscopyConfig, dict]] = None, *, center: float = 0.0,
                 width: float = 1.0, amplitude: float = 1.0, offset: float = 0.0,
                 noise_std: float = 0.0, drift_rate: float = 0.0, slope_sign: float = 1.0,
                 seed: int = 7) -> None:
        if isinstance(config, SpectroscopyConfig):
            center = config.center
            width = config.width
            amplitude = config.amplitude
            offset = config.offset
            noise_std = config.noise_std
            drift_rate = config.drift_rate
            slope_sign = config.slope_sign
            seed = config.seed
        elif isinstance(config, dict):
            center = config.get("center", center)
            width = config.get("width", width)
            amplitude = config.get("amplitude", amplitude)
            offset = config.get("offset", offset)
            noise_std = config.get("noise_std", noise_std)
            drift_rate = config.get("drift_rate", drift_rate)
            slope_sign = config.get("slope_sign", slope_sign)
            seed = config.get("seed", seed)

        self.center = float(center)
        self.width = max(float(width), 1e-6)
        self.amplitude = float(amplitude)
        self.offset = float(offset)
        self.noise_std = float(noise_std)
        self.drift_rate = float(drift_rate)
        self.slope_sign = float(slope_sign)
        self.seed = int(seed)

        self._time = 0.0
        self._drift = 0.0
        if np is not None:
            self._rng = np.random.default_rng(self.seed)
        else:
            self._rng = None

    def ideal(self, detuning: float) -> float:
        """Return the ideal, noise-free dispersive MTS error signal."""

        normalized = (float(detuning) - self.center) / self.width
        shape = normalized * math.exp(-0.5 * normalized * normalized)
        return self.slope_sign * self.amplitude * shape + self.offset + self._drift

    def sample(self, detuning: float) -> float:
        """Return one noisy analog error sample for the given detuning."""

        self._time += 1.0
        self._drift += self.drift_rate
        ideal_value = self.ideal(detuning)
        if self._rng is not None:
            noise = float(self._rng.normal(0.0, self.noise_std))
        else:
            noise = 0.0
        return float(ideal_value + noise)

    def curve(self, detuning_values: Sequence[float]) -> list[float]:
        """Evaluate the ideal curve over a sequence of detunings."""

        return [self.ideal(float(value)) for value in detuning_values]

    def plot_ideal_curve(self, detuning_values: Sequence[float], title: str = "Ideal MTS curve",
                         output_path: Optional[str] = None) -> None:
        """Save a plot of the ideal MTS curve."""

        values = self.curve(detuning_values)
        save_line_plot(detuning_values, values, title=title, xlabel="Detuning", ylabel="Ideal error",
                       legend=["ideal"], output_path=output_path)
