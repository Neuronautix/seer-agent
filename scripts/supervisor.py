#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from observation_analysis import evaluate_thresholds, load_config as load_analysis_config, read_latest_observation, read_observations_in_window, read_recent_observations, summarize_window

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = SCRIPT_DIR.parent / "logs" / "validated-observations.jsonl"

DEFAULT_CONFIG: dict[str, Any] = load_analysis_config()


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    return load_analysis_config(config_path)


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
    evaluation = evaluate_thresholds(observation, config)
    return {
        "ok": True,
        "action": "get_threshold_status",
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
        **evaluation,
    }

def handle_get_alarm_status(log_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    observation = read_latest_observation(log_path)
    evaluation = evaluate_thresholds(observation, config)
    return {
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
    }


def handle_summarize_window(
    log_path: Path,
    config: dict[str, Any],
    count: int | None,
    subject: str,
    since_minutes: int | None,
    bucket_minutes: int | None,
) -> dict[str, Any]:
    if since_minutes is not None and count is not None:
        raise ValueError("summarize_window accepts either count or since_minutes, not both")

    if since_minutes is not None:
        observations = read_observations_in_window(log_path, since_minutes=since_minutes)
    else:
        resolved_count = count if count is not None else 10
        observations = read_recent_observations(log_path, count=resolved_count)

    response = summarize_window(
        observations,
        config,
        requested_count=count,
        subject=subject,
        since_minutes=since_minutes,
        bucket_minutes=bucket_minutes,
    )
    response.update(
        {
            "sensorId": observations[-1].get("sensorId"),
            "sourcePort": observations[-1].get("sourcePort"),
            "schemaVersion": observations[-1].get("schemaVersion"),
        }
    )
    return response


def execute_action(
    action: str,
    subject: str | None,
    *,
    log_path: Path,
    config: dict[str, Any],
    count: int | None = 10,
    since_minutes: int | None = None,
    bucket_minutes: int | None = None,
) -> dict[str, Any]:
    if action == "read_latest":
        if subject not in {"temperature", "humidity", "pressure"}:
            raise ValueError("read_latest requires subject temperature, humidity, or pressure")
        return handle_read_latest(log_path, config, subject)

    if action == "get_threshold_status":
        if subject is not None:
            raise ValueError("get_threshold_status does not accept a subject")
        return handle_get_threshold_status(log_path, config)

    if action == "get_alarm_status":
        if subject is not None:
            raise ValueError("get_alarm_status does not accept a subject")
        return handle_get_alarm_status(log_path, config)

    if action == "summarize_window":
        resolved_subject = (subject or "all").lower()
        return handle_summarize_window(log_path, config, count, resolved_subject, since_minutes, bucket_minutes)

    raise ValueError(f"unsupported action: {action}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic supervisor for validated sensor observations")
    parser.add_argument("action", choices=["read_latest", "get_threshold_status", "get_alarm_status", "summarize_window"])
    parser.add_argument("subject", nargs="?", default=None)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--config", default=None)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--since-minutes", type=int, default=None)
    parser.add_argument("--bucket-minutes", type=int, default=None)
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
            count=args.count,
            since_minutes=args.since_minutes,
            bucket_minutes=args.bucket_minutes,
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