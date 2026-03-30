#!/usr/bin/env python3

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import termios
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, TextIO

from build_observation import DEFAULT_DEVICE, DEFAULT_SENSOR_ID, build_observation
from ontology_guard import DEFAULT_SCHEMA_PATH, load_schema, validate_observation

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = SCRIPT_DIR.parent / "logs" / "validated-observations.jsonl"
DEFAULT_LATEST_PATH = SCRIPT_DIR.parent / "logs" / "latest-observation.json"
DEFAULT_REJECTION_LOG_PATH = SCRIPT_DIR.parent / "logs" / "rejected-lines.jsonl"

BAUD_RATES = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

HUMAN_READABLE_FIELD_PATTERNS = {
    "temperature_c": re.compile(r"^Temperature\s*=\s*([-+]?\d+(?:\.\d+)?)\s*(?:°?C)?\s*$", re.IGNORECASE),
    "humidity_pct": re.compile(r"^Humidity\s*=\s*([-+]?\d+(?:\.\d+)?)\s*%?\s*$", re.IGNORECASE),
    "pressure_hpa": re.compile(r"^Pressure\s*=\s*([-+]?\d+(?:\.\d+)?)\s*(?:hPa)?\s*$", re.IGNORECASE),
}


def set_serial_baud_rate(attributes: list[Any], baud_rate: int) -> None:
    baud_flag = BAUD_RATES[baud_rate]
    set_input_speed = getattr(termios, "cfsetispeed", None)
    set_output_speed = getattr(termios, "cfsetospeed", None)
    if callable(set_input_speed) and callable(set_output_speed):
        set_input_speed(attributes, baud_flag)
        set_output_speed(attributes, baud_flag)
        return

    attributes[4] = baud_flag
    attributes[5] = baud_flag


def parse_sensor_line(line: str) -> dict[str, Any]:
    stripped = line.strip()
    if not stripped:
        raise ValueError("empty line")

    fields: dict[str, str] = {}
    for part in stripped.split(";"):
        if not part:
            continue
        key, separator, value = part.partition("=")
        if separator != "=" or not key or not value:
            raise ValueError(f"malformed field: {part!r}")
        normalized_key = key.strip().upper()
        if normalized_key in fields:
            raise ValueError(f"duplicate field: {normalized_key}")
        fields[normalized_key] = value.strip()

    required = {"TEMP", "HUM", "TS"}
    missing = sorted(required.difference(fields))
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    allowed = set(required) | {"PRESS"}
    extra = sorted(set(fields).difference(allowed))
    if extra:
        raise ValueError(f"unexpected fields: {', '.join(extra)}")

    if "PRESS" in fields:
        return {
            "temperature_c": float(fields["TEMP"]),
            "humidity_pct": float(fields["HUM"]),
            "pressure_hpa": float(fields["PRESS"]),
            "timestamp": fields["TS"],
        }

    return {
        "temperature_c": float(fields["TEMP"]),
        "humidity_pct": float(fields["HUM"]),
        "timestamp": fields["TS"],
    }


def parse_human_readable_sensor_field(line: str) -> tuple[str, float] | None:
    stripped = line.strip()
    if not stripped:
        return None

    for field_name, pattern in HUMAN_READABLE_FIELD_PATTERNS.items():
        match = pattern.match(stripped)
        if match is not None:
            return field_name, float(match.group(1))

    return None


