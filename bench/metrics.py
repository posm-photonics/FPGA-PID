"""Small, backend-independent benchmark metrics."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .scenarios.scenario_base import Result, Scenario


def _finite_pairs(times: Sequence[float], values: Sequence[float]) -> list[tuple[float, float]]:
    return [(float(time), float(value)) for time, value in zip(times, values)
            if math.isfinite(float(time)) and math.isfinite(float(value))]


def _lock_time(result: Result, scenario: Scenario) -> float | None:
    """Return the first observed lock time, or ``None`` if the run timed out."""

    # POSM results include the authoritative RTL lock state. The analog error
    # is intentionally independent of that state and can remain scaled in
    # physical units, so do not reject a hardware lock because the two models
    # use different units.
    if result.lock_state and len(result.lock_state) == len(result.times):
        for time, state in zip(result.times, result.lock_state):
            if int(state) in (7, 8):  # LOCKED or LOCK_WATCH
                return float(time)

    samples = _finite_pairs(result.times, result.error)
    window = max(1, scenario.settling_window)
    threshold = abs(float(scenario.lock_threshold))
    for index in range(0, len(samples) - window + 1):
        if all(abs(value) <= threshold for _, value in samples[index:index + window]):
            return samples[index][0]
    return None


def time_to_lock(result: Result, scenario: Scenario) -> float:
    """Return lock time, censoring an unlocked run at the scenario duration."""

    locked_at = _lock_time(result, scenario)
    return float(scenario.duration_s) if locked_at is None else locked_at


def overshoot(result: Result, scenario: Scenario) -> float:
    """Return the maximum absolute error beyond the lock threshold."""

    threshold = abs(float(scenario.lock_threshold))
    return max((abs(value) - threshold for _, value in _finite_pairs(result.times, result.error)), default=0.0)


def steady_state_error(result: Result, scenario: Scenario) -> float:
    """Return RMS error over the final settling window."""

    values = [value for _, value in _finite_pairs(result.times, result.error)]
    if not values:
        return math.nan
    tail = values[-max(1, scenario.settling_window):]
    return math.sqrt(sum(value * value for value in tail) / len(tail))


def output_noise(result: Result, scenario: Scenario) -> float:
    """Return standard deviation of the post-lock error signal."""

    values = [value for _, value in _finite_pairs(result.times, result.error)]
    if not values:
        return math.nan
    tail = values[-max(1, scenario.settling_window):]
    mean = sum(tail) / len(tail)
    return math.sqrt(sum((value - mean) ** 2 for value in tail) / len(tail))


def false_trigger_count(result: Result, scenario: Scenario) -> int:
    """Count unexpected fault transitions or unexpected lock outcome."""

    flags = result.fault_flags
    transitions = sum(bool(current) != bool(previous)
                      for previous, current in zip(flags, flags[1:]))
    expected_fault = bool(scenario.faults)
    unexpected_fault = transitions if not expected_fault else 0
    locked = _lock_time(result, scenario) is not None
    unexpected_lock = int(scenario.expected_lock is not None and locked != scenario.expected_lock)
    return int(unexpected_fault + unexpected_lock)


def score_result(result: Result, scenario: Scenario) -> dict[str, Any]:
    """Calculate the stable scorecard columns for one run."""

    return {
        "architecture": result.architecture,
        "scenario": result.scenario,
        "samples": result.sample_count,
        "time_to_lock_s": time_to_lock(result, scenario),
        "lock_achieved": _lock_time(result, scenario) is not None,
        "overshoot": overshoot(result, scenario),
        "steady_state_error": steady_state_error(result, scenario),
        "output_noise_rms": output_noise(result, scenario),
        "false_trigger_count": false_trigger_count(result, scenario),
    }
