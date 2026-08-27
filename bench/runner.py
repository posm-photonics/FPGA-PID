"""Run every architecture against every scenario."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .architectures.base import Architecture
from .metrics import score_result
from .scenarios.scenario_base import Scenario


@dataclass
class BenchmarkRunner:
    architectures: list[Architecture]
    scenarios: list[Scenario]
    rows: list[dict] = field(default_factory=list)

    def run(self) -> list[dict]:
        """Execute the Cartesian product and return scorecard rows."""

        self.rows = []
        for scenario in self.scenarios:
            for architecture in self.architectures:
                result = architecture.run(scenario)
                self.rows.append(score_result(result, scenario))
        return list(self.rows)


def run_benchmark(architectures: Iterable[Architecture], scenarios: Iterable[Scenario]) -> list[dict]:
    """Convenience function for scripts and notebooks."""

    return BenchmarkRunner(list(architectures), list(scenarios)).run()
