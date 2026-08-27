"""Hardware-in-the-loop adapter boundary for Linien."""

from __future__ import annotations

from .base import Architecture
from ..scenarios.scenario_base import Result, Scenario


class LinienHardware(Architecture):
    """Run a scenario through a Linien board using an injected client.

    The client is intentionally injected so importing the harness does not
    require ``linien-client`` or network access. A client must implement
    ``run_scenario(scenario)`` and return a :class:`Result`.
    """

    name = "linien-hardware"

    def __init__(self, client: object) -> None:
        self.client = client

    def run(self, scenario: Scenario) -> Result:
        run_scenario = getattr(self.client, "run_scenario", None)
        if not callable(run_scenario):
            raise TypeError("Linien client must provide callable run_scenario(scenario)")
        result = run_scenario(scenario)
        if not isinstance(result, Result):
            raise TypeError("Linien client run_scenario must return bench.Result")
        return result
