#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"

THRESHOLDS: dict[str, dict[str, float | str]] = {
    "temperature": {
        "field": "temperatureC",
        "unit": "C",
        "warningMax": 28.0,
        "criticalMax": 35.0,
    },
    "humidity": {
        "field": "humidityPct",
        "unit": "%",
        "warningMax": 70.0,
        "criticalMax": 85.0,
    },
    "pressure": {
        "field": "pressureHpa",
        "unit": "hPa",
        "warningMin": 980.0,
        "warningMax": 1035.0,
        "criticalMin": 960.0,
        "criticalMax": 1060.0,
    },
}


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("latest observation must be a JSON object")
    return payload


def _read_last_jsonl_record(path: Path) -> dict[str, Any]:
    latest_line: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                latest_line = stripped

    if latest_line is None:
        raise ValueError("validated observation log is empty")

    payload = json.loads(latest_line)
    if not isinstance(payload, dict):
        raise ValueError("latest validated observation must be a JSON object")
    return payload


def load_latest_observation(
    latest_path: Path = DEFAULT_LATEST_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
) -> dict[str, Any]:
    if latest_path.exists():
        return _read_json_file(latest_path)
    if log_path.exists():
        return _read_last_jsonl_record(log_path)
    raise FileNotFoundError("no validated observation file is available")


def metric_status(value: float, config: dict[str, float | str]) -> str:
    critical_min = float(config.get("criticalMin", float("-inf")))
    critical_max = float(config.get("criticalMax", float("inf")))
    warning_min = float(config.get("warningMin", float("-inf")))
    warning_max = float(config.get("warningMax", float("inf")))

    if value <= critical_min or value >= critical_max:
        return "critical"
    if value <= warning_min or value >= warning_max:
        return "warning"
    return "normal"


def main() -> int:
    try:
        observation = load_latest_observation()
        threshold_status: dict[str, Any] = {}
        for metric_name, config in THRESHOLDS.items():
            field = str(config["field"])
            if field not in observation:
                threshold_status[metric_name] = {
                    "available": False,
                    "unit": config["unit"],
                    "status": "unavailable",
                }
                continue
            value = float(observation[field])
            threshold_status[metric_name] = {
                "available": True,
                "value": value,
                "unit": config["unit"],
                "status": metric_status(value, config),
                "thresholds": {key: threshold for key, threshold in config.items() if key not in {"field", "unit"}},
            }

        json.dump(
            {
                "ok": True,
                "observedAt": observation.get("observedAt"),
                "sensorId": observation.get("sensorId"),
                "sourcePort": observation.get("sourcePort"),
                "schemaVersion": observation.get("schemaVersion"),
                "thresholdStatus": threshold_status,
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except (FileNotFoundError, KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())