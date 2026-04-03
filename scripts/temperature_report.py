#!/usr/bin/env python3
"""Utilities for formatting and plotting bucketed temperature history."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_dt(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _window_label(since_minutes: int) -> str:
    if since_minutes >= 7 * 24 * 60:
        return f"{since_minutes // (7 * 24 * 60)}w"
    if since_minutes >= 24 * 60:
        return f"{since_minutes // (24 * 60)}d"
    if since_minutes >= 60:
        return f"{since_minutes // 60}h"
    return f"{since_minutes}min"


def format_temperature_table(
    bucketed_observations: list[dict[str, Any]],
    config: dict[str, Any],
    since_minutes: int,
    bucket_minutes: int,
) -> str:
    """Return a compact text table of bucketed temperature readings."""
    from observation_analysis import metric_status

    temp_config = config["thresholds"]["temperature"]

    window_label = _window_label(since_minutes)

    lines: list[str] = [
        f"Temp history (last {window_label}, {bucket_minutes}-min buckets):"
    ]

    # Use date+time format when window spans more than 24 h
    ts_format = "%m-%d %H:%M" if since_minutes > 24 * 60 else "%H:%M"

    values: list[float] = []
    for obs in bucketed_observations:
        ts = obs.get("observedAt")
        temp = obs.get("temperatureC")
        if ts is None or temp is None:
            continue
        dt = _parse_dt(str(ts))
        value = float(temp)
        values.append(value)
        status = metric_status(value, temp_config)
        marker = "" if status == "normal" else f" [{status.upper()}]"
        lines.append(f"{dt.strftime(ts_format)}  {value:.1f}C{marker}")

    if values:
        avg = sum(values) / len(values)
        lines.append(f"Min:{min(values):.1f} Max:{max(values):.1f} Avg:{avg:.1f}C")
    else:
        lines.append("No temperature data in this window.")

    return "\n".join(lines)


def generate_temperature_plot(
    bucketed_observations: list[dict[str, Any]],
    config: dict[str, Any],
    since_minutes: int,
    bucket_minutes: int,
) -> Path:
    """Generate a temperature time-series PNG and return its temporary file path.

    Caller is responsible for deleting the file after use.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    timestamps: list[datetime] = []
    values: list[float] = []
    for obs in bucketed_observations:
        ts = obs.get("observedAt")
        temp = obs.get("temperatureC")
        if ts is not None and temp is not None:
            timestamps.append(_parse_dt(str(ts)))
            values.append(float(temp))

    if not timestamps:
        raise ValueError("No temperature data available for plot.")

    temp_config = config["thresholds"]["temperature"]
    warning_max = float(temp_config["warningMax"])
    critical_max = float(temp_config["criticalMax"])

    y_min = min(min(values) - 2, warning_max - 5)
    y_max = max(max(values) + 2, critical_max + 3)

    fig, ax = plt.subplots(figsize=(10, 4))

    # Shaded threshold zones
    ax.axhspan(warning_max, critical_max, alpha=0.12, color="yellow")
    ax.axhspan(critical_max, y_max + 10, alpha=0.12, color="red")
    ax.axhline(
        warning_max, color="orange", linestyle="--", linewidth=1, alpha=0.8,
        label=f"Warning {warning_max:.0f}C",
    )
    ax.axhline(
        critical_max, color="red", linestyle="--", linewidth=1, alpha=0.8,
        label=f"Critical {critical_max:.0f}C",
    )

    ax.plot(timestamps, values, "b-o", markersize=4, linewidth=1.5, label="Temp (C)")

    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Temperature (°C)")

    window_label = _window_label(since_minutes)
    ax.set_title(f"Temperature — last {window_label} ({bucket_minutes}-min buckets)")

    # Adaptive x-axis format: show date+hour for ranges longer than 24 h
    if since_minutes > 24 * 60:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=45)
    ax.set_ylim(y_min, y_max)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    fig.savefig(tmp.name, dpi=100, bbox_inches="tight")
    plt.close(fig)

    return Path(tmp.name)
