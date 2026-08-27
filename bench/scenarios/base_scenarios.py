"""General-purpose benchmark scenarios."""

from __future__ import annotations

from .scenario_base import Scenario


BASE_SCENARIOS = (
    Scenario(
        name="clean_easy_lock",
        duration_s=1.0,
        signal={"noise_std": 2.0, "drift_rate": 0.0},
        plant={"noise_std": 0.0, "initial_detuning": 0.4},
        expected_lock=True,
    ),
    Scenario(
        name="noisy_low_snr",
        duration_s=2.0,
        signal={"noise_std": 80.0},
        plant={"noise_std": 0.08, "initial_detuning": 0.8},
        expected_lock=True,
    ),
    Scenario(
        name="slow_dc_drift",
        duration_s=3.0,
        signal={"noise_std": 4.0, "drift_rate": 0.15},
        plant={"noise_std": 0.02, "drift_rate": 0.08, "initial_detuning": 0.5},
        expected_lock=True,
    ),
    Scenario(
        name="signal_dropout_mid_lock",
        duration_s=2.0,
        signal={"noise_std": 3.0},
        faults={"dropout": {"start_time": 1.0, "duration": 0.25}},
        plant={"initial_detuning": 0.4},
        expected_lock=False,
    ),
    Scenario(
        name="wrong_polarity",
        duration_s=1.5,
        signal={"noise_std": 3.0, "slope_sign": -1.0},
        plant={"initial_detuning": 0.6},
        expected_lock=False,
    ),
    Scenario(
        name="fixed_point_limits",
        duration_s=1.0,
        signal={"amplitude": 32_000.0, "noise_std": 0.0},
        plant={"initial_detuning": 2.0},
        expected_lock=False,
    ),
)


def get_base_scenarios() -> tuple[Scenario, ...]:
    """Return the immutable benchmark set."""

    return BASE_SCENARIOS
