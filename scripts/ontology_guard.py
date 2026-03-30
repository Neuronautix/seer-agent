#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from build_observation import JSONLD_CONTEXT, OBSERVATION_TYPE, SCHEMA_VERSION, normalize_timestamp

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEMA_PATH = SCRIPT_DIR.parent / "schemas" / "sensor-observation-v1.json"


def load_schema(schema_path: Path = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _ensure_context(value: Any) -> None:
    if value != JSONLD_CONTEXT:
        raise ValueError("@context does not match the canonical live observation contract")


def _ensure_type(value: Any) -> None:
    if value != OBSERVATION_TYPE:
        raise ValueError(f"@type must be {OBSERVATION_TYPE}")


def _ensure_schema_version(value: Any) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError(f"schemaVersion must be {SCHEMA_VERSION}")


def _ensure_observed_at(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("observedAt must be a string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observedAt must be ISO-8601") from exc
    if normalize_timestamp(value) != value:
        raise ValueError("observedAt must be canonical UTC ISO-8601 ending in Z")


def _ensure_range(name: str, value: Any, lower: float, upper: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if numeric < lower or numeric > upper:
        raise ValueError(f"{name} outside allowed range {lower}..{upper}")


def _format_validation_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if not path:
        return error.message
    return f"{path}: {error.message}"


def validate_observation(
    observation: Mapping[str, Any], schema: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    active_schema = dict(schema or load_schema())
    validator = Draft202012Validator(active_schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(observation), key=lambda item: list(item.absolute_path))
    if errors:
        raise ValueError(_format_validation_error(errors[0]))

    _ensure_context(observation.get("@context"))
    _ensure_type(observation.get("@type"))
    _ensure_schema_version(observation.get("schemaVersion"))
    _ensure_observed_at(observation.get("observedAt"))
    _ensure_range("temperatureC", observation.get("temperatureC"), -40.0, 85.0)
    _ensure_range("humidityPct", observation.get("humidityPct"), 0.0, 100.0)
    if "pressureHpa" in observation:
        _ensure_range("pressureHpa", observation.get("pressureHpa"), 300.0, 1100.0)
    return dict(observation)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate canonical sensor observations")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observation = json.load(sys.stdin)
        validated = validate_observation(observation, load_schema(Path(args.schema)))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ontology_guard: {exc}", file=sys.stderr)
        return 1

    json.dump(validated, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())