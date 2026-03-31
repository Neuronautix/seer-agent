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
def get_latest_observation() -> str:
    return run_tool("get_latest_observation.py")


@SERVER.tool()
def get_metric(metric: str) -> str:
    normalized_metric = metric.strip().lower()
    if normalized_metric not in {"temperature", "humidity", "pressure"}:
        raise ValueError("metric must be temperature, humidity, or pressure")
    return run_tool("get_metric.py", normalized_metric)


@SERVER.tool()
def get_threshold_status() -> str:
    return run_tool("get_threshold_status.py")


@SERVER.tool()
def get_alarm_status() -> str:
    return run_tool("get_alarm_status.py")


@SERVER.tool()
def summarize_window(
    count: int | None = 10,
    subject: str = "all",
    since_minutes: int | None = None,
    bucket_minutes: int | None = None,
) -> str:
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


if __name__ == "__main__":
    SERVER.run(transport="stdio")