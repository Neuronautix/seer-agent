#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from alarm_runtime import handle_admin_message
from observation_analysis import bucket_observations, evaluate_thresholds, load_config, read_observations_in_window, read_recent_observations, summarize_window

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LATEST_PATH = SCRIPT_DIR.parent / "logs" / "latest-observation.json"
DEFAULT_LOG_PATH = SCRIPT_DIR.parent / "logs" / "validated-observations.jsonl"

METRICS = {
    "temperature": {"field": "temperatureC", "unit": "C"},
    "humidity": {"field": "humidityPct", "unit": "%"},
    "pressure": {"field": "pressureHpa", "unit": "hPa"},
}

# CORS headers sent with every response so browsers and third-party apps can
# call this API without a proxy.
_CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
    ("Access-Control-Max-Age", "86400"),
]

HISTORY_FIELDS: dict[str, set[str]] = {
    "all": {"temperatureC", "humidityPct", "pressureHpa"},
    "temperature": {"temperatureC"},
    "humidity": {"humidityPct"},
    "pressure": {"pressureHpa"},
}

DEFAULT_REJECTED_PATH = SCRIPT_DIR.parent / "logs" / "rejected-lines.jsonl"

# ---------------------------------------------------------------------------
# Webhook rate limiter: max 10 requests per 10 s per client IP (in-memory)
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW = 10.0
_RATE_LIMIT_MAX = 10
_rate_limit_state: dict[str, deque] = {}
_rate_limit_lock = threading.Lock()


def _is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    with _rate_limit_lock:
        timestamps = _rate_limit_state.setdefault(client_ip, deque())
        while timestamps and timestamps[0] < now - _RATE_LIMIT_WINDOW:
            timestamps.popleft()
        if len(timestamps) >= _RATE_LIMIT_MAX:
            return True
        timestamps.append(now)
        return False

def load_latest_observation(latest_path: Path) -> dict[str, Any]:
    if not latest_path.exists():
        raise FileNotFoundError(f"latest observation file not found: {latest_path}")

    try:
        observation = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("latest observation file is not valid JSON") from exc

    if not isinstance(observation, dict):
        raise ValueError("latest observation file must contain a JSON object")
    return observation


FRESHNESS_THRESHOLD_SECONDS = 300  # 5 minutes


def build_health_payload(latest_path: Path) -> dict[str, Any]:
    if not latest_path.exists():
        return {"ok": True, "status": "waiting_for_data", "latestObservationAvailable": False}

    try:
        observation = json.loads(latest_path.read_text(encoding="utf-8"))
        observed_at = observation.get("observedAt")
        if observed_at:
            last_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            age_seconds = int((datetime.now(tz=timezone.utc) - last_dt).total_seconds())
            is_fresh = age_seconds <= FRESHNESS_THRESHOLD_SECONDS
            return {
                "ok": True,
                "status": "ready" if is_fresh else "stale",
                "latestObservationAvailable": True,
                "lastObservationAt": observed_at,
                "freshnessAgeSeconds": age_seconds,
                "isFresh": is_fresh,
            }
    except (json.JSONDecodeError, ValueError, KeyError):
        pass

    return {"ok": True, "status": "ready", "latestObservationAvailable": True}


def build_root_payload(latest_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "sovereign-sensor-agent",
        "status": build_health_payload(latest_path)["status"],
        "endpoints": [
            "/health",
            "/metrics",
            "/config/thresholds",
            "/latest",
            "/latest/temp",
            "/latest/humidity",
            "/latest/pressure",
            "/latest/threshold-status",
            "/latest/alarm-status",
            "/summary?count=10&subject=all",
            "/summary?since_minutes=30&bucket_minutes=5&subject=temperature",
            "/history?since_minutes=60&bucket_minutes=5&subject=all",
            "/export?since_minutes=1440&subject=all&format=csv",
            "/export?since_minutes=1440&subject=all&format=jsonl",
            "/diagnostics/rejected?tail=50",
            "/webhook",
        ],
    }


def build_metric_payload(observation: dict[str, Any], metric_name: str, field_name: str, unit: str) -> dict[str, Any]:
    if field_name not in observation:
        raise ValueError(f"latest observation missing {metric_name} metric")
    return {
        "ok": True,
        "metric": metric_name,
        "value": observation[field_name],
        "unit": unit,
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
    }


def build_config_payload(config_path: Path | None) -> dict[str, Any]:
    return {
        "ok": True,
        "action": "get_threshold_config",
        **load_config(config_path),
    }


