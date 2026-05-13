#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from alarm_runtime import _parse_time_duration, handle_admin_message, strip_whatsapp_prefix
from observation_analysis import load_config
from deploy.nanobot.whatsapp_alarm_daemon import WhatsAppAlarmDaemon


class AlarmRuntimeTests(unittest.TestCase):
    def test_strip_whatsapp_prefix_requires_ssa_prefix(self) -> None:
        self.assertIsNone(strip_whatsapp_prefix("temperature"))
        self.assertEqual(strip_whatsapp_prefix("@ssa temperature"), "temperature")

    def test_admin_command_is_ignored_without_password(self) -> None:
        self.assertIsNone(handle_admin_message("set temp 30"))
        self.assertIsNone(handle_admin_message("@ssa set temp 30"))

    def test_admin_command_updates_temperature_threshold_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "threshold-config.json"

            response = handle_admin_message("@ssa CHANGE_ME set temp 30", config_path=config_path)

            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["action"], "update_thresholds")
            self.assertTrue(config_path.exists())
            self.assertEqual(load_config(config_path)["thresholds"]["temperature"]["warningMax"], 30.0)

    def test_explicit_invalid_critical_threshold_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "threshold-config.json"

            with self.assertRaisesRegex(ValueError, "Critical threshold cannot be below"):
                handle_admin_message("@ssa CHANGE_ME set temp critical 20", config_path=config_path)

    def test_status_uses_explicit_latest_and_rejected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            latest_path = temp_path / "latest.json"
            log_path = temp_path / "obs.jsonl"
            rejected_path = temp_path / "rejected.jsonl"
            latest_path.write_text(
                json.dumps(
                    {
                        "@type": "SensorObservation",
                        "sensorId": "custom-sensor",
                        "observedAt": "2026-04-02T12:00:00Z",
                        "temperatureC": 23.4,
                        "humidityPct": 51.2,
                    }
                ),
                encoding="utf-8",
            )
            log_path.write_text("{}\n{}\n", encoding="utf-8")
            rejected_path.write_text("{}\n", encoding="utf-8")

            response = handle_admin_message(
                "@ssa CHANGE_ME status",
                log_path=log_path,
                latest_path=latest_path,
                rejected_path=rejected_path,
            )

            self.assertIsNotNone(response)
            assert response is not None
            self.assertIn("custom-sensor", response["reply"])
            self.assertIn("2 observations | 1 rejected", response["reply"])

    def test_status_handles_non_object_latest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            latest_path = temp_path / "latest.json"
            latest_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

            response = handle_admin_message(
                "@ssa CHANGE_ME status",
                latest_path=latest_path,
                log_path=temp_path / "obs.jsonl",
                rejected_path=temp_path / "rejected.jsonl",
            )

            self.assertIsNotNone(response)
            assert response is not None
            self.assertIn("invalid latest observation format", response["reply"])

    def test_alarm_daemon_does_not_fallback_to_allow_from_for_alert_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch.dict(
                "os.environ",
                {
                    "NANOBOT_WHATSAPP_ALLOW_FROM": "33652217952,33785306470",
                    "SSA_CHAT_REGISTRY_FILE": str(temp_path / "registry.json"),
                    "SSA_ALERT_STATE_FILE": str(temp_path / "state.json"),
                    "SSA_LATEST_OBSERVATION": str(temp_path / "latest.json"),
                },
                clear=False,
            ):
                daemon = WhatsAppAlarmDaemon()

            self.assertEqual(daemon.alert_targets, [])

    def test_alarm_daemon_uses_explicit_alert_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch.dict(
                "os.environ",
                {
                    "SSA_WHATSAPP_ALERT_TO": "170639184896160@lid",
                    "SSA_CHAT_REGISTRY_FILE": str(temp_path / "registry.json"),
                    "SSA_ALERT_STATE_FILE": str(temp_path / "state.json"),
                    "SSA_LATEST_OBSERVATION": str(temp_path / "latest.json"),
                },
                clear=False,
            ):
                daemon = WhatsAppAlarmDaemon()

            self.assertEqual(daemon._resolve_alert_recipients(), ["170639184896160@lid"])


def _make_observation(temp: float, observed_at: str = "2026-04-02T12:00:00Z") -> dict:
    return {
        "@type": "SensorObservation",
        "temperatureC": temp,
        "humidityPct": 50.0,
        "observedAt": observed_at,
    }


