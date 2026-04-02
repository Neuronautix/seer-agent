#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

            response = handle_admin_message("@ssa 8888 set temp 30", config_path=config_path)

            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["action"], "update_thresholds")
            self.assertTrue(config_path.exists())
            self.assertEqual(load_config(config_path)["thresholds"]["temperature"]["warningMax"], 30.0)

    def test_explicit_invalid_critical_threshold_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "threshold-config.json"

            with self.assertRaisesRegex(ValueError, "Critical threshold cannot be below"):
                handle_admin_message("@ssa 8888 set temp critical 20", config_path=config_path)

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

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            _parse_time_duration("xyz")
        with self.assertRaises(ValueError):
            _parse_time_duration("1")


class TempHistoryCommandTests(unittest.TestCase):
    def test_text_history_1h(self) -> None:
        response = handle_admin_message("@ssa 8888 temp last 1h")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 60)
        self.assertEqual(response["bucket_minutes"], 5)
        self.assertFalse(response["plot"])

    def test_text_history_30m(self) -> None:
        response = handle_admin_message("@ssa 8888 temp last 30m")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 30)
        self.assertFalse(response["plot"])

    def test_plot_history_2h(self) -> None:
        response = handle_admin_message("@ssa 8888 temp plot last 2h")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 120)
        self.assertTrue(response["plot"])

    def test_plot_history_30m(self) -> None:
        response = handle_admin_message("@ssa 8888 temp plot last 30m")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "temp_history")
        self.assertEqual(response["since_minutes"], 30)
        self.assertTrue(response["plot"])

    def test_history_invalid_duration_raises(self) -> None:
        with self.assertRaises(ValueError):
            handle_admin_message("@ssa 8888 temp last xyz")

    def test_history_missing_duration_raises(self) -> None:
        with self.assertRaises(ValueError):
            handle_admin_message("@ssa 8888 temp last")

    def test_existing_threshold_set_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "threshold-config.json"
            response = handle_admin_message("@ssa 8888 set temp 30", config_path=config_path)
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["action"], "update_thresholds")

    def test_help_includes_history_commands(self) -> None:
        response = handle_admin_message("@ssa 8888 help")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("temp last", response["reply"])
        self.assertIn("temp plot last", response["reply"])


if __name__ == "__main__":
    unittest.main()