def build_threshold_payload(observation: dict[str, Any], config_path: Path | None) -> dict[str, Any]:
    evaluation = evaluate_thresholds(observation, load_config(config_path))
    return {
        "ok": True,
        "action": "get_threshold_status",
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
        **evaluation,
    }


def build_alarm_payload(observation: dict[str, Any], config_path: Path | None) -> dict[str, Any]:
    evaluation = evaluate_thresholds(observation, load_config(config_path))
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


def build_summary_payload(
    log_path: Path,
    count: int | None,
    subject: str,
    config_path: Path | None,
    *,
    since_minutes: int | None = None,
    bucket_minutes: int | None = None,
) -> dict[str, Any]:
    if since_minutes is not None and count is not None:
        raise ValueError("summary accepts either count or since_minutes, not both")

    if since_minutes is not None:
        observations = read_observations_in_window(log_path, since_minutes=since_minutes)
    else:
        resolved_count = count if count is not None else 10
        observations = read_recent_observations(log_path, count=resolved_count)

    payload = summarize_window(
        observations,
        load_config(config_path),
        requested_count=count,
        subject=subject,
        since_minutes=since_minutes,
        bucket_minutes=bucket_minutes,
    )
    payload.update(
        {
            "sensorId": observations[-1].get("sensorId"),
            "sourcePort": observations[-1].get("sourcePort"),
            "schemaVersion": observations[-1].get("schemaVersion"),
        }
    )
    return payload


def build_history_payload(
    log_path: Path,
    since_minutes: int,
    bucket_minutes: int,
    subject: str,
    config_path: Path | None,
) -> dict[str, Any]:
    if subject not in HISTORY_FIELDS:
        raise ValueError("subject must be all, temperature, humidity, or pressure")
    if since_minutes < 1:
        raise ValueError("since_minutes must be at least 1")
    if bucket_minutes < 1:
        raise ValueError("bucket_minutes must be at least 1")

    observations = read_observations_in_window(log_path, since_minutes=since_minutes)
    bucketed = bucket_observations(observations, bucket_minutes)

    fields = HISTORY_FIELDS[subject]
    points: list[dict[str, Any]] = []
    for obs in bucketed:
        point: dict[str, Any] = {"observedAt": obs.get("observedAt")}
        for field in ("temperatureC", "humidityPct", "pressureHpa"):
            if field in fields and obs.get(field) is not None:
                point[field] = obs[field]
        points.append(point)

    last = bucketed[-1] if bucketed else {}
    return {
        "ok": True,
        "action": "get_history",
        "window": {
            "sinceMinutes": since_minutes,
            "bucketMinutes": bucket_minutes,
            "observedFrom": bucketed[0].get("observedAt") if bucketed else None,
            "observedTo": last.get("observedAt"),
            "pointCount": len(points),
        },
        "subject": subject,
        "sensorId": last.get("sensorId"),
        "sourcePort": last.get("sourcePort"),
        "schemaVersion": last.get("schemaVersion"),
        "points": points,
    }


def build_metrics_text(latest_path: Path, log_path: Path) -> str:
    """Return a Prometheus exposition-format text payload."""
    lines: list[str] = []

    def gauge(name: str, value: Any, help_text: str) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    try:
        obs = json.loads(latest_path.read_text(encoding="utf-8"))
        if isinstance(obs, dict):
            if (temp := obs.get("temperatureC")) is not None:
                gauge("ssa_temperature_celsius", temp, "Latest validated temperature in Celsius")
            if (hum := obs.get("humidityPct")) is not None:
                gauge("ssa_humidity_percent", hum, "Latest validated humidity in percent")
            if (press := obs.get("pressureHpa")) is not None:
                gauge("ssa_pressure_hpa", press, "Latest validated pressure in hPa")
            if (obs_at := obs.get("observedAt")):
                last_dt = datetime.fromisoformat(obs_at.replace("Z", "+00:00"))
                age = int((datetime.now(tz=timezone.utc) - last_dt).total_seconds())
                gauge("ssa_observation_age_seconds", age, "Seconds since last validated observation")
                gauge("ssa_sensor_fresh", 1 if age <= FRESHNESS_THRESHOLD_SECONDS else 0,
                      "1 if latest observation is within freshness threshold")
            from observation_analysis import evaluate_thresholds, load_config
            ev = evaluate_thresholds(obs, load_config())
            gauge("ssa_alarm_active", 1 if ev["hasActiveAlarms"] else 0,
                  "1 if any sensor metric is currently in an alarm state")
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        gauge("ssa_sensor_fresh", 0, "1 if latest observation is within freshness threshold")
        gauge("ssa_alarm_active", 0, "1 if any sensor metric is currently in an alarm state")

    obs_count = sum(1 for ln in log_path.open("r", encoding="utf-8") if ln.strip()) if log_path.exists() else 0
    gauge("ssa_total_observations", obs_count, "Total validated observations stored")

    return "\n".join(lines) + "\n"


