#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DEVICE = "/dev/ttyUSB0"
DEFAULT_SENSOR_ID = "arduino-ttyUSB0"
OBSERVATION_TYPE = "SensorObservation"
SCHEMA_VERSION = "sensor-observation-v1"

JSONLD_CONTEXT = {
    "@vocab": "https://sovereign-sensor-agent.local/ontology#",
    "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
    "sensorId": "https://schema.org/identifier",
    "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
    "observedAt": "https://schema.org/observationDate",
    "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
    "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
    "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa",
}


def normalize_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")

    if not isinstance(value, str):
        raise ValueError("timestamp must be a string or number")

    stripped = value.strip()
    if not stripped:
        raise ValueError("timestamp is empty")

    if stripped.isdigit():
        return normalize_timestamp(int(stripped))

    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unsupported timestamp format: {value!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat().replace("+00:00", "Z")


def _to_float(name: str, value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc

    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")

    return numeric


def build_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    temperature = _to_float("temperature_c", payload.get("temperature_c"))
    humidity = _to_float("humidity_pct", payload.get("humidity_pct"))
    pressure = _to_float("pressure_hpa", payload.get("pressure_hpa"))
    observed_at = normalize_timestamp(payload.get("timestamp"))
    sensor_id = str(payload.get("sensor_id") or DEFAULT_SENSOR_ID).strip()
    device = str(payload.get("device") or DEFAULT_DEVICE).strip()

    if not sensor_id:
        raise ValueError("sensor_id is required")
    if not device.startswith("/"):
        raise ValueError("device must be an absolute path")

    return {
        "@context": dict(JSONLD_CONTEXT),
        "@type": OBSERVATION_TYPE,
        "schemaVersion": SCHEMA_VERSION,
        "sensorId": sensor_id,
        "sourcePort": device,
        "observedAt": observed_at,
        "temperatureC": temperature,
        "humidityPct": humidity,
        "pressureHpa": pressure,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical sensor observation JSON-LD")
    parser.add_argument("--sensor-id", default=DEFAULT_SENSOR_ID)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        payload.setdefault("sensor_id", args.sensor_id)
        payload.setdefault("device", args.device)
        observation = build_observation(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"build_observation: {exc}", file=sys.stderr)
        return 1

    json.dump(observation, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())