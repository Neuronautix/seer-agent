#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = SCRIPT_DIR.parent / "logs" / "validated-observations.jsonl"

DEFAULT_CONFIG: dict[str, Any] = {
    "thresholds": {
        "temperature": {
            "metric": "temperatureC",
            "unit": "C",
            "warningMax": 28.0,
            "criticalMax": 35.0,
        },
        "humidity": {
            "metric": "humidityPct",
            "unit": "%",
            "warningMax": 70.0,
            "criticalMax": 85.0,
        },
        "pressure": {
            "metric": "pressureHpa",
            "unit": "hPa",
            "warningMin": 980.0,
            "warningMax": 1035.0,
            "criticalMin": 960.0,
            "criticalMax": 1060.0,
        },
    }
}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path is None:
        return config

    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    thresholds = loaded.get("thresholds")
    if isinstance(thresholds, dict):
        for metric_name, metric_config in thresholds.items():
            if not isinstance(metric_config, dict):
                raise ValueError(f"threshold config for {metric_name} must be an object")
            existing = config.setdefault("thresholds", {}).get(metric_name, {})
            if not isinstance(existing, dict):
                existing = {}
            config["thresholds"][metric_name] = {**existing, **metric_config}

    for key, value in loaded.items():
        if key != "thresholds":
            config[key] = value

    return config


def read_latest_observation(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        raise FileNotFoundError(f"validated observation log not found: {log_path}")

    latest_line: str | None = None
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                latest_line = stripped

    if latest_line is None:
        raise ValueError("validated observation log is empty")

    try:
        observation = json.loads(latest_line)
    except json.JSONDecodeError as exc:
        raise ValueError("latest validated observation is not valid JSON") from exc

    if not isinstance(observation, dict):
        raise ValueError("latest validated observation must be a JSON object")

    return observation


def _metric_config(config: dict[str, Any], metric_name: str) -> dict[str, Any]:
    thresholds = config.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("config.thresholds must be an object")

    metric_config = thresholds.get(metric_name)
    if not isinstance(metric_config, dict):
        raise ValueError(f"missing threshold config for {metric_name}")
    return metric_config


def _metric_status(value: float, metric_config: dict[str, Any]) -> str:
    critical_min = float(metric_config.get("criticalMin", float("-inf")))
    critical_max = float(metric_config.get("criticalMax", float("inf")))
    warning_min = float(metric_config.get("warningMin", float("-inf")))
    warning_max = float(metric_config.get("warningMax", float("inf")))
    if value <= critical_min or value >= critical_max:
        return "critical"
    if value <= warning_min or value >= warning_max:
        return "warning"
    return "normal"


def _read_metric_payload(observation: dict[str, Any], config: dict[str, Any], metric_name: str) -> dict[str, Any]:
    metric_config = _metric_config(config, metric_name)
    observation_key = metric_config.get("metric")
    if not isinstance(observation_key, str) or observation_key not in observation:
        raise ValueError(f"latest observation missing {metric_name} metric")

    value = float(observation[observation_key])
    return {
        "ok": True,
        "action": "read_latest",
        "metric": metric_name,
        "value": value,
        "unit": metric_config["unit"],
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
    }


def handle_read_latest(log_path: Path, config: dict[str, Any], metric_name: str) -> dict[str, Any]:
    observation = read_latest_observation(log_path)
    return _read_metric_payload(observation, config, metric_name)


def handle_get_threshold_status(log_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    observation = read_latest_observation(log_path)
    response = {
        "ok": True,
        "action": "get_threshold_status",
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
        "thresholdStatus": {},
    }

    for metric_name in ("temperature", "humidity", "pressure"):
        metric_config = _metric_config(config, metric_name)
        observation_key = metric_config.get("metric")
        if not isinstance(observation_key, str) or observation_key not in observation:
            response["thresholdStatus"][metric_name] = {
                "available": False,
                "unit": metric_config["unit"],
                "status": "unavailable",
            }
            continue

        value = float(observation[observation_key])
        response["thresholdStatus"][metric_name] = {
            "available": True,
            "value": value,
            "unit": metric_config["unit"],
            "status": _metric_status(value, metric_config),
            "thresholds": {key: float(limit) for key, limit in metric_config.items() if key not in {"metric", "unit"}},
        }

    return response


def execute_action(action: str, subject: str | None, *, log_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    if action == "read_latest":
        if subject not in {"temperature", "humidity", "pressure"}:
            raise ValueError("read_latest requires subject temperature, humidity, or pressure")
        return handle_read_latest(log_path, config, subject)

    if action == "get_threshold_status":
        if subject is not None:
            raise ValueError("get_threshold_status does not accept a subject")
        return handle_get_threshold_status(log_path, config)

    raise ValueError(f"unsupported action: {action}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic supervisor for validated sensor observations")
    parser.add_argument("action", choices=["read_latest", "get_threshold_status"])
    parser.add_argument("subject", nargs="?", default=None)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--config", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_path = Path(args.log_file)
    config_path = Path(args.config) if args.config else None

    try:
        response = execute_action(
            args.action,
            args.subject,
            log_path=log_path,
            config=load_config(config_path),
        )
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
        json.dump(
            {
                "ok": False,
                "action": args.action,
                "subject": args.subject,
                "error": str(exc),
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 1

    json.dump(response, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())