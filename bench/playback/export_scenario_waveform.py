"""Export a deterministic scenario signal for external playback equipment."""

from __future__ import annotations

import csv
import math
import struct
import wave
from pathlib import Path

from ..scenarios.scenario_base import Scenario


def generate_waveform(scenario: Scenario) -> list[float]:
    """Generate the noise-free input waveform described by a scenario."""

    signal = {"amplitude": 2500.0, "width": 1.0, "offset": 0.0, **scenario.signal}
    initial = float(scenario.plant.get("initial_detuning", 0.0))
    drift = float(scenario.plant.get("drift_rate", 0.0))
    values = []
    for index in range(scenario.steps):
        detuning = initial + drift * index * scenario.timestep_s
        normalized = detuning / max(abs(float(signal["width"])), 1e-9)
        values.append(float(signal["offset"]) + float(signal["amplitude"]) * normalized * math.exp(-0.5 * normalized ** 2))
    return values


def export_csv(scenario: Scenario, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", "value"])
        for index, value in enumerate(generate_waveform(scenario)):
            writer.writerow([index * scenario.timestep_s, value])
    return path


def export_wav(scenario: Scenario, path: str | Path, *, amplitude: int = 32767) -> Path:
    """Write a mono 16-bit WAV, scaling the scenario peak to ``amplitude``."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = generate_waveform(scenario)
    peak = max((abs(value) for value in values), default=1.0)
    scale = min(1.0, amplitude / peak)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(round(scenario.sample_rate_hz))
        handle.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, round(value * scale)))) for value in values))
    return path
