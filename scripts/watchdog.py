#!/usr/bin/env python3
"""
Freshness watchdog for the sovereign sensor ingest pipeline.

Checks whether the latest validated observation is within the freshness
threshold. Writes a machine-readable status to logs/watchdog-status.json
for use by `ssa health` and other tooling.

Exit codes:
  0  — observation is fresh
  1  — observation is stale or missing
  2  — unexpected error
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
DEFAULT_STATUS_PATH = ROOT_DIR / "logs" / "watchdog-status.json"
DEFAULT_FRESHNESS_THRESHOLD = 300  # seconds


def check_freshness(
    latest_path: Path,
    threshold_seconds: int = DEFAULT_FRESHNESS_THRESHOLD,
) -> dict:
    now = datetime.now(tz=timezone.utc)
    checked_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not latest_path.exists():
        return {
            "ok": False,
            "checkedAt": checked_at,
            "status": "no_data",
            "message": f"observation file not found: {latest_path}",
        }

    try:
        obs = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "checkedAt": checked_at,
            "status": "error",
            "message": f"observation file is not valid JSON: {exc}",
        }

    observed_at = obs.get("observedAt")
    if not observed_at:
        return {
            "ok": False,
            "checkedAt": checked_at,
            "status": "error",
            "message": "observation missing observedAt field",
        }

    try:
        last_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        return {
            "ok": False,
            "checkedAt": checked_at,
            "status": "error",
            "message": f"invalid observedAt timestamp: {exc}",
        }

    age_seconds = int((now - last_dt).total_seconds())
    is_fresh = age_seconds <= threshold_seconds

    return {
        "ok": is_fresh,
        "checkedAt": checked_at,
        "status": "fresh" if is_fresh else "stale",
        "lastObservationAt": observed_at,
        "freshnessAgeSeconds": age_seconds,
        "freshnessThresholdSeconds": threshold_seconds,
        "isFresh": is_fresh,
        "sensorId": obs.get("sensorId"),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freshness watchdog for sensor ingest")
    parser.add_argument("--latest-file", default=str(DEFAULT_LATEST_PATH))
    parser.add_argument("--status-file", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--threshold", type=int, default=DEFAULT_FRESHNESS_THRESHOLD,
                        help="Freshness threshold in seconds (default: 300)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress stderr output; rely on exit code only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    latest_path = Path(args.latest_file)
    status_path = Path(args.status_file)

    try:
        result = check_freshness(latest_path, threshold_seconds=args.threshold)
    except Exception as exc:
        result = {
            "ok": False,
            "checkedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "error",
            "message": str(exc),
        }

    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        if not args.quiet:
            print(f"watchdog: could not write status file: {exc}", file=sys.stderr)

    if not result["ok"] and not args.quiet:
        msg = result.get("message") or f"{result.get('freshnessAgeSeconds', '?')}s since last observation"
        print(f"watchdog: {result['status']} — {msg}", file=sys.stderr)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
