"""Repeatable apples-to-apples architecture comparison harness."""

from .metrics import score_result
from .runner import BenchmarkRunner, run_benchmark
from .scenarios import Result, Scenario

__all__ = ["BenchmarkRunner", "Result", "Scenario", "run_benchmark", "score_result"]
