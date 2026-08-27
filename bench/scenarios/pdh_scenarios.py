"""PDH-specific scenarios reserved for the completed PDH demodulator."""

from __future__ import annotations

from .scenario_base import Scenario


def get_pdh_scenarios() -> tuple[Scenario, ...]:
    """Return PDH cases once the PDH demodulation benchmark is available."""

    raise NotImplementedError(
        "PDH scenarios are intentionally deferred until the demodulation chain "
        "has a stable architecture adapter."
    )
