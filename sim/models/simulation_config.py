from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class TimingConfig:
    """Simulation timing parameters."""

    timestep_s: float = 1e-3
    duration_s: float = 2.0
    sample_rate_hz: float = 1e3
    clock_period_s: float = 1e-8
    steps: int = 2000


@dataclass
class LaserConfig:
    """Laser plant parameters."""

    fast_gain: float = 0.35
    slow_gain: float = 0.06
    tau: float = 0.04
    noise_std: float = 0.08
    drift_rate: float = 0.002
    fast_min: float = -5000.0
    fast_max: float = 5000.0
    slow_min: float = -2000.0
    slow_max: float = 2000.0
    initial_detuning: float = 0.0
    initial_fast_state: float = 0.0
    initial_slow_state: float = 0.0


@dataclass
class SpectroscopyConfig:
    """MTS spectroscopy model parameters."""

    center: float = 0.0
    width: float = 1.0
    amplitude: float = 2500.0
    offset: float = 0.0
    noise_std: float = 8.0
    drift_rate: float = 0.002
    slope_sign: float = 1.0
    seed: int = 7


@dataclass
class FaultSpec:
    """A single fault definition with timing and severity."""

    name: str
    start_time: float = 0.0
    duration: float = 0.0
    severity: float = 1.0
    enabled: bool = False


@dataclass
class FaultSettings:
    """Fault injector settings."""

    specs: Dict[str, FaultSpec] = field(default_factory=dict)


@dataclass
class ScanConfig:
    """Scan settings used by the closed-loop demo."""

    initial_scan_offset: float = -1800.0
    scan_span: float = 3600.0
    scan_steps: int = 100


@dataclass
class OutputConfig:
    """Directory layout for simulation artifacts."""

    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1] / "outputs")
    csv_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1] / "outputs" / "csv")
    plot_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1] / "outputs" / "plots")
    waveform_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1] / "outputs" / "waveforms")


@dataclass
class PlotConfig:
    """Plotting defaults."""

    dpi: int = 180
    figsize: tuple = (10, 6)
    linewidth: float = 1.8


@dataclass
class SimulationConfig:
    """Top-level container for the physics simulation environment."""

    timing: TimingConfig = field(default_factory=TimingConfig)
    laser: LaserConfig = field(default_factory=LaserConfig)
    spectroscopy: SpectroscopyConfig = field(default_factory=SpectroscopyConfig)
    faults: FaultSettings = field(default_factory=FaultSettings)
    scan: ScanConfig = field(default_factory=ScanConfig)
    outputs: OutputConfig = field(default_factory=OutputConfig)
    plots: PlotConfig = field(default_factory=PlotConfig)
    adc_scale: float = 50.0
    lock_threshold: float = 25.0
    settling_window: int = 80
    lock_hold_cycles: int = 20

    def ensure_directories(self) -> None:
        """Create all output directories if they do not exist."""

        for path in (self.outputs.csv_dir, self.outputs.plot_dir, self.outputs.waveform_dir):
            path.mkdir(parents=True, exist_ok=True)
