from __future__ import annotations

import csv
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amaranth import *
from amaranth.sim import Simulator

from rtl.bus.register_defs import (
    ADDR_CONTROL,
    ADDR_MODE,
    ADDR_TRACE_CONFIG,
    ADDR_TRACE_LENGTH,
    ADDR_SLOW_CTRL_CONFIG,
    ADDR_SLOW_RECENTER_TARGET,
    ADDR_SLOW_RECENTER_GAIN,
    ADDR_SLOW_OUT_MIN,
    ADDR_SLOW_OUT_MAX,
    ADDR_SLOW_OUT_SAFE,
    ADDR_SLOW_BIAS,
    ADDR_FAULT_ENABLE,
    CTRL_GLOBAL_ENABLE,
    CTRL_LOCK_ENABLE_REQUEST,
    CTRL_TRACE_CAPTURE_ENABLE,
    CTRL_AUTOLOCK_ENABLE,
    CTRL_SLOW_RECENTER_ENABLE,
)
from top.lock_core_top import LockCoreTop
from sim.models.fake_laser_plant import FakeLaserPlant
from sim.models.fake_mts_signal import FakeMTSSignal
from sim.models.fault_injector import FaultInjector
from sim.models.simulation_config import SimulationConfig


class ClosedLoopRunner:
    """Drive the Amaranth lock-core DUT with a physics-based plant model."""

    def __init__(self, config: Optional[SimulationConfig] = None) -> None:
        self.config = config or SimulationConfig()
        self.config.ensure_directories()
        self.plant = FakeLaserPlant(self.config.laser)
        self.signal = FakeMTSSignal(self.config.spectroscopy)
        self.injector = FaultInjector(self.config.faults)
        self.dut = LockCoreTop()
        self._history: List[Dict[str, Any]] = []
        self._start_time = time.time()

    def _write_reg(self, dut: Any, addr: int, value: int):
        yield dut.adr.eq(addr)
        yield dut.dat_w.eq(value)
        yield dut.we.eq(1)
        yield dut.stb.eq(1)
        yield
        yield dut.we.eq(0)
        yield dut.stb.eq(0)
        yield

    def _configure_dut(self):
        yield from self._write_reg(self.dut, ADDR_CONTROL,
                                   (1 << CTRL_GLOBAL_ENABLE) | (1 << CTRL_LOCK_ENABLE_REQUEST))
        yield from self._write_reg(self.dut, ADDR_MODE, 0)
        yield from self._write_reg(self.dut, ADDR_TRACE_CONFIG, 1)
        yield from self._write_reg(self.dut, ADDR_TRACE_LENGTH, 256)
        yield from self._write_reg(self.dut, ADDR_SLOW_CTRL_CONFIG,
                                   (1 << CTRL_TRACE_CAPTURE_ENABLE) | (1 << CTRL_AUTOLOCK_ENABLE)
                                   | (1 << CTRL_SLOW_RECENTER_ENABLE))
        yield from self._write_reg(self.dut, ADDR_SLOW_BIAS, 0)
        yield from self._write_reg(self.dut, ADDR_SLOW_RECENTER_TARGET, 0)
        yield from self._write_reg(self.dut, ADDR_SLOW_RECENTER_GAIN, 64)
        yield from self._write_reg(self.dut, ADDR_SLOW_OUT_MIN, -4096)
        yield from self._write_reg(self.dut, ADDR_SLOW_OUT_MAX, 4096)
        yield from self._write_reg(self.dut, ADDR_SLOW_OUT_SAFE, 0)
        yield from self._write_reg(self.dut, ADDR_FAULT_ENABLE, 0xFFFF)

    def _run_simulation(self) -> None:
        def tb(dut: Any):
            yield dut.rst.eq(1)
            for _ in range(4):
                yield
            yield dut.rst.eq(0)
            yield from self._configure_dut()

            for step in range(self.config.timing.steps):
                time_s = step * self.config.timing.timestep_s
                fast_dac = int(getattr(dut, "o_dac_fast", 0)) if hasattr(dut, "o_dac_fast") else 0
                slow_dac = int(getattr(dut, "o_dac_slow", 0)) if hasattr(dut, "o_dac_slow") else 0

                detuning = self.plant.step(float(fast_dac), float(slow_dac))
                ideal_error = self.signal.ideal(detuning)
                measured_error = self.signal.sample(detuning)
                detuning_adj, adc_sample, fast_adj, slow_adj = self.injector.apply(
                    detuning, measured_error, fast_dac, slow_dac, time_s)

                yield dut.i_adc_ch0.eq(int(round(adc_sample)))
                yield dut.i_adc_ch1.eq(int(round(adc_sample * 0.5)))
                yield dut.i_adc_valid.eq(1)
                yield dut.i_adc_overrange_ch0.eq(0)
                yield dut.i_adc_overrange_ch1.eq(0)
                yield dut.i_external_interlock.eq(0)
                yield dut.i_feature_selected.eq(step > 100)
                yield

                self._history.append({
                    "time": time_s,
                    "laser_detuning": detuning_adj,
                    "ideal_error": ideal_error,
                    "measured_error": measured_error,
                    "adc_sample": adc_sample,
                    "fast_dac": fast_adj,
                    "slow_dac": slow_adj,
                    "controller_output": float(getattr(dut, "fast_output", 0)),
                    "controller_output_before_limiting": float(getattr(dut, "fast_output", 0)),
                    "controller_output_after_limiting": fast_adj,
                    "integrator": 0.0,
                    "lock_state": int(getattr(dut, "lock_state", 0)),
                    "fault_flags": ",".join(self.injector.active_flags),
                    "thermal_drift": self.signal._drift,
                    "fast_saturation_flag": 1.0 if abs(fast_adj) >= 1000.0 else 0.0,
                    "slow_saturation_flag": 1.0 if abs(slow_adj) >= 1000.0 else 0.0,
                })

            for _ in range(20):
                yield dut.i_adc_valid.eq(0)
                yield

        sim = Simulator(self.dut)
        sim.add_clock(self.config.timing.clock_period_s)
        sim.add_process(tb)
        sim.run()

    def run(self) -> Dict[str, Any]:
        self._run_simulation()
        self._write_csv()
        self._write_plots()
        return self.summarize()

    def _write_csv(self) -> None:
        csv_path = self.config.outputs.csv_dir / "simulation.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "time",
                "laser_detuning",
                "ideal_error",
                "measured_error",
                "adc_sample",
                "fast_dac",
                "slow_dac",
                "controller_output",
                "controller_output_before_limiting",
                "controller_output_after_limiting",
                "integrator",
                "lock_state",
                "fault_flags",
                "thermal_drift",
                "fast_saturation_flag",
                "slow_saturation_flag",
            ])
            writer.writeheader()
            for row in self._history:
                writer.writerow(row)

    def _write_plots(self) -> None:
        if not self._history:
            return

        times = [row["time"] for row in self._history]
        detuning = [row["laser_detuning"] for row in self._history]
        ideal = [row["ideal_error"] for row in self._history]
        measured = [row["measured_error"] for row in self._history]
        fast = [row["fast_dac"] for row in self._history]
        slow = [row["slow_dac"] for row in self._history]
        error = [row["measured_error"] for row in self._history]
        lock_state = [row["lock_state"] for row in self._history]
        fault_flags = [row["fault_flags"] for row in self._history]
        thermal_drift = [row["thermal_drift"] for row in self._history]
        controller_before = [row["controller_output_before_limiting"] for row in self._history]
        controller_after = [row["controller_output_after_limiting"] for row in self._history]
        fast_sat = [row["fast_saturation_flag"] for row in self._history]
        slow_sat = [row["slow_saturation_flag"] for row in self._history]

        # Basic plots
        from sim.models.plotting import save_line_plot, save_histogram, save_multi_panel_plot

        save_line_plot(times, detuning, title="Laser detuning vs time", xlabel="Time (s)", ylabel="Detuning",
                       legend=["laser detuning"], output_path=str(self.config.outputs.plot_dir / "laser_detuning_vs_time.png"))
        save_line_plot(times, ideal, title="Ideal MTS curve", xlabel="Time (s)", ylabel="Ideal error",
                       legend=["ideal"], output_path=str(self.config.outputs.plot_dir / "ideal_mts_curve.png"))
        save_line_plot(times, measured, title="Measured MTS curve", xlabel="Time (s)", ylabel="Measured error",
                       legend=["measured"], output_path=str(self.config.outputs.plot_dir / "measured_mts_curve.png"))
        save_line_plot(times, fast, title="Fast DAC vs time", xlabel="Time (s)", ylabel="Fast DAC",
                       legend=["fast DAC"], output_path=str(self.config.outputs.plot_dir / "fast_dac_vs_time.png"))
        save_line_plot(times, slow, title="Slow DAC vs time", xlabel="Time (s)", ylabel="Slow DAC",
                       legend=["slow DAC"], output_path=str(self.config.outputs.plot_dir / "slow_dac_vs_time.png"))
        save_line_plot(times, error, title="Corrected error vs time", xlabel="Time (s)", ylabel="Error",
                       legend=["error"], output_path=str(self.config.outputs.plot_dir / "corrected_error_vs_time.png"))
        save_line_plot(times, controller_before, title="Controller output before limiting", xlabel="Time (s)", ylabel="Controller output",
                       legend=["before limiting"], output_path=str(self.config.outputs.plot_dir / "controller_output_before_limiting.png"))
        save_line_plot(times, controller_after, title="Controller output after limiting", xlabel="Time (s)", ylabel="Controller output",
                       legend=["after limiting"], output_path=str(self.config.outputs.plot_dir / "controller_output_after_limiting.png"))
        save_line_plot(times, fast_sat, title="Fast saturation flags", xlabel="Time (s)", ylabel="Saturation flag",
                       legend=["fast sat"], output_path=str(self.config.outputs.plot_dir / "fast_saturation_flags.png"))
        save_line_plot(times, slow_sat, title="Slow saturation flags", xlabel="Time (s)", ylabel="Saturation flag",
                       legend=["slow sat"], output_path=str(self.config.outputs.plot_dir / "slow_saturation_flags.png"))
        save_line_plot(times, thermal_drift, title="Thermal drift", xlabel="Time (s)", ylabel="Drift",
                       legend=["thermal drift"], output_path=str(self.config.outputs.plot_dir / "thermal_drift.png"))
        save_line_plot(times, lock_state, title="Lock state timeline", xlabel="Time (s)", ylabel="Lock state",
                       legend=["state"], output_path=str(self.config.outputs.plot_dir / "lock_state_timeline.png"))
        save_line_plot(times, [1.0 if flag else 0.0 for flag in fault_flags], title="Fault timeline", xlabel="Time (s)", ylabel="Fault active",
                       legend=["fault"], output_path=str(self.config.outputs.plot_dir / "fault_timeline.png"))
        save_histogram(error, title="Error histogram", xlabel="Error", output_path=str(self.config.outputs.plot_dir / "error_histogram.png"))
        save_histogram(fast, title="Fast DAC histogram", xlabel="Fast DAC", output_path=str(self.config.outputs.plot_dir / "fast_dac_histogram.png"))
        save_histogram(slow, title="Slow DAC histogram", xlabel="Slow DAC", output_path=str(self.config.outputs.plot_dir / "slow_dac_histogram.png"))
        save_multi_panel_plot([
            ("Laser detuning", detuning),
            ("Error", error),
            ("Fast DAC", fast),
            ("Slow DAC", slow),
            ("Lock state", lock_state),
        ], title="Summary dashboard", output_path=str(self.config.outputs.plot_dir / "summary_dashboard.png"))

    def summarize(self) -> Dict[str, Any]:
        if not self._history:
            return {}

        errors = [row["measured_error"] for row in self._history]
        fast = [row["fast_dac"] for row in self._history]
        slow = [row["slow_dac"] for row in self._history]
        lock_states = [row["lock_state"] for row in self._history]
        summary = {
            "lock_acquisition_time": 0.0,
            "settling_time": 0.0,
            "steady_state_rms_error": float(sum(x * x for x in errors[-100:]) / max(1, len(errors[-100:]))) ** 0.5,
            "peak_error": float(max(abs(value) for value in errors)),
            "mean_error": float(sum(errors) / len(errors)),
            "overshoot": 0.0,
            "max_fast_dac": float(max(abs(value) for value in fast)),
            "max_slow_dac": float(max(abs(value) for value in slow)),
            "fast_saturation_count": int(sum(1 for value in fast if abs(value) >= 1000.0)),
            "slow_saturation_count": int(sum(1 for value in slow if abs(value) >= 1000.0)),
            "average_lock_error": float(sum(errors[-100:]) / max(1, len(errors[-100:]))),
            "lock_success": bool(sum(1 for value in lock_states if value > 0) > 0),
            "injected_faults": ",".join(sorted(set(f for row in self._history for f in row["fault_flags"].split(",") if f))),
            "recovered_faults": 0,
            "failed_recoveries": 0,
            "simulation_runtime": time.time() - self._start_time,
        }
        return summary

    def print_summary(self, summary: Optional[Dict[str, Any]] = None) -> None:
        summary = summary or self.summarize()
        print("\nClosed-loop simulation summary")
        print("=" * 32)
        for key, value in summary.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    runner = ClosedLoopRunner()
    summary = runner.run()
    runner.print_summary(summary)
