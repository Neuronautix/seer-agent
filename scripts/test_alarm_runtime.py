#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarm_runtime import handle_admin_message, strip_whatsapp_prefix
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


if __name__ == "__main__":
    unittest.main()