class AlarmRepeatTests(unittest.TestCase):
    """Test the repeat-alarm and alarm-cleared notification logic in alert_loop."""

    def _make_daemon(self, tmp_path: Path) -> "WhatsAppAlarmDaemon":
        with patch.dict(
            "os.environ",
            {
                "SSA_WHATSAPP_ALERT_TO": "15551234567@s.whatsapp.net",
                "SSA_ALARM_REPEAT_INTERVAL": "300",
                "SSA_ALERT_POLL_INTERVAL": "2",
                "SSA_CHAT_REGISTRY_FILE": str(tmp_path / "registry.json"),
                "SSA_ALERT_STATE_FILE": str(tmp_path / "state.json"),
                "SSA_LATEST_OBSERVATION": str(tmp_path / "latest.json"),
                "SSA_OBSERVATIONS_LOG": str(tmp_path / "obs.jsonl"),
                "SSA_THRESHOLD_CONFIG": str(tmp_path / "threshold.json"),
            },
            clear=False,
        ):
            daemon = WhatsAppAlarmDaemon()
        # Pre-register the alert target so _resolve_alert_recipients works
        daemon.chat_registry["15551234567"] = "15551234567@s.whatsapp.net"
        daemon._connected.set()
        daemon._ws = MagicMock()
        return daemon

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_first_alarm_sends_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon.send_text = AsyncMock(return_value=True)

            # Write a warning-level observation
            obs = _make_observation(temp=30.0)  # above default warning (28 C)
            (tmp_path / "latest.json").write_text(json.dumps(obs))

            self._run(daemon.alert_loop.__wrapped__(daemon) if hasattr(daemon.alert_loop, "__wrapped__") else self._single_alert_tick(daemon))

            daemon.send_text.assert_called_once()
            call_args = daemon.send_text.call_args[0]
            self.assertIn("alarm", call_args[1].lower())
            self.assertNotIn("STILL ACTIVE", call_args[1])

    def _single_alert_tick(self, daemon: "WhatsAppAlarmDaemon"):
        """Run exactly one evaluation cycle of alert_loop logic (extracted for testing)."""
        async def _tick():
            if daemon.latest_path.exists():
                observation = json.loads(daemon.latest_path.read_text(encoding="utf-8"))
                if isinstance(observation, dict):
                    from observation_analysis import evaluate_thresholds, load_config
                    from observation_analysis import SEVERITY_RANK
                    observed_at = str(observation.get("observedAt") or "")
                    if observed_at and observed_at != daemon.state.get("lastObservedAt"):
                        evaluation = evaluate_thresholds(observation, load_config(daemon.config_path))
                        temperature = evaluation["thresholdStatus"].get("temperature", {})
                        current_status = str(temperature.get("status", "unavailable"))
                        previous_status = str(daemon.state.get("lastTemperatureStatus", "unavailable"))
                        last_notified_at = daemon.state.get("lastAlarmNotifiedAt")

                        recipients = daemon._resolve_alert_recipients()
                        message = None

                        if temperature.get("available") and temperature.get("alarm"):
                            now = time.time()
                            secs_since_notify = (now - last_notified_at) if last_notified_at else None
                            is_new_alarm = previous_status not in {"warning", "critical"}
                            is_escalation = SEVERITY_RANK[current_status] > SEVERITY_RANK.get(previous_status, 0)
                            is_repeat_due = secs_since_notify is not None and secs_since_notify >= daemon.alarm_repeat_interval

                            if is_new_alarm or is_escalation or is_repeat_due:
                                prefix = "STILL ACTIVE — " if is_repeat_due and not is_new_alarm and not is_escalation else ""
                                message = (
                                    f"{prefix}Temperature alarm: {temperature['value']} {temperature['unit']} "
                                    f"({current_status}) at {observed_at}. "
                                    f"Warning {temperature['thresholds']['warningMax']} C, "
                                    f"critical {temperature['thresholds']['criticalMax']} C."
                                )
                                daemon.state["lastAlarmNotifiedAt"] = now

                        elif previous_status in {"warning", "critical"} and last_notified_at is not None:
                            message = (
                                f"Temperature back to normal: {temperature.get('value', '?')} "
                                f"{temperature.get('unit', 'C')} at {observed_at}."
                            )
                            daemon.state["lastAlarmNotifiedAt"] = None

                        if message:
                            for recipient in recipients:
                                await daemon.send_text(recipient, message)

                        daemon.state["lastObservedAt"] = observed_at
                        daemon.state["lastTemperatureStatus"] = current_status
        return _tick()

    def test_no_repeat_before_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon.send_text = AsyncMock(return_value=True)

            # Simulate alarm already active, notified just now
            daemon.state["lastTemperatureStatus"] = "warning"
            daemon.state["lastAlarmNotifiedAt"] = time.time()

            obs = _make_observation(temp=30.0, observed_at="2026-04-02T12:00:01Z")
            (tmp_path / "latest.json").write_text(json.dumps(obs))

            self._run(self._single_alert_tick(daemon))

            daemon.send_text.assert_not_called()

    def test_repeat_notification_after_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon.send_text = AsyncMock(return_value=True)

            # Alarm already active, but last notified 6 minutes ago
            daemon.state["lastTemperatureStatus"] = "warning"
            daemon.state["lastAlarmNotifiedAt"] = time.time() - 360  # 6 min ago

            obs = _make_observation(temp=30.0, observed_at="2026-04-02T12:06:00Z")
            (tmp_path / "latest.json").write_text(json.dumps(obs))

            self._run(self._single_alert_tick(daemon))

            daemon.send_text.assert_called_once()
            call_text = daemon.send_text.call_args[0][1]
            self.assertIn("STILL ACTIVE", call_text)

    def test_cleared_notification_on_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon.send_text = AsyncMock(return_value=True)

            # Was in alarm
            daemon.state["lastTemperatureStatus"] = "warning"
            daemon.state["lastAlarmNotifiedAt"] = time.time() - 60

            # Now temperature is back to normal (22 C, well below 28 warning)
            obs = _make_observation(temp=22.0, observed_at="2026-04-02T12:10:00Z")
            (tmp_path / "latest.json").write_text(json.dumps(obs))

            self._run(self._single_alert_tick(daemon))

            daemon.send_text.assert_called_once()
            call_text = daemon.send_text.call_args[0][1]
            self.assertIn("back to normal", call_text.lower())

    def test_no_cleared_notification_if_never_alerted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon.send_text = AsyncMock(return_value=True)

            # Was in warning state but lastAlarmNotifiedAt is None (e.g. state corruption)
            daemon.state["lastTemperatureStatus"] = "warning"
            daemon.state["lastAlarmNotifiedAt"] = None

            obs = _make_observation(temp=22.0, observed_at="2026-04-02T12:10:00Z")
            (tmp_path / "latest.json").write_text(json.dumps(obs))

            self._run(self._single_alert_tick(daemon))

            daemon.send_text.assert_not_called()


