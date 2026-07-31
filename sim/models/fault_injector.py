from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised in minimal environments
    np = None

from .simulation_config import FaultSettings, FaultSpec


class FaultInjector:
    """Inject hardware faults into the plant/ADC/DAC signal path."""

    def __init__(self, config: Optional[FaultSettings] = None) -> None:
        self.config = config or FaultSettings()
        self._active_faults: Dict[str, FaultSpec] = {}
        self.active_flags: List[str] = []
        self._rng = np.random.default_rng(11) if np is not None else None

    def enable_fault(self, name: str, *, start_time: float = 0.0, duration: float = 0.0,
                     severity: float = 1.0) -> None:
        """Enable a named fault for the requested interval."""

        self._active_faults[name] = FaultSpec(name=name, start_time=start_time, duration=duration,
                                              severity=severity, enabled=True)

    def disable_fault(self, name: str) -> None:
        """Disable a named fault"""

        self._active_faults.pop(name, None)

    def _is_active(self, fault: FaultSpec, time_s: float) -> bool:
        if not fault.enabled:
            return False
        if fault.duration <= 0.0:
            return time_s >= fault.start_time
        return fault.start_time <= time_s <= fault.start_time + fault.duration

    def apply(self, detuning: float, adc_sample: float, fast_dac: float, slow_dac: float,
              time_s: float) -> Tuple[float, float, float, float]:
        """Apply all active faults to the current physics simulation state."""

        detuning_out = float(detuning)
        adc_out = float(adc_sample)
        fast_out = float(fast_dac)
        slow_out = float(slow_dac)
        self.active_flags = []

        for name, spec in self._active_faults.items():
            if not self._is_active(spec, time_s):
                continue
            self.active_flags.append(name)
            severity = max(float(spec.severity), 0.0)
            if name == "adc_overrange":
                adc_out = adc_out + severity * 1000.0
            elif name == "adc_stuck_high":
                adc_out = 65535.0
            elif name == "adc_stuck_low":
                adc_out = 0.0
            elif name == "missing_adc_samples":
                adc_out = 0.0
            elif name == "excessive_measurement_noise":
                if self._rng is not None:
                    adc_out = adc_out + self._rng.normal(0.0, severity * 100.0)
                else:
                    adc_out = adc_out + severity * 100.0
            elif name == "spectroscopy_feature_disappears":
                detuning_out = detuning_out * (1.0 - 0.6 * severity)
            elif name == "wrong_polarity":
                adc_out = -adc_out
            elif name == "fast_actuator_saturation":
                fast_out = max(-1000.0, min(1000.0, fast_out))
            elif name == "slow_actuator_saturation":
                slow_out = max(-1000.0, min(1000.0, slow_out))
            elif name == "sudden_laser_frequency_jump":
                detuning_out += severity * 1000.0

        return detuning_out, adc_out, fast_out, slow_out
