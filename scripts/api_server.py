#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from alarm_runtime import handle_admin_message
from observation_analysis import evaluate_thresholds, load_config, read_recent_observations, summarize_window

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LATEST_PATH = SCRIPT_DIR.parent / "logs" / "latest-observation.json"
DEFAULT_LOG_PATH = SCRIPT_DIR.parent / "logs" / "validated-observations.jsonl"

METRICS = {
    "temperature": {"field": "temperatureC", "unit": "C"},
    "humidity": {"field": "humidityPct", "unit": "%"},
    "pressure": {"field": "pressureHpa", "unit": "hPa"},
}

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


def build_health_payload(latest_path: Path) -> dict[str, Any]:
    if latest_path.exists():
        return {"ok": True, "status": "ready", "latestObservationAvailable": True}
    return {"ok": True, "status": "waiting_for_data", "latestObservationAvailable": False}


def build_root_payload(latest_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "sovereign-sensor-agent",
        "status": build_health_payload(latest_path)["status"],
        "endpoints": [
            "/health",
            "/latest",
            "/latest/temp",
            "/latest/humidity",
            "/latest/pressure",
            "/latest/threshold-status",
            "/latest/alarm-status",
            "/summary?count=10&subject=all",
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


def build_summary_payload(log_path: Path, count: int, subject: str, config_path: Path | None) -> dict[str, Any]:
    observations = read_recent_observations(log_path, count=count)
    payload = summarize_window(observations, load_config(config_path), requested_count=count, subject=subject)
    payload.update(
        {
            "sensorId": observations[-1].get("sensorId"),
            "sourcePort": observations[-1].get("sourcePort"),
            "schemaVersion": observations[-1].get("schemaVersion"),
        }
    )
    return payload


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
        count = int(params.get("count", ["10"])[0])
        subject = params.get("subject", ["all"])[0].strip().lower()
        return HTTPStatus.OK, build_summary_payload(log_path, count=count, subject=subject, config_path=config_path)

    observation = load_latest_observation(latest_path)

    if parsed.path == "/latest":
        return HTTPStatus.OK, observation
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
        params = parse_qs("")
        del params
        count = 10
        for token in normalized.split():
            if token.isdigit():
                count = int(token)
                break
        subject = "all"
        for metric_name in METRICS:
            if metric_name in normalized:
                subject = metric_name
                break
        summary_payload = build_summary_payload(log_path, count=count, subject=subject, config_path=config_path)
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
        def do_GET(self) -> None:
            try:
                status, payload = route_request(self.path, latest_path, active_log_path, config_path)
            except (FileNotFoundError, ValueError) as exc:
                status = HTTPStatus.SERVICE_UNAVAILABLE
                payload = {"ok": False, "error": str(exc)}

            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/webhook":
                self._method_not_allowed()
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)

            try:
                status, payload = route_webhook(body, content_type, latest_path, active_log_path, config_path)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                status = HTTPStatus.BAD_REQUEST
                payload = {"ok": False, "error": str(exc)}

            response = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _method_not_allowed(self) -> None:
            body = json.dumps({"ok": False, "error": "read-only API"}, separators=(",", ":")).encode("utf-8")
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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