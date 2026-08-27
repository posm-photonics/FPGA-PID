"""Scenario and result contracts for architecture comparisons."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class Scenario:
    """A deterministic experiment definition shared by all architectures."""

    name: str
    duration_s: float = 2.0
    sample_rate_hz: float = 1_000.0
    plant: Mapping[str, Any] = field(default_factory=dict)
    signal: Mapping[str, Any] = field(default_factory=dict)
    faults: Mapping[str, Any] = field(default_factory=dict)
    expected_lock: Optional[bool] = None
    lock_threshold: float = 25.0
    settling_window: int = 80
    seed: int = 7

    @property
    def steps(self) -> int:
        return max(1, round(self.duration_s * self.sample_rate_hz))

    @property
    def timestep_s(self) -> float:
        return 1.0 / self.sample_rate_hz

    def with_updates(self, **updates: Any) -> "Scenario":
        """Return a scenario variant without mutating the library definition."""

        return replace(self, **updates)


@dataclass
class Result:
    """Recorded architecture output in a format independent of its backend."""

    architecture: str
    scenario: str
    times: list[float] = field(default_factory=list)
    error: list[float] = field(default_factory=list)
    output: list[float] = field(default_factory=list)
    lock_state: list[Any] = field(default_factory=list)
    fault_flags: list[Any] = field(default_factory=list)
    traces: dict[str, list[Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lengths = {len(values) for values in (self.times, self.error, self.output) if values}
        if len(lengths) > 1:
            raise ValueError("times, error, and output must have matching lengths")

    @property
    def sample_count(self) -> int:
        return len(self.times)

    def series(self, name: str) -> list[Any]:
        """Read a standard or backend-specific recorded series."""

        standard = {
            "time": self.times,
            "error": self.error,
            "output": self.output,
            "lock_state": self.lock_state,
            "fault_flags": self.fault_flags,
        }
        if name in standard:
            return standard[name]
        try:
            return self.traces[name]
        except KeyError as exc:
            raise KeyError(f"Result has no series named {name!r}") from exc