def append_jsonl(log_path: Path, observation: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        json.dump(observation, handle, separators=(",", ":"))
        handle.write("\n")
        handle.flush()


def write_latest_observation(latest_path: Path, observation: dict[str, Any]) -> None:
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    with latest_path.open("w", encoding="utf-8") as handle:
        json.dump(observation, handle, separators=(",", ":"))
        handle.write("\n")


def log_rejected_line(
    *,
    raw_line: str,
    error: str,
    device: str,
    stderr: TextIO,
    rejection_log_path: Path | None,
) -> None:
    event = {
        "loggedAt": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "event": "rejected_sensor_line",
        "device": device,
        "error": error,
        "rawLine": raw_line.rstrip("\r\n"),
    }
    if rejection_log_path is not None:
        append_jsonl(rejection_log_path, event)
    print(json.dumps(event, separators=(",", ":")), file=stderr)


def process_line(
    raw_line: str,
    *,
    device: str,
    sensor_id: str,
    log_path: Path,
    latest_path: Path,
    rejection_log_path: Path | None,
    schema: dict[str, Any],
    stderr: TextIO,
    stdout: TextIO | None = None,
) -> bool:
    try:
        payload = parse_sensor_line(raw_line)
        payload["device"] = device
        payload["sensor_id"] = sensor_id
        observation = build_observation(payload)
        validated = validate_observation(observation, schema)
    except ValueError as exc:
        log_rejected_line(
            raw_line=raw_line,
            error=str(exc),
            device=device,
            stderr=stderr,
            rejection_log_path=rejection_log_path,
        )
        return False

    append_jsonl(log_path, validated)
    write_latest_observation(latest_path, validated)
    target_stdout = stdout if stdout is not None else sys.stdout
    print(json.dumps(validated, separators=(",", ":")), file=target_stdout, flush=True)
    return True


def process_payload(
    payload: dict[str, Any],
    *,
    device: str,
    sensor_id: str,
    log_path: Path,
    latest_path: Path,
    rejection_log_path: Path | None,
    schema: dict[str, Any],
    stderr: TextIO,
    stdout: TextIO | None = None,
) -> bool:
    try:
        payload["device"] = device
        payload["sensor_id"] = sensor_id
        observation = build_observation(payload)
        validated = validate_observation(observation, schema)
    except ValueError as exc:
        raw_line = json.dumps(payload, separators=(",", ":"))
        log_rejected_line(
            raw_line=raw_line,
            error=str(exc),
            device=device,
            stderr=stderr,
            rejection_log_path=rejection_log_path,
        )
        return False

    append_jsonl(log_path, validated)
    write_latest_observation(latest_path, validated)
    target_stdout = stdout if stdout is not None else sys.stdout
    print(json.dumps(validated, separators=(",", ":")), file=target_stdout, flush=True)
    return True


def ingest_stream(
    stream: Iterable[str],
    *,
    device: str,
    sensor_id: str,
    log_path: Path,
    latest_path: Path,
    rejection_log_path: Path | None,
    schema: dict[str, Any],
    stderr: TextIO,
    stdout: TextIO | None = None,
    max_lines: int | None = None,
) -> int:
    processed = 0
    partial_payload: dict[str, Any] = {}

    for raw_line in stream:
        try:
            payload = parse_sensor_line(raw_line)
        except ValueError:
            parsed_field = parse_human_readable_sensor_field(raw_line)
            stripped = raw_line.strip()

            if parsed_field is not None:
                field_name, value = parsed_field
                partial_payload[field_name] = value
            elif not stripped:
                if "temperature_c" in partial_payload and "humidity_pct" in partial_payload:
                    partial_payload["timestamp"] = datetime.now(tz=UTC).isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    )
                    if process_payload(
                        dict(partial_payload),
                        device=device,
                        sensor_id=sensor_id,
                        log_path=log_path,
                        latest_path=latest_path,
                        rejection_log_path=rejection_log_path,
                        schema=schema,
                        stderr=stderr,
                        stdout=stdout,
                    ):
                        pass
                    partial_payload.clear()
                elif partial_payload:
                    log_rejected_line(
                        raw_line=raw_line,
                        error="incomplete human-readable sensor block",
                        device=device,
                        stderr=stderr,
                        rejection_log_path=rejection_log_path,
                    )
                    partial_payload.clear()
            else:
                log_rejected_line(
                    raw_line=raw_line,
                    error="missing fields: HUM, PRESS, TEMP, TS",
                    device=device,
                    stderr=stderr,
                    rejection_log_path=rejection_log_path,
                )
        else:
            if process_payload(
                payload,
                device=device,
                sensor_id=sensor_id,
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=schema,
                stderr=stderr,
                stdout=stdout,
            ):
                pass
            partial_payload.clear()

        processed += 1
        if max_lines is not None and processed >= max_lines:
            break
    return processed


def open_serial_stream(device: str, baud_rate: int) -> TextIO:
    if baud_rate not in BAUD_RATES:
        raise ValueError(f"unsupported baud rate: {baud_rate}")

    descriptor = os.open(device, os.O_RDONLY | os.O_NOCTTY)
    attributes = termios.tcgetattr(descriptor)
    attributes[0] = 0
    attributes[1] = 0
    attributes[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attributes[3] = 0
    set_serial_baud_rate(attributes, baud_rate)
    termios.tcsetattr(descriptor, termios.TCSANOW, attributes)

    return io.TextIOWrapper(
        os.fdopen(descriptor, "rb", buffering=0),
        encoding="utf-8",
        errors="replace",
        newline="\n",
        line_buffering=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read deterministic sensor data from a serial device")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--sensor-id", default=DEFAULT_SENSOR_ID)
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--latest-file", default=str(DEFAULT_LATEST_PATH))
    parser.add_argument("--reject-log-file", default=str(DEFAULT_REJECTION_LOG_PATH))
    parser.add_argument("--stdin", action="store_true", help="Read from stdin instead of the serial device")
    parser.add_argument("--max-lines", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    schema = load_schema(Path(args.schema))
    log_path = Path(args.log_file)
    latest_path = Path(args.latest_file)
    rejection_log_path = Path(args.reject_log_file) if args.reject_log_file else None

    try:
        if args.stdin:
            ingest_stream(
                sys.stdin,
                device=args.device,
                sensor_id=args.sensor_id,
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=schema,
                stderr=sys.stderr,
                max_lines=args.max_lines,
            )
            return 0

        with open_serial_stream(args.device, args.baud) as stream:
            ingest_stream(
                stream,
                device=args.device,
                sensor_id=args.sensor_id,
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=schema,
                stderr=sys.stderr,
                max_lines=args.max_lines,
            )
            return 0
    except (OSError, ValueError) as exc:
        print(f"read_serial: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())