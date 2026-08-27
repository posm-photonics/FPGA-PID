#!/usr/bin/env python3
"""Run the project benchmark harness.

From the repo root:
    python run_bench.py

This executes the built-in benchmark scenarios against the available
simulation architectures and prints a summary table while also writing CSV/JSON
artifacts under a local bench_output/ directory.
"""

from __future__ import annotations

from pathlib import Path

from bench.architectures.linien_reference import LinienReference
from bench.architectures.posm_sim import PosmSim
from bench.report import write_report
from bench.runner import run_benchmark
from bench.scenarios.base_scenarios import get_base_scenarios


def main() -> int:
    architectures = [
        PosmSim(),
        LinienReference(),
    ]
    scenarios = get_base_scenarios()

    output_dir = Path(__file__).resolve().parent / "bench_output"
    output_dir.mkdir(exist_ok=True)

    print(f"Running benchmark: {len(architectures)} architectures x {len(scenarios)} scenarios")
    rows = run_benchmark(architectures, scenarios)

    write_report(
        rows,
        csv_path=output_dir / "bench_results.csv",
        json_path=output_dir / "bench_results.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
