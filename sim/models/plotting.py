from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

try:
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - exercised in minimal environments
    plt = None


def save_line_plot(x_values: Sequence[float], y_values: Sequence[float], *, title: str, xlabel: str,
                   ylabel: str, legend: Optional[list[str]] = None, output_path: Optional[str] = None) -> None:
    """Create and save a line plot to the configured output directory."""

    if plt is None:
        return
    output_path = output_path or str(Path(__file__).resolve().parents[1] / "outputs" / "plots" / f"{title.lower().replace(' ', '_')}.png")
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)
    ax.plot(x_values, y_values, linewidth=1.8)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    if legend:
        ax.legend(legend)
    fig.tight_layout()
    fig.savefig(output_file)
    plt.close(fig)


def save_scatter_plot(x_values: Sequence[float], y_values: Sequence[float], *, title: str,
                       xlabel: str, ylabel: str, output_path: Optional[str] = None) -> None:
    """Create and save a scatter plot."""

    if plt is None:
        return
    output_file = Path(output_path or str(Path(__file__).resolve().parents[1] / "outputs" / "plots" / f"{title.lower().replace(' ', '_')}.png"))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)
    ax.scatter(x_values, y_values, s=8, alpha=0.6)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_file)
    plt.close(fig)


def save_histogram(values: Sequence[float], *, title: str, xlabel: str, output_path: Optional[str] = None) -> None:
    """Create and save a histogram plot."""

    if plt is None:
        return
    output_file = Path(output_path or str(Path(__file__).resolve().parents[1] / "outputs" / "plots" / f"{title.lower().replace(' ', '_')}.png"))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)
    ax.hist(values, bins=40, alpha=0.8)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_file)
    plt.close(fig)


def save_multi_panel_plot(series: Sequence[tuple[str, Sequence[float]]], *, title: str,
                           output_path: Optional[str] = None) -> None:
    """Create and save a simple multi-panel dashboard plot."""

    if plt is None:
        return
    output_file = Path(output_path or str(Path(__file__).resolve().parents[1] / "outputs" / "plots" / f"{title.lower().replace(' ', '_')}.png"))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(len(series), 1, figsize=(10, 2.2 * len(series)), dpi=180)
    if len(series) == 1:
        axes = [axes]
    for axis, (name, values) in zip(axes, series):
        axis.plot(values, linewidth=1.2)
        axis.set_title(name)
        axis.grid(True, alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_file)
    plt.close(fig)
