"""Stable console, CSV, and JSON scorecard output."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Iterable, TextIO


COLUMNS = (
    "architecture", "scenario", "samples", "time_to_lock_s", "overshoot",
    "steady_state_error", "output_noise_rms", "false_trigger_count",
)


def _display(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def format_table(rows: Iterable[dict]) -> str:
    rows = list(rows)
    if not rows:
        return "No benchmark results."
    values = [[_display(row.get(column)) for column in COLUMNS] for row in rows]
    widths = [max(len(column), *(len(row[index]) for row in values)) for index, column in enumerate(COLUMNS)]
    header = " | ".join(column.ljust(widths[index]) for index, column in enumerate(COLUMNS))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in values]
    return "\n".join([header, divider, *body])


def write_report(rows: Iterable[dict], *, csv_path: Path | None = None,
                 json_path: Path | None = None, stream: TextIO = sys.stdout) -> None:
    """Write the console table and optional archival formats."""

    rows = list(rows)
    stream.write(format_table(rows) + "\n")
    if csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows({column: row.get(column) for column in COLUMNS} for row in rows)
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n", encoding="utf-8")
