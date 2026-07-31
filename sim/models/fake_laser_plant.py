from __future__ import annotations

import math
from typing import Optional, Sequence, Union

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised in minimal environments
    np = None

from .simulation_config import LaserConfig


class FakeLaserPlant:
    """First-order laser plant model driven by the fast and slow DAC paths."""

    def __init__(self, config: Optional[Union[LaserConfig, dict]] = None, *, fast_gain: float = 0.35,
                 slow_gain: float = 0.06, tau: float = 0.04, noise_std: float = 0.08,
                 drift_rate: float = 0.002, fast_min: float = -5000.0, fast_max: float = 5000.0,
                 slow_min: float = -2000.0, slow_max: float = 2000.0, timestep: float = 1e-3,
                 initial_detuning: float = 0.0, initial_fast_state: float = 0.0,
                 initial_slow_state: float = 0.0) -> None:
        if isinstance(config, LaserConfig):
            fast_gain = config.fast_gain
            slow_gain = config.slow_gain
            tau = config.tau
            noise_std = config.noise_std
            drift_rate = config.drift_rate
            fast_min = config.fast_min
            fast_max = config.fast_max
            slow_min = config.slow_min
            slow_max = config.slow_max
            timestep = 1e-3
            initial_detuning = config.initial_detuning
            initial_fast_state = config.initial_fast_state
            initial_slow_state = config.initial_slow_state
        elif isinstance(config, dict):
            fast_gain = config.get("fast_gain", fast_gain)
            slow_gain = config.get("slow_gain", slow_gain)
            tau = config.get("tau", tau)
            noise_std = config.get("noise_std", noise_std)
            drift_rate = config.get("drift_rate", drift_rate)
            fast_min = config.get("fast_min", fast_min)
            fast_max = config.get("fast_max", fast_max)
            slow_min = config.get("slow_min", slow_min)
            slow_max = config.get("slow_max", slow_max)
            timestep = config.get("timestep", timestep)
            initial_detuning = config.get("initial_detuning", initial_detuning)
            initial_fast_state = config.get("initial_fast_state", initial_fast_state)
            initial_slow_state = config.get("initial_slow_state", initial_slow_state)

        self.fast_gain = float(fast_gain)
        self.slow_gain = float(slow_gain)
        self.tau = max(float(tau), 1e-6)
        self.noise_std = float(noise_std)
        self.drift_rate = float(drift_rate)
        self.fast_min = float(fast_min)
        self.fast_max = float(fast_max)
        self.slow_min = float(slow_min)
        self.slow_max = float(slow_max)
        self.timestep = float(timestep)
        self.detuning = float(initial_detuning)
        self.fast_state = float(initial_fast_state)
        self.slow_state = float(initial_slow_state)
        self.history_detuning: list[float] = []
        self.history_fast_state: list[float] = []
        self.history_slow_state: list[float] = []
        self.history_fast_code: list[float] = []
        self.history_slow_code: list[float] = []
        self._rng = np.random.default_rng(0) if np is not None else None

    def _clip(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    def _normal(self, std: float) -> float:
        if self._rng is not None:
            return float(self._rng.normal(0.0, std))
        return 0.0

    def step(self, fast_code: float, slow_code: float) -> float:
        """Advance the laser plant by one timestep and return the new detuning."""

        fast_code = float(fast_code)
        slow_code = float(slow_code)
        fast_code = self._clip(fast_code, self.fast_min, self.fast_max)
        slow_code = self._clip(slow_code, self.slow_min, self.slow_max)
        fast_norm = (fast_code - self.fast_min) / (self.fast_max - self.fast_min + 1e-9)
        slow_norm = (slow_code - self.slow_min) / (self.slow_max - self.slow_min + 1e-9)
        fast_target = self.fast_gain * fast_norm
        slow_target = self.slow_gain * slow_norm

        self.fast_state += (fast_target - self.fast_state) * (self.timestep / self.tau)
        self.slow_state += (slow_target - self.slow_state) * (self.timestep / self.tau)

        drift_term = self.drift_rate * self.timestep
        noise_term = self._normal(self.noise_std)
        self.detuning += (self.fast_state + self.slow_state + drift_term + noise_term) * self.timestep

        self.history_detuning.append(self.detuning)
        self.history_fast_state.append(self.fast_state)
        self.history_slow_state.append(self.slow_state)
        self.history_fast_code.append(fast_code)
        self.history_slow_code.append(slow_code)
        return float(self.detuning)
