"""POSM adapter backed by the existing Amaranth closed-loop simulation."""

from __future__ import annotations

from dataclasses import replace

from .base import Architecture
from ..scenarios.scenario_base import Result, Scenario


class PosmSim(Architecture):
    """Run the working-tree POSM RTL through ``ClosedLoopRunner``."""

    name = "posm-sim"

    def run(self, scenario: Scenario) -> Result:
        from sim.models.simulation_config import SimulationConfig
        from sim.run_closed_loop_demo import ClosedLoopRunner

        config = SimulationConfig()
        config.timing = replace(
            config.timing,
            timestep_s=scenario.timestep_s,
            duration_s=scenario.duration_s,
            sample_rate_hz=scenario.sample_rate_hz,
            steps=scenario.steps,
        )
        config.laser = replace(config.laser, **dict(scenario.plant))
        config.spectroscopy = replace(config.spectroscopy, **dict(scenario.signal), seed=scenario.seed)
        config.lock_threshold = scenario.lock_threshold
        config.settling_window = scenario.settling_window

        runner = ClosedLoopRunner(config)
        summary = runner.run()
        history = runner._history
        return Result(
            architecture=self.name,
            scenario=scenario.name,
            times=[float(row["time"]) for row in history],
            error=[float(row["measured_error"]) for row in history],
            output=[float(row["controller_output_after_limiting"]) for row in history],
            lock_state=[row["lock_state"] for row in history],
            fault_flags=[row["fault_flags"] for row in history],
            traces={
                "laser_detuning": [row["laser_detuning"] for row in history],
                "fast_dac": [row["fast_dac"] for row in history],
                "slow_dac": [row["slow_dac"] for row in history],
            },
            metadata={"summary": summary, "backend": "Amaranth"},
        )
