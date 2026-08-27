"""Adapter for running a POSM fork in an isolated checkout."""

from __future__ import annotations

from pathlib import Path

from .base import Architecture
from ..scenarios.scenario_base import Result, Scenario


class PosmForkSim(Architecture):
    """Describe a fork checkout while preserving the common adapter contract.

    The fork must expose a compatible simulation entry point. This adapter
    fails explicitly until that checkout is supplied, rather than silently
    comparing different workloads.
    """

    name = "posm-fork-sim"

    def __init__(self, checkout: str | Path) -> None:
        self.checkout = Path(checkout).resolve()

    def run(self, scenario: Scenario) -> Result:
        if not self.checkout.is_dir():
            raise FileNotFoundError(f"POSM fork checkout does not exist: {self.checkout}")
        raise NotImplementedError(
            "A fork simulation entry point is required; expected the checkout "
            "to provide a scenario-compatible adapter."
        )
