#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from observation_analysis import DEFAULT_CONFIG_PATH, load_config

DEFAULT_ADMIN_PASSWORD = "8888"
WHATSAPP_PREFIX = "@ssa"
TEMPERATURE_ALIASES = {"temp", "temperature"}
SHOW_KEYWORDS = {"show", "list", "status", "thresholds", "alarms"}
UPDATE_KEYWORDS = {"set", "update", "change"}


def get_admin_password() -> str:
    return os.environ.get("SSA_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)


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


def _parse_temperature_update(tokens: list[str]) -> tuple[str, float, list[str]]:
    if not tokens:
        raise ValueError(
            "Use 8888 set temp 30 or 8888 set temp critical 35."
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
            "Missing threshold value. Use 8888 set temp 30 or 8888 set temp critical 35."
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
                "Warning threshold cannot be above critical. Set critical first or use 8888 set temp 30."
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


def handle_admin_message(
    message_text: str,
    *,
    config_path: Path | None = None,
    password: str | None = None,
) -> dict[str, Any] | None:
    stripped = strip_whatsapp_prefix(message_text)
    if stripped is None:
        return None

    normalized = stripped
    if not normalized:
        raise ValueError("Use @ssa 8888 thresholds or @ssa 8888 set temp 30.")

    tokens = normalized.split()
    expected_password = password or get_admin_password()
    if not expected_password or tokens[0] != expected_password:
        return None

    command_tokens = tokens[1:]
    if not command_tokens:
        raise ValueError("Use @ssa 8888 thresholds or @ssa 8888 set temp 30.")

    lowered = [token.lower() for token in command_tokens]
    if lowered[0] in {"help", "?"}:
        return {
            "ok": True,
            "action": "admin_help",
            "reply": "Admin commands: @ssa 8888 thresholds, @ssa 8888 set temp 30, @ssa 8888 set temp critical 35.",
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
            raise ValueError("Use @ssa 8888 set temp 30 or @ssa 8888 set temp critical 35.")

    if lowered and lowered[0] in {"threshold", "thresholds", "alarm", "alarms"}:
        lowered = lowered[1:]
        if not lowered:
            raise ValueError("Use @ssa 8888 set temp 30 or @ssa 8888 thresholds.")

    if lowered[0] not in TEMPERATURE_ALIASES:
        raise ValueError("Only temperature threshold updates are supported over WhatsApp right now.")

    if len(lowered) == 1:
        return {
            "ok": True,
            "action": "show_thresholds",
            "reply": format_temperature_thresholds(config),
            "data": _config_thresholds_only(config),
        }

    threshold_key, value, trailing = _parse_temperature_update(lowered[1:])
    if trailing:
        raise ValueError("Too many arguments. Use @ssa 8888 set temp 30 or @ssa 8888 set temp critical 35.")

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