"""Architecture adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..scenarios.scenario_base import Result, Scenario


class Architecture(ABC):
    """A pluggable implementation that can execute one benchmark scenario."""

    name = "architecture"

    @abstractmethod
    def run(self, scenario: Scenario) -> Result:
        """Run the scenario and return recorded, backend-neutral data."""
        raise NotImplementedError