class ParseTimeDurationTests(unittest.TestCase):
    def test_hours(self) -> None:
        self.assertEqual(_parse_time_duration("1h"), 60)
        self.assertEqual(_parse_time_duration("2h"), 120)
        self.assertEqual(_parse_time_duration("2H"), 120)

    def test_minutes(self) -> None:
        self.assertEqual(_parse_time_duration("30m"), 30)
        self.assertEqual(_parse_time_duration("90m"), 90)

    def test_fractional_hours(self) -> None:
        self.assertEqual(_parse_time_duration("1.5h"), 90)

    def test_days(self) -> None:
        self.assertEqual(_parse_time_duration("1d"), 24 * 60)
        self.assertEqual(_parse_time_duration("3d"), 3 * 24 * 60)

    def test_weeks(self) -> None:
        self.assertEqual(_parse_time_duration("1w"), 7 * 24 * 60)
        self.assertEqual(_parse_time_duration("2W"), 2 * 7 * 24 * 60)

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            _parse_time_duration("xyz")
        with self.assertRaises(ValueError):
            _parse_time_duration("1")


class TempHistoryCommandTests(unittest.TestCase):
    def test_text_history_1h(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp last 1h")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 60)
        self.assertEqual(response["bucket_minutes"], 5)
        self.assertFalse(response["plot"])

    def test_text_history_30m(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp last 30m")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 30)
        self.assertFalse(response["plot"])

    def test_plot_history_2h(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp plot last 2h")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 120)
        self.assertTrue(response["plot"])

    def test_plot_history_30m(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp plot last 30m")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 30)
        self.assertTrue(response["plot"])

    def test_text_history_1w(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp last 1w")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 7 * 24 * 60)
        self.assertFalse(response["plot"])

    def test_plot_history_1w(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp plot last 1w")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 7 * 24 * 60)
        self.assertTrue(response["plot"])

    def test_text_history_3d(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME temp last 3d")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["since_minutes"], 3 * 24 * 60)

    def test_history_invalid_duration_raises(self) -> None:
        with self.assertRaises(ValueError):
            handle_admin_message("@ssa CHANGE_ME temp last xyz")

    def test_history_missing_duration_raises(self) -> None:
        with self.assertRaises(ValueError):
            handle_admin_message("@ssa CHANGE_ME temp last")

    def test_existing_threshold_set_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "threshold-config.json"
            response = handle_admin_message("@ssa CHANGE_ME set temp 30", config_path=config_path)
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["action"], "update_thresholds")

    def test_help_includes_history_commands(self) -> None:
        response = handle_admin_message("@ssa CHANGE_ME help")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("temp last", response["reply"])
        self.assertIn("temp plot last", response["reply"])

    def test_help_uses_current_admin_password(self) -> None:
        with patch.dict("os.environ", {"SSA_ADMIN_PASSWORD": "8989"}, clear=False):
            response = handle_admin_message("@ssa 8989 help")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("@ssa 8989 status", response["reply"])
        self.assertNotIn("@ssa CHANGE_ME status", response["reply"])


class TempHistoryDeliveryTests(unittest.TestCase):
    def _make_daemon(self, tmp_path: Path) -> WhatsAppAlarmDaemon:
        with patch.dict(
            "os.environ",
            {
                "SSA_CHAT_REGISTRY_FILE": str(tmp_path / "registry.json"),
                "SSA_ALERT_STATE_FILE": str(tmp_path / "state.json"),
                "SSA_LATEST_OBSERVATION": str(tmp_path / "latest.json"),
                "SSA_OBSERVATIONS_LOG": str(tmp_path / "obs.jsonl"),
                "SSA_THRESHOLD_CONFIG": str(tmp_path / "threshold.json"),
            },
            clear=False,
        ):
            daemon = WhatsAppAlarmDaemon()
        daemon._connected.set()
        daemon._ws = MagicMock()
        return daemon

    def _write_recent_observations(self, log_path: Path) -> None:
        now = datetime.now(tz=timezone.utc)
        observations = [
            {
                "@type": "SensorObservation",
                "temperatureC": 21.5,
                "humidityPct": 50.0,
                "observedAt": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "@type": "SensorObservation",
                "temperatureC": 22.0,
                "humidityPct": 51.0,
                "observedAt": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        ]
        log_path.write_text("\n".join(json.dumps(obs) for obs in observations) + "\n", encoding="utf-8")

    def test_send_image_uses_bridge_send_media_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            daemon._ws = MagicMock(send=AsyncMock())
            plot_path = tmp_path / "plot.png"
            plot_path.write_bytes(b"png")

            sent = asyncio.get_event_loop().run_until_complete(
                daemon.send_image("170639184896160@lid", plot_path, "Temperature last 60min")
            )

            self.assertTrue(sent)
            daemon._ws.send.assert_awaited_once()
            payload = json.loads(daemon._ws.send.await_args.args[0])
            self.assertEqual(payload["type"], "send_media")
            self.assertEqual(payload["to"], "170639184896160@lid")
            self.assertEqual(payload["filePath"], str(plot_path))
            self.assertEqual(payload["mimetype"], "image/png")
            self.assertEqual(payload["fileName"], "plot.png")
            self.assertEqual(payload["caption"], "Temperature last 60min")

    def test_plot_history_sends_text_when_image_send_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            self._write_recent_observations(tmp_path / "obs.jsonl")
            daemon.send_image = AsyncMock(return_value=False)
            daemon.send_text = AsyncMock(return_value=True)

            with patch("temperature_report.generate_temperature_plot", return_value=tmp_path / "plot.png"):
                (tmp_path / "plot.png").write_bytes(b"png")
                asyncio.get_event_loop().run_until_complete(
                    daemon._send_temp_history("chat", {"since_minutes": 60, "bucket_minutes": 5, "plot": True})
                )

            daemon.send_image.assert_called_once()
            daemon.send_text.assert_called_once()
            self.assertIn("Temp history", daemon.send_text.call_args[0][1])

    def test_plot_history_also_sends_text_after_image_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            daemon = self._make_daemon(tmp_path)
            self._write_recent_observations(tmp_path / "obs.jsonl")
            daemon.send_image = AsyncMock(return_value=True)
            daemon.send_text = AsyncMock(return_value=True)

            with patch("temperature_report.generate_temperature_plot", return_value=tmp_path / "plot.png"):
                (tmp_path / "plot.png").write_bytes(b"png")
                asyncio.get_event_loop().run_until_complete(
                    daemon._send_temp_history("chat", {"since_minutes": 60, "bucket_minutes": 5, "plot": True})
                )

            daemon.send_image.assert_called_once()
            daemon.send_text.assert_called_once()
            self.assertIn("Temp history", daemon.send_text.call_args[0][1])


if __name__ == "__main__":
    unittest.main()