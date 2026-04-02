#!/usr/bin/env python3
"""Return system health: sensor freshness and service readiness."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
FRESHNESS_THRESHOLD_SECONDS = 300


def main() -> int:
    try:
        if not DEFAULT_LATEST_PATH.exists():
            json.dump(
                {"ok": True, "status": "waiting_for_data", "latestObservationAvailable": False},
                sys.stdout, separators=(",", ":"),
            )
            sys.stdout.write("\n")
            return 0

        observation = json.loads(DEFAULT_LATEST_PATH.read_text(encoding="utf-8"))
        observed_at = observation.get("observedAt") if isinstance(observation, dict) else None

        if observed_at:
            last_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            age_seconds = int((datetime.now(tz=timezone.utc) - last_dt).total_seconds())
            is_fresh = age_seconds <= FRESHNESS_THRESHOLD_SECONDS
            result = {
                "ok": True,
                "status": "ready" if is_fresh else "stale",
                "latestObservationAvailable": True,
                "lastObservationAt": observed_at,
                "freshnessAgeSeconds": age_seconds,
                "isFresh": is_fresh,
                "sensorId": observation.get("sensorId"),
                "sourcePort": observation.get("sourcePort"),
            }
        else:
            result = {"ok": True, "status": "ready", "latestObservationAvailable": True}

        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
