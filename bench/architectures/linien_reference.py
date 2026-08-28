"""Approximate, simulation-only model of a Linien-style PID loop.

WARNING -- READ BEFORE QUOTING ANY RESULT FROM THIS FILE
========================================================
This is an IDEALISED FLOATING-POINT model. It is not Linien, it is not
Linien's gateware, and it is not even fixed point: the integrator is a
Python float updated as `integral += error * timestep`.

That matters more than it sounds. The class of defect that was actually
breaking the POSM design was fixed-point truncation:

  * the PI integrator shifted Ki*e down BEFORE accumulating, giving a
    dead zone for small errors and a monotonic drift to a rail on a
    zero-mean error, and
  * the demodulation low-pass did the same thing and settled up to
    2^alpha - 1 counts short of its input.

A float model has none of that behaviour by construction, so comparing
POSM against this reference can never surface that class of bug. It
scores loop shaping and scenario handling only.

Do not describe output from this harness as parity with Linien. For a
real comparison either read Linien's gateware directly
(linien/gateware/logic/pid.py) or drive real hardware through
LinienHardware, which needs an injected client that does not currently
exist.
"""

from __future__ import annotations

import math
import random

from .base import Architecture
from ..scenarios.scenario_base import Result, Scenario
from sim.models.fault_injector import FaultInjector


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
        injector = FaultInjector()
        for name, specification in scenario.faults.items():
            injector.enable_fault(name, **dict(specification))
        integral = 0.0
        times: list[float] = []
        errors: list[float] = []
        outputs: list[float] = []
        fault_flags: list[str] = []
        for index in range(scenario.steps):
            time_s = index * scenario.timestep_s
            normalized = detuning / max(abs(float(signal["width"])), 1e-9)
            error = float(signal["amplitude"]) * normalized * math.exp(-0.5 * normalized * normalized)
            error *= float(signal.get("slope_sign", 1.0))
            error += rng.gauss(0.0, float(signal.get("noise_std", 0.0)))
            _, error, _, _ = injector.apply(detuning, error, 0.0, 0.0, time_s)
            fault_flags.append(",".join(injector.active_flags))
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
            fault_flags=fault_flags,
            metadata={"backend": "Python approximation", "cycle_accurate": False},
        )
