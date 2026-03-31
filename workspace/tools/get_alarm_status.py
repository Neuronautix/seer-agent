#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from observation_analysis import evaluate_thresholds, load_config
from get_latest_observation import load_latest_observation

DEFAULT_CONFIG = load_config()


def main() -> int:
    try:
        observation = load_latest_observation()
        evaluation = evaluate_thresholds(observation, DEFAULT_CONFIG)
        json.dump(
            {
                "ok": True,
                "action": "get_alarm_status",
                "observedAt": observation.get("observedAt"),
                "sensorId": observation.get("sensorId"),
                "sourcePort": observation.get("sourcePort"),
                "schemaVersion": observation.get("schemaVersion"),
                "overallStatus": evaluation["overallStatus"],
                "hasActiveAlarms": evaluation["hasActiveAlarms"],
                "activeAlarms": evaluation["activeAlarms"],
                "thresholdStatus": evaluation["thresholdStatus"],
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())