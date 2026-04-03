#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT_DIR = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT_DIR / "workspace" / "tools"
SERVER = FastMCP("sovereign-sensor-tools")


def run_tool(script_name: str, *args: str) -> str:
    command = [sys.executable, str(TOOLS_DIR / script_name), *args]
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        error = result.stdout.strip() or result.stderr.strip() or f"{script_name} failed"
        return json.dumps({"ok": False, "error": error}, separators=(",", ":"))

    return result.stdout.strip()


@SERVER.tool()
def get_health() -> str:
    """Return the sensor system health and data freshness.

    Checks whether the latest sensor reading is fresh (updated within the last
    5 minutes). Returns status ('ready', 'stale', or 'waiting_for_data'),
    lastObservationAt, freshnessAgeSeconds, and isFresh flag.

    Call this first to confirm the sensor is online before running other queries.
    """
    return run_tool("get_health.py")


@SERVER.tool()
def get_latest_observation() -> str:
    """Return the most recent validated sensor observation.

    Returns the full SensorObservation object including temperatureC (Celsius),
    humidityPct (%), pressureHpa (hPa, if available), observedAt (UTC ISO-8601),
    sensorId, and sourcePort.

    Use this when asked for the current reading or when you need all metrics at once.
    """
    return run_tool("get_latest_observation.py")


@SERVER.tool()
def get_metric(metric: str) -> str:
    """Return a single metric value from the latest sensor observation.

    Parameters:
    - metric: One of 'temperature', 'humidity', or 'pressure'.

    Returns value, unit, and observedAt. Use this for quick single-value lookups
    (e.g. "what is the current temperature?").
    """
    normalized_metric = metric.strip().lower()
    if normalized_metric not in {"temperature", "humidity", "pressure"}:
        raise ValueError("metric must be temperature, humidity, or pressure")
    return run_tool("get_metric.py", normalized_metric)


@SERVER.tool()
def get_threshold_status() -> str:
    """Return the current threshold evaluation for all sensor metrics.

    Shows whether temperature, humidity, and pressure are within normal, warning,
    or critical ranges, along with the current value, unit, and threshold limits
    for each metric.

    Use this to report overall sensor health or check whether any metric is
    approaching an alarm threshold.
    """
    return run_tool("get_threshold_status.py")


@SERVER.tool()
def get_alarm_status() -> str:
    """Return the current alarm status across all sensor metrics.

    Returns overallStatus ('normal', 'warning', or 'critical'), hasActiveAlarms
    (bool), and a list of activeAlarms with metric name, value, unit, and severity.

    Use this when asked whether any alarms are active, or before sending an
    alert summary.
    """
    return run_tool("get_alarm_status.py")


@SERVER.tool()
def summarize_window(
    count: int | None = 10,
    subject: str = "all",
    since_minutes: int | None = None,
    bucket_minutes: int | None = None,
) -> str:
    """Summarize sensor observations over a time window (aggregate statistics).

    Returns min, max, average, latest value, delta, and status counts for the
    selected metric(s). Use this to report trends or ranges — not raw data points.

    Parameters:
    - count: Number of recent observations to include (default 10). Mutually
      exclusive with since_minutes.
    - subject: Which metric to summarize: 'all', 'temperature', 'humidity', or
      'pressure' (default 'all').
    - since_minutes: Include observations from the last N minutes. Mutually
      exclusive with count.
    - bucket_minutes: Group observations into N-minute buckets before summarising
      (e.g. 5 for 5-minute averages).
    """
    normalized_subject = subject.strip().lower()
    if normalized_subject not in {"all", "temperature", "humidity", "pressure"}:
        raise ValueError("subject must be all, temperature, humidity, or pressure")
    if count is not None and count < 1:
        raise ValueError("count must be at least 1")
    if since_minutes is not None and since_minutes < 1:
        raise ValueError("since_minutes must be at least 1")
    if bucket_minutes is not None and bucket_minutes < 1:
        raise ValueError("bucket_minutes must be at least 1")
    if since_minutes is not None and count is not None:
        raise ValueError("choose either count or since_minutes")

    args = ["--subject", normalized_subject]
    if since_minutes is not None:
        args.extend(["--since-minutes", str(since_minutes)])
    else:
        args.extend(["--count", str(10 if count is None else count)])
    if bucket_minutes is not None:
        args.extend(["--bucket-minutes", str(bucket_minutes)])
    return run_tool("summarize_window.py", *args)


@SERVER.tool()
def get_temperature_history(
    since_minutes: int = 60,
    bucket_minutes: int = 5,
    subject: str = "all",
) -> str:
    """Return a time-series of bucketed sensor observations for trend analysis or charting.

    Unlike summarize_window (which returns aggregate statistics), this returns the
    raw data points so you can describe how the temperature evolved over time.

    Parameters:
    - since_minutes: How far back to look in minutes (default 60). Use 1440 for
      the last 24 hours, 10080 for the last week.
    - bucket_minutes: Bucket size in minutes (default 5). One representative
      reading is kept per bucket, giving a downsampled time-series.
    - subject: Which metrics to include in each point: 'all', 'temperature',
      'humidity', or 'pressure' (default 'all').

    Returns a 'points' array of {observedAt, temperatureC, humidityPct,
    pressureHpa} objects, plus window metadata (observedFrom, observedTo,
    pointCount).
    """
    normalized_subject = subject.strip().lower()
    if normalized_subject not in {"all", "temperature", "humidity", "pressure"}:
        raise ValueError("subject must be all, temperature, humidity, or pressure")
    if since_minutes < 1:
        raise ValueError("since_minutes must be at least 1")
    if bucket_minutes < 1:
        raise ValueError("bucket_minutes must be at least 1")

    return run_tool(
        "get_temperature_history.py",
        "--since-minutes", str(since_minutes),
        "--bucket-minutes", str(bucket_minutes),
        "--subject", normalized_subject,
    )


if __name__ == "__main__":
    SERVER.run(transport="stdio")
