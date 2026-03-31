#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from supervisor import DEFAULT_CONFIG, execute_action, load_config, read_latest_observation


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            json.dump(record, handle, separators=(",", ":"))
            handle.write("\n")


class SupervisorTests(unittest.TestCase):
    def make_observation(
        self,
        *,
        temperature: float = 23.4,
        humidity: float = 51.2,
        pressure: float | None = 1008.7,
        observed_at: str = "2026-03-29T11:12:13Z",
    ) -> dict[str, object]:
        observation = {
            "@context": {
                "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                "sensorId": "https://schema.org/identifier",
                "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                "observedAt": "https://schema.org/observationDate",
                "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa",
            },
            "@type": "SensorObservation",
            "schemaVersion": "sensor-observation-v1",
            "sensorId": "arduino-ttyUSB0",
            "sourcePort": "/dev/ttyUSB0",
            "observedAt": observed_at,
            "temperatureC": temperature,
            "humidityPct": humidity,
        }
        if pressure is not None:
            observation["pressureHpa"] = pressure
        return observation

    def test_read_latest_observation_returns_last_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(
                log_path,
                [
                    self.make_observation(temperature=20.0, humidity=40.0, observed_at="2026-03-29T10:00:00Z"),
                    self.make_observation(temperature=24.5, humidity=55.0, observed_at="2026-03-29T11:00:00Z"),
                ],
            )

            latest = read_latest_observation(log_path)
            self.assertEqual(latest["temperatureC"], 24.5)
            self.assertEqual(latest["humidityPct"], 55.0)

    def test_read_latest_temperature_returns_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(temperature=26.2)])

            response = execute_action(
                "read_latest",
                "temperature",
                log_path=log_path,
                config=load_config(),
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["action"], "read_latest")
            self.assertEqual(response["metric"], "temperature")
            self.assertEqual(response["value"], 26.2)
            self.assertEqual(response["unit"], "C")

    def test_read_latest_humidity_returns_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(humidity=63.5)])

            response = execute_action(
                "read_latest",
                "humidity",
                log_path=log_path,
                config=load_config(),
            )

            self.assertEqual(response["metric"], "humidity")
            self.assertEqual(response["value"], 63.5)
            self.assertEqual(response["unit"], "%")

    def test_read_latest_pressure_returns_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(pressure=1001.4)])

            response = execute_action(
                "read_latest",
                "pressure",
                log_path=log_path,
                config=load_config(),
            )

            self.assertEqual(response["metric"], "pressure")
            self.assertEqual(response["value"], 1001.4)
            self.assertEqual(response["unit"], "hPa")

    def test_get_threshold_status_uses_default_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(temperature=29.5, humidity=86.0, pressure=970.0)])

            response = execute_action(
                "get_threshold_status",
                None,
                log_path=log_path,
                config=load_config(),
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["thresholdStatus"]["temperature"]["status"], "warning")
            self.assertEqual(response["thresholdStatus"]["humidity"]["status"], "critical")
            self.assertEqual(response["thresholdStatus"]["pressure"]["status"], "warning")

    def test_get_threshold_status_uses_custom_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            config_path = temp_path / "supervisor-config.json"
            write_jsonl(log_path, [self.make_observation(temperature=29.5, humidity=60.0)])
            config_path.write_text(
                json.dumps(
                    {
                        "thresholds": {
                            "temperature": {
                                "metric": "temperatureC",
                                "unit": "C",
                                "warningMax": 30.0,
                                "criticalMax": 32.0,
                            },
                            "humidity": {
                                "metric": "humidityPct",
                                "unit": "%",
                                "warningMax": 65.0,
                                "criticalMax": 80.0,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            response = execute_action(
                "get_threshold_status",
                None,
                log_path=log_path,
                config=load_config(config_path),
            )

            self.assertEqual(response["thresholdStatus"]["temperature"]["status"], "normal")
            self.assertEqual(response["thresholdStatus"]["humidity"]["status"], "normal")

    def test_get_threshold_status_marks_pressure_unavailable_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(pressure=None)])

            response = execute_action(
                "get_threshold_status",
                None,
                log_path=log_path,
                config=load_config(),
            )

            self.assertEqual(response["thresholdStatus"]["pressure"]["status"], "unavailable")
            self.assertFalse(response["thresholdStatus"]["pressure"]["available"])

    def test_get_alarm_status_returns_active_alarms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(temperature=36.2, humidity=86.0, pressure=970.0)])

            response = execute_action(
                "get_alarm_status",
                None,
                log_path=log_path,
                config=load_config(),
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["hasActiveAlarms"])
            self.assertEqual(response["overallStatus"], "critical")
            self.assertEqual(response["activeAlarms"][0]["metric"], "temperature")

    def test_summarize_window_returns_recent_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(
                log_path,
                [
                    self.make_observation(temperature=20.0, humidity=40.0, pressure=1001.0, observed_at="2026-03-29T10:00:00Z"),
                    self.make_observation(temperature=22.0, humidity=42.0, pressure=1002.0, observed_at="2026-03-29T10:05:00Z"),
                    self.make_observation(temperature=24.0, humidity=44.0, pressure=1003.0, observed_at="2026-03-29T10:10:00Z"),
                ],
            )

            response = execute_action(
                "summarize_window",
                "temperature",
                log_path=log_path,
                config=load_config(),
                count=2,
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["window"]["actualCount"], 2)
            self.assertEqual(response["summary"]["temperature"]["average"], 23.0)
            self.assertEqual(response["summary"]["temperature"]["latest"], 24.0)

    def test_read_latest_pressure_fails_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation(pressure=None)])

            with self.assertRaisesRegex(ValueError, "missing pressure metric"):
                execute_action("read_latest", "pressure", log_path=log_path, config=load_config())

    def test_invalid_subject_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            write_jsonl(log_path, [self.make_observation()])

            with self.assertRaisesRegex(ValueError, "read_latest requires subject"):
                execute_action("read_latest", "wind", log_path=log_path, config=load_config())

    def test_missing_log_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "missing.jsonl"

            with self.assertRaisesRegex(FileNotFoundError, "validated observation log not found"):
                execute_action("read_latest", "temperature", log_path=log_path, config=load_config())

    def test_default_config_stays_local_and_plain(self) -> None:
        self.assertIn("thresholds", DEFAULT_CONFIG)
        self.assertNotIn("@context", DEFAULT_CONFIG)
        self.assertNotIn("@type", DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()