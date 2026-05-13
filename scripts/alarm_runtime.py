#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observation_analysis import DEFAULT_CONFIG_PATH, evaluate_thresholds, load_config

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"
DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
DEFAULT_REJECTED_PATH = ROOT_DIR / "logs" / "rejected-lines.jsonl"

DEFAULT_ADMIN_PASSWORD = "CHANGE_ME"
WHATSAPP_PREFIX = "@ssa"
TEMPERATURE_ALIASES = {"temp", "temperature"}
SHOW_KEYWORDS = {"show", "list", "thresholds", "alarms"}
STATUS_KEYWORDS = {"status", "health", "info", "summary"}
UPDATE_KEYWORDS = {"set", "update", "change"}
HISTORY_KEYWORDS = {"last", "history", "since"}
DEFAULT_HISTORY_BUCKET_MINUTES = 5


def get_admin_password() -> str:
    return os.environ.get("SSA_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)


def _admin_example(password: str, suffix: str) -> str:
    return f"{WHATSAPP_PREFIX} {password} {suffix}"


def strip_whatsapp_prefix(message_text: str) -> str | None:
    normalized = " ".join(message_text.strip().split())
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered == WHATSAPP_PREFIX:
        return ""
    if lowered.startswith(f"{WHATSAPP_PREFIX} "):
        return normalized[len(WHATSAPP_PREFIX):].strip()
    return None


def _config_thresholds_only(config: dict[str, Any]) -> dict[str, Any]:
    return {"thresholds": config.get("thresholds", {})}


def save_config(config: dict[str, Any], config_path: Path | None = None) -> Path:
    resolved_path = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(_config_thresholds_only(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return resolved_path


def format_temperature_thresholds(config: dict[str, Any]) -> str:
    thresholds = config["thresholds"]["temperature"]
    warning_max = float(thresholds["warningMax"])
    critical_max = float(thresholds["criticalMax"])
    return f"Temperature warning {warning_max:.1f} C, critical {critical_max:.1f} C."


def _parse_temperature_update(tokens: list[str], password: str) -> tuple[str, float, list[str]]:
    if not tokens:
        raise ValueError(
            f"Use {_admin_example(password, 'set temp 30')} or {_admin_example(password, 'set temp critical 35')}."
        )

    threshold_key = "warningMax"
    remaining = tokens
    first = remaining[0].lower()
    if first in {"warning", "warn", "alarm"}:
        threshold_key = "warningMax"
        remaining = remaining[1:]
    elif first in {"critical", "crit"}:
        threshold_key = "criticalMax"
        remaining = remaining[1:]

    if not remaining:
        raise ValueError(
            f"Missing threshold value. Use {_admin_example(password, 'set temp 30')} or {_admin_example(password, 'set temp critical 35')}."
        )

    try:
        value = float(remaining[0])
    except ValueError as exc:
        raise ValueError("Threshold value must be numeric.") from exc

    return threshold_key, value, remaining[1:]


def update_temperature_threshold(
    config: dict[str, Any],
    *,
    threshold_key: str,
    value: float,
    implicit_warning_update: bool,
) -> tuple[dict[str, Any], list[str]]:
    thresholds = config.setdefault("thresholds", {}).setdefault("temperature", {})
    warning_max = float(thresholds.get("warningMax", value))
    critical_max = float(thresholds.get("criticalMax", value))
    adjustments: list[str] = []

    if threshold_key == "warningMax":
        warning_max = value
        if implicit_warning_update and critical_max < warning_max:
            critical_max = warning_max
            adjustments.append(f"critical adjusted to {critical_max:.1f} C")
        elif critical_max < warning_max:
            raise ValueError(
                f"Warning threshold cannot be above critical. Set critical first or use {_admin_example(get_admin_password(), 'set temp 30')}."
            )
    elif threshold_key == "criticalMax":
        critical_max = value
        if critical_max < warning_max:
            raise ValueError("Critical threshold cannot be below the warning threshold.")
    else:
        raise ValueError(f"Unsupported threshold key: {threshold_key}")

    thresholds["warningMax"] = warning_max
    thresholds["criticalMax"] = critical_max
    return config, adjustments


def _count_lines(path: Path) -> int:
    """Count non-empty lines in a file; return 0 if the file does not exist."""
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def _build_status_reply(
    config_path: Path | None,
    log_path: Path | None = None,
    latest_path: Path | None = None,
    rejected_path: Path | None = None,
) -> str:
    """Return a compact system-health digest for the @ssa status command."""
    resolved_log = log_path or DEFAULT_LOG_PATH
    resolved_latest = latest_path or DEFAULT_LATEST_PATH
    resolved_rejected = rejected_path or DEFAULT_REJECTED_PATH

    now_utc = datetime.now(tz=timezone.utc)
    lines: list[str] = [f"Status at {now_utc.strftime('%H:%M')} UTC"]

    # Sensor freshness
    if resolved_latest.exists():
        try:
            obs = json.loads(resolved_latest.read_text(encoding="utf-8"))
            if not isinstance(obs, dict):
                lines.append("Sensor: invalid latest observation format")
            else:
                observed_at = str(obs.get("observedAt") or "")
                sensor_id = str(obs.get("sensorId") or "unknown")
                if observed_at:
                    last_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                    age = int((now_utc - last_dt).total_seconds())
                    freshness = "fresh" if age <= 300 else "STALE"
                    lines.append(f"Sensor: {freshness} ({age}s ago) | {sensor_id}")
                else:
                    lines.append(f"Sensor: no timestamp | {sensor_id}")

                config = load_config(config_path)
                ev = evaluate_thresholds(obs, config)
                ts = ev["thresholdStatus"]
                parts: list[str] = []
                for metric, label, unit_suffix in (
                    ("temperature", "Temp", "°C"),
                    ("humidity", "Hum", "%"),
                    ("pressure", "Press", "hPa"),
                ):
                    m = ts.get(metric, {})
                    if m.get("available"):
                        status_flag = "" if m["status"] == "normal" else f" [{m['status'].upper()}]"
                        parts.append(f"{label} {m['value']}{unit_suffix}{status_flag}")
                if parts:
                    lines.append(" | ".join(parts))
                alarms = ev.get("activeAlarms", [])
                lines.append(f"Alarms: {'none' if not alarms else ', '.join(a['metric'] for a in alarms)}")
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            lines.append("Sensor: error reading latest observation")
    else:
        lines.append("Sensor: no data yet")

    # Log stats
    obs_count = _count_lines(resolved_log)
    rej_count = _count_lines(resolved_rejected)
    lines.append(f"Log: {obs_count:,} observations | {rej_count} rejected")

    return "\n".join(lines)


def _parse_time_duration(token: str) -> int:
    """Parse a duration token like '1h', '30m', '2d', '1w' into minutes."""
    t = token.lower().strip()
    if t.endswith("w"):
        try:
            return max(1, int(round(float(t[:-1]) * 7 * 24 * 60)))
        except ValueError:
            pass
    elif t.endswith("d"):
        try:
            return max(1, int(round(float(t[:-1]) * 24 * 60)))
        except ValueError:
            pass
    elif t.endswith("h"):
        try:
            return max(1, int(round(float(t[:-1]) * 60)))
        except ValueError:
            pass
    elif t.endswith("m"):
        try:
            return max(1, int(t[:-1]))
        except ValueError:
            pass
    raise ValueError(
        f"Time duration must be like '30m', '1h', '2d', or '1w'. Got: '{token}'"
    )


def _handle_temp_history(tokens: list[str], password: str) -> dict[str, Any]:
    """Parse 'last 1h', 'plot last 30m', etc. and return a temp_history action dict."""
    remaining = tokens
    plot = False

    if remaining and remaining[0].lower() == "plot":
        plot = True
        remaining = remaining[1:]

    if not remaining or remaining[0].lower() not in HISTORY_KEYWORDS:
        raise ValueError(
            f"Use {_admin_example(password, 'temp last 1h')} or {_admin_example(password, 'temp plot last 30m')}."
        )
    remaining = remaining[1:]  # skip 'last' / 'history' / 'since'

    if not remaining:
        raise ValueError("Specify a time range like 1h or 30m.")

    since_minutes = _parse_time_duration(remaining[0])

    return {
        "ok": True,
        "action": "temp_history",
        "since_minutes": since_minutes,
        "bucket_minutes": DEFAULT_HISTORY_BUCKET_MINUTES,
        "plot": plot,
    }


def handle_admin_message(
    message_text: str,
    *,
    config_path: Path | None = None,
    log_path: Path | None = None,
    latest_path: Path | None = None,
    rejected_path: Path | None = None,
    password: str | None = None,
) -> dict[str, Any] | None:
    stripped = strip_whatsapp_prefix(message_text)
    if stripped is None:
        return None

    normalized = stripped
    if not normalized:
        raise ValueError(
            f"Use {_admin_example(get_admin_password(), 'thresholds')} or {_admin_example(get_admin_password(), 'set temp 30')}."
        )

    tokens = normalized.split()
    expected_password = password or get_admin_password()
    if not expected_password or tokens[0] != expected_password:
        return None

    command_tokens = tokens[1:]
    if not command_tokens:
        raise ValueError(
            f"Use {_admin_example(expected_password, 'thresholds')} or {_admin_example(expected_password, 'set temp 30')}."
        )

    lowered = [token.lower() for token in command_tokens]
    if lowered[0] in {"help", "?"}:
        return {
            "ok": True,
            "action": "admin_help",
            "reply": (
                f"Admin commands: {_admin_example(expected_password, 'status')}, "
                f"{_admin_example(expected_password, 'thresholds')}, "
                f"{_admin_example(expected_password, 'set temp 30')}, "
                f"{_admin_example(expected_password, 'set temp critical 35')}, "
                f"{_admin_example(expected_password, 'temp last 1h')}, "
                f"{_admin_example(expected_password, 'temp plot last 30m')}."
            ),
        }

    if lowered[0] in STATUS_KEYWORDS:
        return {
            "ok": True,
            "action": "system_status",
            "reply": _build_status_reply(
                config_path,
                log_path=log_path,
                latest_path=latest_path,
                rejected_path=rejected_path,
            ),
        }

    config = load_config(config_path)

    if lowered[0] in SHOW_KEYWORDS:
        return {
            "ok": True,
            "action": "show_thresholds",
            "reply": format_temperature_thresholds(config),
            "data": _config_thresholds_only(config),
        }

    implicit_warning_update = False
    if lowered[0] in UPDATE_KEYWORDS:
        lowered = lowered[1:]
        if not lowered:
            raise ValueError(
                f"Use {_admin_example(expected_password, 'set temp 30')} or {_admin_example(expected_password, 'set temp critical 35')}."
            )

    if lowered and lowered[0] in {"threshold", "thresholds", "alarm", "alarms"}:
        lowered = lowered[1:]
        if not lowered:
            raise ValueError(
                f"Use {_admin_example(expected_password, 'set temp 30')} or {_admin_example(expected_password, 'thresholds')}."
            )

    if lowered[0] not in TEMPERATURE_ALIASES:
        raise ValueError("Only temperature threshold updates are supported over WhatsApp right now.")

    if len(lowered) == 1:
        return {
            "ok": True,
            "action": "show_thresholds",
            "reply": format_temperature_thresholds(config),
            "data": _config_thresholds_only(config),
        }

    # History query: "temp last 1h", "temp plot last 30m", etc.
    if lowered[1] in HISTORY_KEYWORDS or lowered[1] == "plot":
        return _handle_temp_history(lowered[1:], expected_password)

    threshold_key, value, trailing = _parse_temperature_update(lowered[1:], expected_password)
    if trailing:
        raise ValueError(
            f"Too many arguments. Use {_admin_example(expected_password, 'set temp 30')} or {_admin_example(expected_password, 'set temp critical 35')}."
        )

    implicit_warning_update = threshold_key == "warningMax" and len(lowered[1:]) == 1
    updated_config, adjustments = update_temperature_threshold(
        config,
        threshold_key=threshold_key,
        value=value,
        implicit_warning_update=implicit_warning_update,
    )
    save_config(updated_config, config_path)

    reply = f"Saved. {format_temperature_thresholds(updated_config)}"
    if adjustments:
        reply = f"Saved. {format_temperature_thresholds(updated_config)} {'; '.join(adjustments)}."

    return {
        "ok": True,
        "action": "update_thresholds",
        "reply": reply,
        "data": _config_thresholds_only(updated_config),
    }