"""Approximate, simulation-only model of a Linien-style PID loop."""

from __future__ import annotations

import math
import random

from .base import Architecture
from ..scenarios.scenario_base import Result, Scenario


class LinienReference(Architecture):
    """Fast non-cycle-accurate reference model, not Linien gateware."""

    name = "linien-reference"

    def __init__(self, kp: float = 0.8, ki: float = 0.12, output_limit: float = 5000.0) -> None:
        self.kp = float(kp)
        self.ki = float(ki)
        self.output_limit = abs(float(output_limit))

    def run(self, scenario: Scenario) -> Result:
        signal = {"amplitude": 2500.0, "width": 1.0, "noise_std": 0.0, **scenario.signal}
        detuning = float(scenario.plant.get("initial_detuning", 0.0))
        drift = float(scenario.plant.get("drift_rate", 0.0))
        rng = random.Random(scenario.seed)
        integral = 0.0
        times: list[float] = []
        errors: list[float] = []
        outputs: list[float] = []
        for index in range(scenario.steps):
            time_s = index * scenario.timestep_s
            normalized = detuning / max(abs(float(signal["width"])), 1e-9)
            error = float(signal["amplitude"]) * normalized * math.exp(-0.5 * normalized * normalized)
            error *= float(signal.get("slope_sign", 1.0))
            error += rng.gauss(0.0, float(signal.get("noise_std", 0.0)))
            integral += error * scenario.timestep_s
            command = max(-self.output_limit, min(self.output_limit, self.kp * error + self.ki * integral))
            detuning += (drift - command * 0.001) * scenario.timestep_s
            times.append(time_s)
            errors.append(error)
            outputs.append(command)
        return Result(
            architecture=self.name,
            scenario=scenario.name,
            times=times,
            error=errors,
            output=outputs,
            metadata={"backend": "Python approximation", "cycle_accurate": False},
        )
