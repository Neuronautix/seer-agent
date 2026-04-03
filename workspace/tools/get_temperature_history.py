#!/usr/bin/env python3
"""Return a bucketed time-series of sensor observations for trend analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from observation_analysis import bucket_observations, read_observations_in_window

DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"

HISTORY_FIELDS: dict[str, set[str]] = {
    "all": {"temperatureC", "humidityPct", "pressureHpa"},
    "temperature": {"temperatureC"},
    "humidity": {"humidityPct"},
    "pressure": {"pressureHpa"},
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return bucketed sensor time-series for charting or trend analysis"
    )
    parser.add_argument("--since-minutes", type=int, default=60,
                        help="How many minutes back to include (default 60)")
    parser.add_argument("--bucket-minutes", type=int, default=5,
                        help="Bucket size in minutes — one data point per bucket (default 5)")
    parser.add_argument("--subject",
                        choices=["all", "temperature", "humidity", "pressure"],
                        default="all",
                        help="Which metrics to include in each point (default all)")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observations = read_observations_in_window(Path(args.log_file), args.since_minutes)
        bucketed = bucket_observations(observations, args.bucket_minutes)

        fields = HISTORY_FIELDS[args.subject]
        points = []
        for obs in bucketed:
            point: dict = {"observedAt": obs.get("observedAt")}
            for field in ("temperatureC", "humidityPct", "pressureHpa"):
                if field in fields and obs.get(field) is not None:
                    point[field] = obs[field]
            points.append(point)

        last = bucketed[-1] if bucketed else {}
        result = {
            "ok": True,
            "action": "get_temperature_history",
            "window": {
                "sinceMinutes": args.since_minutes,
                "bucketMinutes": args.bucket_minutes,
                "observedFrom": bucketed[0].get("observedAt") if bucketed else None,
                "observedTo": last.get("observedAt"),
                "pointCount": len(points),
            },
            "subject": args.subject,
            "sensorId": last.get("sensorId"),
            "sourcePort": last.get("sourcePort"),
            "schemaVersion": last.get("schemaVersion"),
            "points": points,
        }
        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0

    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
