#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LATEST_PATH = SCRIPT_DIR.parent / "logs" / "latest-observation.json"

METRICS = {
    "temperature": {"field": "temperatureC", "unit": "C"},
    "humidity": {"field": "humidityPct", "unit": "%"},
    "pressure": {"field": "pressureHpa", "unit": "hPa"},
}

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
            "/webhook",
        ],
    }


def build_metric_payload(observation: dict[str, Any], metric_name: str, field_name: str, unit: str) -> dict[str, Any]:
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


def build_threshold_payload(observation: dict[str, Any]) -> dict[str, Any]:
    threshold_status: dict[str, Any] = {}
    for metric_name, config in THRESHOLDS.items():
        field = str(config["field"])
        value = float(observation[field])
        threshold_status[metric_name] = {
            "value": value,
            "unit": config["unit"],
            "status": metric_status(value, config),
            "thresholds": {key: threshold for key, threshold in config.items() if key not in {"field", "unit"}},
        }

    return {
        "ok": True,
        "observedAt": observation.get("observedAt"),
        "sensorId": observation.get("sensorId"),
        "sourcePort": observation.get("sourcePort"),
        "schemaVersion": observation.get("schemaVersion"),
        "thresholdStatus": threshold_status,
    }


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


def build_chat_reply(message_text: str, observation: dict[str, Any]) -> dict[str, Any]:
    normalized = " ".join(message_text.lower().split())

    if any(keyword in normalized for keyword in ("threshold", "status", "alert")):
        payload = build_threshold_payload(observation)
        parts = []
        for metric_name in ("temperature", "humidity", "pressure"):
            metric = payload["thresholdStatus"][metric_name]
            parts.append(f"{metric_name} {metric['value']} {metric['unit']} ({metric['status']})")
        reply = f"Threshold status: {'; '.join(parts)}. Observed at {payload['observedAt']}."
        return {"ok": True, "reply": reply, "action": "get_threshold_status", "data": payload}

    for metric_name, config in METRICS.items():
        if metric_name in normalized:
            payload = build_metric_payload(observation, metric_name, config["field"], config["unit"])
            reply = f"{metric_name.capitalize()} is {payload['value']} {payload['unit']} at {payload['observedAt']}."
            return {"ok": True, "reply": reply, "action": "read_latest", "data": payload}

    reply = (
        "Ask for temperature, humidity, pressure, or threshold status. "
        f"Latest observation is from {observation.get('observedAt')}."
    )
    return {"ok": True, "reply": reply, "action": "help"}


def route_request(path: str, latest_path: Path) -> tuple[int, dict[str, Any]]:
    if path == "/":
        return HTTPStatus.OK, build_root_payload(latest_path)

    if path == "/health":
        return HTTPStatus.OK, build_health_payload(latest_path)

    observation = load_latest_observation(latest_path)

    if path == "/latest":
        return HTTPStatus.OK, observation
    if path == "/latest/temp":
        return HTTPStatus.OK, build_metric_payload(observation, "temperature", "temperatureC", "C")
    if path == "/latest/humidity":
        return HTTPStatus.OK, build_metric_payload(observation, "humidity", "humidityPct", "%")
    if path == "/latest/pressure":
        return HTTPStatus.OK, build_metric_payload(observation, "pressure", "pressureHpa", "hPa")
    if path == "/latest/threshold-status":
        return HTTPStatus.OK, build_threshold_payload(observation)

    return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"}


def route_webhook(body: bytes, content_type: str, latest_path: Path) -> tuple[int, dict[str, Any]]:
    observation = load_latest_observation(latest_path)
    message_text = extract_message_text(body, content_type)
    return HTTPStatus.OK, build_chat_reply(message_text, observation)


def make_handler(latest_path: Path):
    class ApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                status, payload = route_request(self.path, latest_path)
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
                status, payload = route_webhook(body, content_type, latest_path)
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    latest_path = Path(args.latest_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(latest_path))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())