def build_rejected_payload(rejected_path: Path, tail: int) -> dict[str, Any]:
    """Return the last `tail` rejected lines with raw content and error info."""
    if not rejected_path.exists():
        return {"ok": True, "action": "get_rejected", "count": 0, "entries": []}

    raw_lines: deque[str] = deque(maxlen=tail)
    total = 0
    with rejected_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                raw_lines.append(stripped)
                total += 1

    entries: list[dict[str, Any]] = []
    for raw in raw_lines:
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            entries.append({"raw": raw})

    return {
        "ok": True,
        "action": "get_rejected",
        "totalRejected": total,
        "returnedCount": len(entries),
        "entries": entries,
    }


def build_export_bytes(
    log_path: Path,
    since_minutes: int,
    bucket_minutes: int | None,
    subject: str,
    fmt: str,
) -> tuple[bytes, str]:
    """Return (content_bytes, content_type) for CSV or JSONL export."""
    if subject not in HISTORY_FIELDS:
        raise ValueError("subject must be all, temperature, humidity, or pressure")
    if fmt not in {"csv", "jsonl"}:
        raise ValueError("format must be csv or jsonl")

    observations = read_observations_in_window(log_path, since_minutes=since_minutes)
    if bucket_minutes is not None:
        observations = bucket_observations(observations, bucket_minutes)

    fields = HISTORY_FIELDS[subject]
    ordered_fields = ["observedAt"] + [f for f in ("temperatureC", "humidityPct", "pressureHpa") if f in fields]

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=ordered_fields, extrasaction="ignore")
        writer.writeheader()
        for obs in observations:
            row = {k: obs.get(k, "") for k in ordered_fields}
            writer.writerow(row)
        return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8"

    # jsonl
    lines = [
        json.dumps({k: obs.get(k) for k in ordered_fields}, separators=(",", ":"))
        for obs in observations
    ]
    return ("\n".join(lines) + "\n").encode("utf-8"), "application/x-ndjson; charset=utf-8"


def extract_message_text(body: bytes, content_type: str) -> str:
    if "application/json" in content_type:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("webhook payload must be a JSON object")

        candidates = [
            payload.get("text"),
            payload.get("message"),
            payload.get("body"),
        ]

        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            first_message = messages[0]
            if isinstance(first_message, dict):
                text_payload = first_message.get("text")
                if isinstance(text_payload, dict):
                    candidates.append(text_payload.get("body"))
                candidates.append(first_message.get("body"))

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        raise ValueError("webhook payload does not contain message text")

    if "application/x-www-form-urlencoded" in content_type:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=False)
        for key in ("Body", "body", "text", "message"):
            values = form.get(key)
            if values:
                candidate = values[0].strip()
                if candidate:
                    return candidate
        raise ValueError("form payload does not contain message text")

    raise ValueError("unsupported webhook content type")


def build_chat_reply(message_text: str, observation: dict[str, Any], config_path: Path | None) -> dict[str, Any]:
    normalized = " ".join(message_text.lower().split())

    if any(keyword in normalized for keyword in ("alarm", "alert", "critical", "warning")):
        payload = build_alarm_payload(observation, config_path)
        if payload["hasActiveAlarms"]:
            alarms = "; ".join(
                f"{alarm['metric']} {alarm['value']} {alarm['unit']} ({alarm['status']})"
                for alarm in payload["activeAlarms"]
            )
            reply = f"Active alarms: {alarms}. Observed at {payload['observedAt']}."
        else:
            reply = f"No active alarms. Overall status is {payload['overallStatus']}. Observed at {payload['observedAt']}."
        return {"ok": True, "reply": reply, "action": "get_alarm_status", "data": payload}

    if any(keyword in normalized for keyword in ("threshold", "status")):
        payload = build_threshold_payload(observation, config_path)
        parts = []
        for metric_name in ("temperature", "humidity", "pressure"):
            metric = payload["thresholdStatus"][metric_name]
            if metric.get("available") is False:
                parts.append(f"{metric_name} unavailable")
            else:
                parts.append(f"{metric_name} {metric['value']} {metric['unit']} ({metric['status']})")
        reply = f"Threshold status: {'; '.join(parts)}. Observed at {payload['observedAt']}."
        return {"ok": True, "reply": reply, "action": "get_threshold_status", "data": payload}

    for metric_name, config in METRICS.items():
        if metric_name in normalized:
            try:
                payload = build_metric_payload(observation, metric_name, config["field"], config["unit"])
            except ValueError:
                return {
                    "ok": True,
                    "reply": f"{metric_name.capitalize()} is currently unavailable from the latest observation.",
                    "action": "read_latest",
                    "data": {
                        "ok": False,
                        "metric": metric_name,
                        "error": f"latest observation missing {metric_name} metric",
                        "observedAt": observation.get("observedAt"),
                        "sensorId": observation.get("sensorId"),
                        "sourcePort": observation.get("sourcePort"),
                        "schemaVersion": observation.get("schemaVersion"),
                    },
                }
            reply = f"{metric_name.capitalize()} is {payload['value']} {payload['unit']} at {payload['observedAt']}."
            return {"ok": True, "reply": reply, "action": "read_latest", "data": payload}

    reply = (
        "Ask for temperature, humidity, pressure, or threshold status. "
        f"Latest observation is from {observation.get('observedAt')}."
    )
    return {"ok": True, "reply": reply, "action": "help"}


