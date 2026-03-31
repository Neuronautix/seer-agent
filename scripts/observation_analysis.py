#!/usr/bin/env python3

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "threshold-config.json"

DEFAULT_THRESHOLDS: dict[str, dict[str, float | str]] = {
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

METRIC_ORDER = ("temperature", "humidity", "pressure")
SEVERITY_RANK = {"unavailable": 0, "normal": 1, "warning": 2, "critical": 3}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    config = {"thresholds": json.loads(json.dumps(DEFAULT_THRESHOLDS))}
    resolved_path = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    if not resolved_path.exists():
        return config

    loaded = json.loads(resolved_path.read_text(encoding="utf-8"))
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
    observations = read_recent_observations(log_path, count=1)
    return observations[-1]


def read_recent_observations(log_path: Path, count: int) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be at least 1")
    if not log_path.exists():
        raise FileNotFoundError(f"validated observation log not found: {log_path}")

    recent_lines: deque[str] = deque(maxlen=count)
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                recent_lines.append(stripped)

    if not recent_lines:
        raise ValueError("validated observation log is empty")

    observations: list[dict[str, Any]] = []
    for line in recent_lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("validated observation log contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("validated observation log must contain JSON objects")
        observations.append(payload)
    return observations


def _parse_observed_at(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("validated observation missing observedAt")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def read_observations_in_window(log_path: Path, since_minutes: int) -> list[dict[str, Any]]:
    if since_minutes < 1:
        raise ValueError("since_minutes must be at least 1")
    if not log_path.exists():
        raise FileNotFoundError(f"validated observation log not found: {log_path}")

    observations: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            observations.append(payload)

    if not observations:
        raise ValueError("validated observation log is empty")

    latest_timestamp = _parse_observed_at(str(observations[-1].get("observedAt") or ""))
    window_start = latest_timestamp - timedelta(minutes=since_minutes)
    selected = [
        observation
        for observation in observations
        if observation.get("observedAt") and _parse_observed_at(str(observation.get("observedAt") or "")) >= window_start
    ]
    if not selected:
        raise ValueError("validated observation log is empty")
    return selected


def bucket_observations(observations: Iterable[dict[str, Any]], bucket_minutes: int) -> list[dict[str, Any]]:
    if bucket_minutes < 1:
        raise ValueError("bucket_minutes must be at least 1")

    buckets: dict[datetime, dict[str, Any]] = {}
    ordered_keys: list[datetime] = []
    for observation in observations:
        timestamp = _parse_observed_at(str(observation.get("observedAt") or ""))
        minute_floor = timestamp.replace(second=0, microsecond=0)
        bucket_offset = minute_floor.minute % bucket_minutes
        bucket_start = minute_floor - timedelta(minutes=bucket_offset)
        if bucket_start not in buckets:
            ordered_keys.append(bucket_start)
        buckets[bucket_start] = observation

    return [buckets[key] for key in ordered_keys]


def metric_config(config: dict[str, Any], metric_name: str) -> dict[str, Any]:
    thresholds = config.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("config.thresholds must be an object")

    selected = thresholds.get(metric_name)
    if not isinstance(selected, dict):
        raise ValueError(f"missing threshold config for {metric_name}")
    return selected


def metric_status(value: float, selected_config: dict[str, Any]) -> str:
    critical_min = float(selected_config.get("criticalMin", float("-inf")))
    critical_max = float(selected_config.get("criticalMax", float("inf")))
    warning_min = float(selected_config.get("warningMin", float("-inf")))
    warning_max = float(selected_config.get("warningMax", float("inf")))
    if value <= critical_min or value >= critical_max:
        return "critical"
    if value <= warning_min or value >= warning_max:
        return "warning"
    return "normal"


def _threshold_payload(value: float, selected_config: dict[str, Any]) -> dict[str, Any]:
    status = metric_status(value, selected_config)
    return {
        "available": True,
        "value": value,
        "unit": selected_config["unit"],
        "status": status,
        "alarm": status in {"warning", "critical"},
        "thresholds": {
            key: float(limit) for key, limit in selected_config.items() if key not in {"metric", "unit"}
        },
    }


def evaluate_thresholds(observation: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    threshold_status: dict[str, Any] = {}
    active_alarms: list[dict[str, Any]] = []
    highest_status = "unavailable"

    for metric_name in METRIC_ORDER:
        selected_config = metric_config(config, metric_name)
        field_name = selected_config.get("metric")
        if not isinstance(field_name, str) or field_name not in observation:
            threshold_status[metric_name] = {
                "available": False,
                "unit": selected_config["unit"],
                "status": "unavailable",
                "alarm": False,
            }
            continue

        value = float(observation[field_name])
        payload = _threshold_payload(value, selected_config)
        threshold_status[metric_name] = payload
        if SEVERITY_RANK[payload["status"]] > SEVERITY_RANK[highest_status]:
            highest_status = payload["status"]
        if payload["alarm"]:
            active_alarms.append(
                {
                    "metric": metric_name,
                    "status": payload["status"],
                    "value": value,
                    "unit": payload["unit"],
                    "observedAt": observation.get("observedAt"),
                }
            )

    if highest_status == "unavailable" and any(item.get("available") for item in threshold_status.values()):
        highest_status = "normal"

    return {
        "thresholdStatus": threshold_status,
        "overallStatus": highest_status,
        "hasActiveAlarms": bool(active_alarms),
        "activeAlarms": active_alarms,
    }


def summarize_window(
    observations: Iterable[dict[str, Any]],
    config: dict[str, Any],
    *,
    requested_count: int | None,
    subject: str,
    since_minutes: int | None = None,
    bucket_minutes: int | None = None,
) -> dict[str, Any]:
    observation_list = list(observations)
    if not observation_list:
        raise ValueError("validated observation log is empty")
    if subject not in {"all", *METRIC_ORDER}:
        raise ValueError("subject must be temperature, humidity, pressure, or all")

    if bucket_minutes is not None:
        observation_list = bucket_observations(observation_list, bucket_minutes)

    selected_metrics = list(METRIC_ORDER if subject == "all" else (subject,))
    summary: dict[str, Any] = {}
    overall_status = "unavailable"

    for metric_name in selected_metrics:
        selected_config = metric_config(config, metric_name)
        field_name = selected_config.get("metric")
        if not isinstance(field_name, str):
            raise ValueError(f"missing metric field config for {metric_name}")

        values: list[float] = []
        timestamps: list[str] = []
        status_counts = {"normal": 0, "warning": 0, "critical": 0}
        for observation in observation_list:
            if field_name not in observation:
                continue
            value = float(observation[field_name])
            values.append(value)
            timestamps.append(str(observation.get("observedAt") or ""))
            status_counts[metric_status(value, selected_config)] += 1

        if not values:
            summary[metric_name] = {
                "available": False,
                "unit": selected_config["unit"],
                "sampleCount": 0,
                "status": "unavailable",
            }
            continue

        latest_value = values[-1]
        latest_status = metric_status(latest_value, selected_config)
        metric_summary = {
            "available": True,
            "unit": selected_config["unit"],
            "sampleCount": len(values),
            "minimum": min(values),
            "maximum": max(values),
            "average": round(sum(values) / len(values), 3),
            "latest": latest_value,
            "delta": round(latest_value - values[0], 3),
            "status": latest_status,
            "firstObservedAt": timestamps[0] or None,
            "latestObservedAt": timestamps[-1] or None,
            "statusCounts": status_counts,
        }
        summary[metric_name] = metric_summary
        if SEVERITY_RANK[latest_status] > SEVERITY_RANK[overall_status]:
            overall_status = latest_status

    if overall_status == "unavailable" and any(item.get("available") for item in summary.values()):
        overall_status = "normal"

    return {
        "ok": True,
        "action": "summarize_window",
        "window": {
            "requestedCount": requested_count,
            "requestedSinceMinutes": since_minutes,
            "actualCount": len(observation_list),
            "subject": subject,
            "observedFrom": observation_list[0].get("observedAt"),
            "observedTo": observation_list[-1].get("observedAt"),
            "bucketMinutes": bucket_minutes,
            "sampling": "latest_per_bucket" if bucket_minutes is not None else "raw_observations",
        },
        "overallStatus": overall_status,
        "summary": summary,
    }
