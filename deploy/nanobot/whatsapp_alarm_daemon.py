#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import websockets

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alarm_runtime import handle_admin_message, strip_whatsapp_prefix
from observation_analysis import SEVERITY_RANK, evaluate_thresholds, load_config

DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
DEFAULT_STATE_PATH = ROOT_DIR / "logs" / "whatsapp-alert-state.json"
DEFAULT_CHAT_REGISTRY_PATH = ROOT_DIR / "logs" / "whatsapp-chat-registry.json"


def _normalize_whatsapp_id(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value).split(":", 1)[0]
    return normalized.split("@", 1)[0]


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class WhatsAppAlarmDaemon:
    def __init__(self) -> None:
        self.bridge_url = os.environ.get("NANOBOT_WHATSAPP_BRIDGE_URL", "ws://127.0.0.1:3001")
        self.bridge_token = os.environ.get("NANOBOT_WHATSAPP_BRIDGE_TOKEN", "")
        self.allow_self_messages = os.environ.get("NANOBOT_WHATSAPP_ALLOW_SELF_MESSAGES", "false") == "true"
        self.self_chat_only = os.environ.get("NANOBOT_WHATSAPP_SELF_CHAT_ONLY", "false") == "true"
        self.allowed_ids = self._parse_id_list(os.environ.get("NANOBOT_WHATSAPP_ALLOW_FROM", ""))
        alert_targets_raw = os.environ.get("SSA_WHATSAPP_ALERT_TO", "")
        self.alert_targets = self._parse_id_list(alert_targets_raw)
        self.config_path = Path(os.environ.get("SSA_THRESHOLD_CONFIG", ROOT_DIR / "threshold-config.json"))
        self.latest_path = Path(os.environ.get("SSA_LATEST_OBSERVATION", DEFAULT_LATEST_PATH))
        self.state_path = Path(os.environ.get("SSA_ALERT_STATE_FILE", DEFAULT_STATE_PATH))
        self.chat_registry_path = Path(os.environ.get("SSA_CHAT_REGISTRY_FILE", DEFAULT_CHAT_REGISTRY_PATH))
        self.poll_interval = float(os.environ.get("SSA_ALERT_POLL_INTERVAL", "2.0"))
        self.state = _load_json_dict(self.state_path)
        self.chat_registry = _load_json_dict(self.chat_registry_path)
        self._connected = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._recent_outbound_echoes: OrderedDict[str, float] = OrderedDict()
        self._ws: Any = None

    @staticmethod
    def _parse_id_list(raw_value: str) -> list[str]:
        values: list[str] = []
        for item in raw_value.split(","):
            stripped = item.strip()
            if stripped:
                values.append(stripped)
        return values

    def _remember_outbound_echo(self, chat_id: str, content: str) -> None:
        if not content:
            return
        self._prune_outbound_echoes()
        key = f"{_normalize_whatsapp_id(chat_id)}\n{content.strip()}"
        self._recent_outbound_echoes[key] = time.time()
        while len(self._recent_outbound_echoes) > 200:
            self._recent_outbound_echoes.popitem(last=False)

    def _consume_outbound_echo(self, chat_id: str, content: str) -> bool:
        if not content:
            return False
        self._prune_outbound_echoes()
        key = f"{_normalize_whatsapp_id(chat_id)}\n{content.strip()}"
        timestamp = self._recent_outbound_echoes.pop(key, None)
        return timestamp is not None

    def _prune_outbound_echoes(self) -> None:
        cutoff = time.time() - 300
        while self._recent_outbound_echoes:
            first_key = next(iter(self._recent_outbound_echoes))
            if self._recent_outbound_echoes[first_key] >= cutoff:
                break
            self._recent_outbound_echoes.popitem(last=False)

    def _is_allowed_inbound(self, normalized_sender_id: str, normalized_chat_id: str) -> bool:
        if not self.allowed_ids:
            return False
        if "*" in self.allowed_ids:
            return True
        normalized_allow = {_normalize_whatsapp_id(value) for value in self.allowed_ids}
        return normalized_sender_id in normalized_allow or normalized_chat_id in normalized_allow

    def _remember_chat(self, normalized_id: str, full_chat_id: str) -> None:
        if not normalized_id or not full_chat_id:
            return
        existing = self.chat_registry.get(normalized_id)
        if existing == full_chat_id:
            return
        self.chat_registry[normalized_id] = full_chat_id
        _save_json(self.chat_registry_path, self.chat_registry)

    def _resolve_alert_recipients(self) -> list[str]:
        recipients: list[str] = []
        for target in self.alert_targets:
            if "@" in target:
                recipients.append(target)
                continue
            mapped = self.chat_registry.get(_normalize_whatsapp_id(target))
            if mapped:
                recipients.append(mapped)
        return list(dict.fromkeys(recipients))

    async def send_text(self, chat_id: str, text: str) -> bool:
        if not self._connected.is_set() or self._ws is None:
            return False

        payload = {"type": "send", "to": chat_id, "text": text}
        async with self._send_lock:
            self._remember_outbound_echo(chat_id, text)
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        return True

    async def handle_inbound_message(self, data: dict[str, Any]) -> None:
        sender = str(data.get("sender", ""))
        content = str(data.get("content", "")).strip()
        from_me = bool(data.get("fromMe", False))
        is_self_chat_candidate = bool(data.get("isSelfChatCandidate", False))
        pn = str(data.get("pn", ""))
        participant = str(data.get("participant", ""))
        remote_jid_alt = str(data.get("remoteJidAlt", ""))
        normalized_sender_id = _normalize_whatsapp_id(participant or pn or remote_jid_alt or sender)
        normalized_chat_id = _normalize_whatsapp_id(sender)

        self._remember_chat(normalized_sender_id, sender)
        self._remember_chat(normalized_chat_id, sender)

        if from_me:
            if not self.allow_self_messages:
                return
            if self._consume_outbound_echo(sender, content):
                return

        if self.self_chat_only and not is_self_chat_candidate:
            return
        if not self.self_chat_only and not self._is_allowed_inbound(normalized_sender_id, normalized_chat_id):
            return

        if not content:
            return

        if strip_whatsapp_prefix(content) is None:
            return

        try:
            response = handle_admin_message(content, config_path=self.config_path)
        except ValueError as exc:
            await self.send_text(sender, str(exc))
            return

        if response is None:
            return

        reply = response.get("reply")
        if isinstance(reply, str) and reply:
            await self.send_text(sender, reply)

    async def bridge_loop(self) -> None:
        while True:
            try:
                async with websockets.connect(self.bridge_url) as websocket:
                    self._ws = websocket
                    if self.bridge_token:
                        await websocket.send(json.dumps({"type": "auth", "token": self.bridge_token}))
                    self._connected.set()

                    async for raw_message in websocket:
                        payload = json.loads(raw_message)
                        if not isinstance(payload, dict):
                            continue
                        if payload.get("type") == "message":
                            await self.handle_inbound_message(payload)
            except Exception as exc:
                self._connected.clear()
                self._ws = None
                print(f"whatsapp alarm daemon bridge error: {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(5)

    async def alert_loop(self) -> None:
        while True:
            try:
                if self.latest_path.exists():
                    observation = json.loads(self.latest_path.read_text(encoding="utf-8"))
                    if isinstance(observation, dict):
                        observed_at = str(observation.get("observedAt") or "")
                        if observed_at and observed_at != self.state.get("lastObservedAt"):
                            evaluation = evaluate_thresholds(observation, load_config(self.config_path))
                            temperature = evaluation["thresholdStatus"].get("temperature", {})
                            current_status = str(temperature.get("status", "unavailable"))
                            previous_status = str(self.state.get("lastTemperatureStatus", "unavailable"))

                            if temperature.get("available") and temperature.get("alarm"):
                                if previous_status not in {"warning", "critical"} or SEVERITY_RANK[current_status] > SEVERITY_RANK.get(previous_status, 0):
                                    recipients = self._resolve_alert_recipients()
                                    message = (
                                        f"Temperature alarm: {temperature['value']} {temperature['unit']} "
                                        f"({current_status}) at {observed_at}. "
                                        f"Warning {temperature['thresholds']['warningMax']} C, "
                                        f"critical {temperature['thresholds']['criticalMax']} C."
                                    )
                                    for recipient in recipients:
                                        await self.send_text(recipient, message)

                            self.state["lastObservedAt"] = observed_at
                            self.state["lastTemperatureStatus"] = current_status
                            _save_json(self.state_path, self.state)
            except Exception as exc:
                print(f"whatsapp alarm daemon alert error: {exc}", file=sys.stderr, flush=True)

            await asyncio.sleep(self.poll_interval)


async def main() -> int:
    daemon = WhatsAppAlarmDaemon()
    await asyncio.gather(daemon.bridge_loop(), daemon.alert_loop())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))