def build_summary_reply(summary_payload: dict[str, Any], subject: str) -> str:
    metric_names = [subject] if subject != "all" else ["temperature", "humidity", "pressure"]
    parts: list[str] = []
    for metric_name in metric_names:
        metric_summary = summary_payload["summary"].get(metric_name)
        if not isinstance(metric_summary, dict):
            continue
        if metric_summary.get("available") is False:
            parts.append(f"{metric_name} unavailable")
            continue
        parts.append(
            f"{metric_name} avg {metric_summary['average']} {metric_summary['unit']}, "
            f"min {metric_summary['minimum']}, max {metric_summary['maximum']}, latest {metric_summary['latest']}"
        )
    return (
        f"Summary over last {summary_payload['window']['actualCount']} observations: {'; '.join(parts)}. "
        f"Window {summary_payload['window']['observedFrom']} to {summary_payload['window']['observedTo']}."
    )


def route_request(
    path: str,
    latest_path: Path,
    log_path: Path,
    config_path: Path | None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    if parsed.path == "/":
        return HTTPStatus.OK, build_root_payload(latest_path)

    if parsed.path == "/health":
        return HTTPStatus.OK, build_health_payload(latest_path)

    if parsed.path == "/config/thresholds":
        return HTTPStatus.OK, build_config_payload(config_path)

    if parsed.path == "/summary":
        params = parse_qs(parsed.query, keep_blank_values=False)
        count = int(params["count"][0]) if "count" in params else None
        since_minutes = int(params["since_minutes"][0]) if "since_minutes" in params else None
        bucket_minutes = int(params["bucket_minutes"][0]) if "bucket_minutes" in params else None
        subject = params.get("subject", ["all"])[0].strip().lower()
        return HTTPStatus.OK, build_summary_payload(
            log_path,
            count=count,
            subject=subject,
            config_path=config_path,
            since_minutes=since_minutes,
            bucket_minutes=bucket_minutes,
        )

    if parsed.path == "/history":
        params = parse_qs(parsed.query, keep_blank_values=False)
        since_minutes = int(params.get("since_minutes", ["60"])[0])
        bucket_minutes = int(params.get("bucket_minutes", ["5"])[0])
        subject = params.get("subject", ["all"])[0].strip().lower()
        return HTTPStatus.OK, build_history_payload(
            log_path, since_minutes, bucket_minutes, subject, config_path
        )

    if parsed.path == "/diagnostics/rejected":
        params = parse_qs(parsed.query, keep_blank_values=False)
        tail = max(1, min(int(params.get("tail", ["50"])[0]), 500))
        return HTTPStatus.OK, build_rejected_payload(DEFAULT_REJECTED_PATH, tail)

    observation = load_latest_observation(latest_path)

    if parsed.path == "/latest":
        return HTTPStatus.OK, {"ok": True, "action": "get_latest", **observation}
    if parsed.path == "/latest/temp":
        return HTTPStatus.OK, build_metric_payload(observation, "temperature", "temperatureC", "C")
    if parsed.path == "/latest/humidity":
        return HTTPStatus.OK, build_metric_payload(observation, "humidity", "humidityPct", "%")
    if parsed.path == "/latest/pressure":
        return HTTPStatus.OK, build_metric_payload(observation, "pressure", "pressureHpa", "hPa")
    if parsed.path == "/latest/threshold-status":
        return HTTPStatus.OK, build_threshold_payload(observation, config_path)
    if parsed.path == "/latest/alarm-status":
        return HTTPStatus.OK, build_alarm_payload(observation, config_path)

    return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"}


def route_webhook(
    body: bytes,
    content_type: str,
    latest_path: Path,
    log_path: Path,
    config_path: Path | None,
) -> tuple[int, dict[str, Any]]:
    message_text = extract_message_text(body, content_type)
    admin_response = handle_admin_message(message_text, config_path=config_path)
    if admin_response is not None:
        return HTTPStatus.OK, admin_response

    observation = load_latest_observation(latest_path)
    normalized = " ".join(message_text.lower().split())
    if any(keyword in normalized for keyword in ("summary", "average", "avg", "recent", "last")):
        count: int | None = 10
        since_minutes: int | None = None
        bucket_minutes: int | None = None
        for token in normalized.split():
            if token.isdigit():
                numeric_value = int(token)
                if "minute" in normalized:
                    since_minutes = numeric_value
                    count = None
                else:
                    count = numeric_value
                break
        if "one data per minute" in normalized or "one reading per minute" in normalized:
            bucket_minutes = 1
        subject = "all"
        for metric_name in METRICS:
            if metric_name in normalized:
                subject = metric_name
                break
        summary_payload = build_summary_payload(
            log_path,
            count=count,
            subject=subject,
            config_path=config_path,
            since_minutes=since_minutes,
            bucket_minutes=bucket_minutes,
        )
        return HTTPStatus.OK, {
            "ok": True,
            "reply": build_summary_reply(summary_payload, subject),
            "action": "summarize_window",
            "data": summary_payload,
        }
    return HTTPStatus.OK, build_chat_reply(message_text, observation, config_path)


def make_handler(
    latest_path: Path,
    log_path: Path | None = None,
    config_path: Path | None = None,
):
    active_log_path = log_path or DEFAULT_LOG_PATH

    class ApiHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for header, value in _CORS_HEADERS:
                self.send_header(header, value)
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            for header, value in _CORS_HEADERS:
                self.send_header(header, value)
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            # /metrics — Prometheus text format, bypasses JSON envelope
            if parsed.path == "/metrics":
                try:
                    text = build_metrics_text(latest_path, active_log_path)
                    body = text.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    for header, value in _CORS_HEADERS:
                        self.send_header(header, value)
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
                return

            # /export — file download, bypasses JSON envelope
            if parsed.path == "/export":
                params = parse_qs(parsed.query, keep_blank_values=False)
                fmt = params.get("format", ["csv"])[0].strip().lower()
                since_minutes = int(params.get("since_minutes", ["1440"])[0])
                bucket_minutes_raw = params.get("bucket_minutes", [None])[0]
                bucket_minutes = int(bucket_minutes_raw) if bucket_minutes_raw else None
                subject = params.get("subject", ["all"])[0].strip().lower()
                try:
                    content, content_type = build_export_bytes(
                        active_log_path, since_minutes, bucket_minutes, subject, fmt
                    )
                    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    filename = f"sensor-export-{ts}.{fmt}"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(content)))
                    for header, value in _CORS_HEADERS:
                        self.send_header(header, value)
                    self.end_headers()
                    self.wfile.write(content)
                except (FileNotFoundError, ValueError) as exc:
                    self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
                return

            try:
                status, payload = route_request(self.path, latest_path, active_log_path, config_path)
            except (FileNotFoundError, ValueError) as exc:
                status = HTTPStatus.SERVICE_UNAVAILABLE
                payload = {"ok": False, "error": str(exc)}
            self._send_json(status, payload)

        def do_POST(self) -> None:
            if self.path != "/webhook":
                self._method_not_allowed()
                return

            client_ip = self.client_address[0]
            if _is_rate_limited(client_ip):
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"ok": False, "error": "rate limit exceeded — max 10 requests per 10 s"},
                )
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)

            try:
                status, payload = route_webhook(body, content_type, latest_path, active_log_path, config_path)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                status = HTTPStatus.BAD_REQUEST
                payload = {"ok": False, "error": str(exc)}
            self._send_json(status, payload)

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _method_not_allowed(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "read-only API"})

    return ApiHandler


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only API for validated sensor observations")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--latest-file", default=str(DEFAULT_LATEST_PATH))
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--config-file", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    latest_path = Path(args.latest_file)
    log_path = Path(args.log_file)
    config_path = Path(args.config_file) if args.config_file else None
    server = ThreadingHTTPServer((args.host, args.port), make_handler(latest_path, log_path, config_path))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())