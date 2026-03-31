#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest import mock
from pathlib import Path

from build_observation import build_observation, normalize_timestamp
from api_server import make_handler
from ontology_guard import load_schema, validate_observation
from read_serial import (
    ingest_stream,
    parse_human_readable_sensor_field,
    parse_sensor_line,
    set_serial_baud_rate,
    split_human_readable_fragments,
)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_schema()

    def test_parse_sensor_line(self) -> None:
        parsed = parse_sensor_line("TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n")
        self.assertEqual(parsed["temperature_c"], 23.4)
        self.assertEqual(parsed["humidity_pct"], 51.2)
        self.assertEqual(parsed["pressure_hpa"], 1008.7)
        self.assertEqual(parsed["timestamp"], "2026-03-29T11:12:13Z")

    def test_parse_sensor_line_without_pressure(self) -> None:
        parsed = parse_sensor_line("TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z\n")
        self.assertEqual(parsed["temperature_c"], 23.4)
        self.assertEqual(parsed["humidity_pct"], 51.2)
        self.assertNotIn("pressure_hpa", parsed)

    def test_parse_human_readable_sensor_field(self) -> None:
        self.assertEqual(
            parse_human_readable_sensor_field("Temperature = 19.95 °C\n"),
            ("temperature_c", 19.95),
        )
        self.assertEqual(
            parse_human_readable_sensor_field("Humidity    = 37.28 %\n"),
            ("humidity_pct", 37.28),
        )
        self.assertIsNone(parse_human_readable_sensor_field("ignored text\n"))

    def test_split_human_readable_fragments(self) -> None:
        self.assertEqual(
            split_human_readable_fragments("Temperature = 19.95 °CHumidity    = 37.28 %\r\n"),
            ["Temperature = 19.95 °C", "Humidity    = 37.28 %"],
        )

    def test_set_serial_baud_rate_falls_back_without_cfset_functions(self) -> None:
        attributes = [0, 0, 0, 0, 0, 0, []]

        with mock.patch("read_serial.termios.cfsetispeed", new=None, create=True), mock.patch(
            "read_serial.termios.cfsetospeed", new=None, create=True
        ):
            set_serial_baud_rate(attributes, 9600)

        self.assertEqual(attributes[4], attributes[5])
        self.assertNotEqual(attributes[4], 0)

    def test_build_and_validate_observation(self) -> None:
        observation = build_observation(
            {
                "temperature_c": 23.4,
                "humidity_pct": 51.2,
                "pressure_hpa": 1008.7,
                "timestamp": "2026-03-29T11:12:13Z",
                "device": "/dev/ttyUSB0",
                "sensor_id": "arduino-ttyUSB0",
            }
        )
        validated = validate_observation(observation, self.schema)
        self.assertEqual(validated["@type"], "SensorObservation")
        self.assertEqual(validated["observedAt"], "2026-03-29T11:12:13Z")
        self.assertEqual(validated["pressureHpa"], 1008.7)
        self.assertEqual(validated["sourcePort"], "/dev/ttyUSB0")
        self.assertEqual(validated["schemaVersion"], "sensor-observation-v1")

    def test_build_and_validate_observation_without_pressure(self) -> None:
        observation = build_observation(
            {
                "temperature_c": 23.4,
                "humidity_pct": 51.2,
                "timestamp": "2026-03-29T11:12:13Z",
                "device": "/dev/ttyUSB0",
                "sensor_id": "arduino-ttyUSB0",
            }
        )
        validated = validate_observation(observation, self.schema)
        self.assertNotIn("pressureHpa", validated)

    def test_timestamp_is_normalized_to_canonical_utc_z(self) -> None:
        self.assertEqual(normalize_timestamp("2026-03-29T13:12:13+02:00"), "2026-03-29T11:12:13Z")

    def test_noncanonical_timestamp_is_rejected(self) -> None:
        observation = build_observation(
            {
                "temperature_c": 23.4,
                "humidity_pct": 51.2,
                "pressure_hpa": 1008.7,
                "timestamp": "2026-03-29T13:12:13+02:00",
                "device": "/dev/ttyUSB0",
                "sensor_id": "arduino-ttyUSB0",
            }
        )
        observation["observedAt"] = "2026-03-29T13:12:13+02:00"

        with self.assertRaisesRegex(ValueError, "observedAt"):
            validate_observation(observation, self.schema)

    def test_parse_sensor_line_rejects_invalid_shapes(self) -> None:
        invalid_cases = {
            "malformed field": "TEMP23.4;HUM=51.2;TS=2026-03-29T11:12:13Z\n",
            "missing TEMP": "HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "missing HUM": "TEMP=23.4;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "missing TS": "TEMP=23.4;HUM=51.2;PRESS=1008.7\n",
            "duplicate field": "TEMP=23.4;TEMP=24.1;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "unexpected extra field": "TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z;ALT=1.2\n",
        }

        for expected, line in invalid_cases.items():
            with self.subTest(case=expected):
                with self.assertRaises(ValueError) as error:
                    parse_sensor_line(line)
                self.assertIn(expected.split()[0], str(error.exception))

    def test_ingest_stream_rejects_invalid_ranges_with_structured_logs(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        lines = [
            "TEMP=23.4;HUM=101;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "TEMP=23.4;HUM=51.2;PRESS=1200;TS=2026-03-29T11:12:13Z\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            rejection_log_path = temp_path / "rejected-lines.jsonl"
            latest_path = temp_path / "latest-observation.json"
            ingest_stream(
                lines,
                device="/dev/ttyUSB0",
                sensor_id="arduino-ttyUSB0",
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=self.schema,
                stderr=errors,
                stdout=output,
            )

            self.assertFalse(log_path.exists())
            self.assertFalse(latest_path.exists())
            rejection_events = [
                json.loads(line) for line in rejection_log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rejection_events), 2)
            self.assertTrue(all(event["event"] == "rejected_sensor_line" for event in rejection_events))
            self.assertTrue(all(event["loggedAt"].endswith("Z") for event in rejection_events))
            self.assertIn("humidityPct:", rejection_events[0]["error"])
            self.assertIn("pressureHpa:", rejection_events[1]["error"])
            self.assertEqual(output.getvalue(), "")
            stderr_events = [json.loads(line) for line in errors.getvalue().splitlines()]
            self.assertEqual(len(stderr_events), 2)

    def test_ingest_stream_only_writes_valid_lines(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        lines = [
            "TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "TEMP=bad;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n",
            "TEMP=23.4;HUM=51.2;PRESS=1200;TS=2026-03-29T11:12:13Z\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            latest_path = temp_path / "latest-observation.json"
            rejection_log_path = temp_path / "rejected-lines.jsonl"
            with mock.patch("sys.stdout", output):
                ingest_stream(
                    lines,
                    device="/dev/ttyUSB0",
                    sensor_id="arduino-ttyUSB0",
                    log_path=log_path,
                    latest_path=latest_path,
                    rejection_log_path=rejection_log_path,
                    schema=self.schema,
                    stderr=errors,
                )

            written_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(written_lines), 1)
            rejection_events = [json.loads(line) for line in rejection_log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rejection_events), 2)
            self.assertEqual(rejection_events[0]["event"], "rejected_sensor_line")
            self.assertEqual(len(output.getvalue().strip().splitlines()), 1)
            stored = json.loads(written_lines[0])
            self.assertEqual(stored["temperatureC"], 23.4)
            self.assertEqual(stored["pressureHpa"], 1008.7)
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["pressureHpa"], 1008.7)

    def test_latest_file_updates_on_valid_observation(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        lines = [
            "TEMP=21.4;HUM=48.2;PRESS=1004.2;TS=2026-03-29T11:12:13Z\n",
            "TEMP=22.0;HUM=49.1;PRESS=1005.8;TS=2026-03-29T11:13:13Z\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            latest_path = temp_path / "latest-observation.json"
            rejection_log_path = temp_path / "rejected-lines.jsonl"
            ingest_stream(
                lines,
                device="/dev/ttyUSB0",
                sensor_id="arduino-nano-33-ble-sense-rev2",
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=self.schema,
                stderr=errors,
                stdout=output,
            )

            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["temperatureC"], 22.0)
            self.assertEqual(latest["humidityPct"], 49.1)
            self.assertEqual(latest["pressureHpa"], 1005.8)
            self.assertEqual(latest["sensorId"], "arduino-nano-33-ble-sense-rev2")

    def test_ingest_stream_accepts_human_readable_temp_humidity_blocks(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        lines = [
            "Temperature = 19.95 °C\n",
            "Humidity    = 37.28 %\n",
            "\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            latest_path = temp_path / "latest-observation.json"
            rejection_log_path = temp_path / "rejected-lines.jsonl"
            ingest_stream(
                lines,
                device="/dev/ttyACM0",
                sensor_id="arduino-ttyACM0",
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=self.schema,
                stderr=errors,
                stdout=output,
            )

            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["temperatureC"], 19.95)
            self.assertEqual(latest["humidityPct"], 37.28)
            self.assertNotIn("pressureHpa", latest)
            self.assertEqual(latest["sensorId"], "arduino-ttyACM0")
            self.assertEqual(latest["sourcePort"], "/dev/ttyACM0")
            self.assertTrue(latest["observedAt"].endswith("Z"))
            self.assertEqual(errors.getvalue(), "")

    def test_ingest_stream_accepts_merged_human_readable_fragments(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        lines = [
            "Temperature = 19.95 °CHumidity    = 37.28 %\r\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "validated-observations.jsonl"
            latest_path = temp_path / "latest-observation.json"
            rejection_log_path = temp_path / "rejected-lines.jsonl"
            ingest_stream(
                lines,
                device="/dev/ttyACM0",
                sensor_id="arduino-ttyACM0",
                log_path=log_path,
                latest_path=latest_path,
                rejection_log_path=rejection_log_path,
                schema=self.schema,
                stderr=errors,
                stdout=output,
            )

            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["temperatureC"], 19.95)
            self.assertEqual(latest["humidityPct"], 37.28)
            self.assertFalse(rejection_log_path.exists())
            self.assertEqual(errors.getvalue(), "")

    def test_api_latest_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            observation = {
                "@context": {
                    "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                    "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                    "sensorId": "https://schema.org/identifier",
                    "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                    "observedAt": "https://schema.org/observationDate",
                    "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                    "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                    "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa"
                },
                "@type": "SensorObservation",
                "schemaVersion": "sensor-observation-v1",
                "sensorId": "arduino-nano-33-ble-sense-rev2",
                "sourcePort": "/dev/ttyUSB0",
                "observedAt": "2026-03-29T15:30:00Z",
                "temperatureC": 23.4,
                "humidityPct": 51.2,
                "pressureHpa": 1008.7
            }
            latest_path.write_text(json.dumps(observation), encoding="utf-8")
            log_path.write_text(json.dumps(observation) + "\n", encoding="utf-8")

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(latest_path, log_path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            try:
                root = json.loads(urllib.request.urlopen(f"{base_url}/").read().decode("utf-8"))
                health = json.loads(urllib.request.urlopen(f"{base_url}/health").read().decode("utf-8"))
                latest = json.loads(urllib.request.urlopen(f"{base_url}/latest").read().decode("utf-8"))
                temp = json.loads(urllib.request.urlopen(f"{base_url}/latest/temp").read().decode("utf-8"))
                humidity = json.loads(urllib.request.urlopen(f"{base_url}/latest/humidity").read().decode("utf-8"))
                threshold_status = json.loads(
                    urllib.request.urlopen(f"{base_url}/latest/threshold-status").read().decode("utf-8")
                )
                alarm_status = json.loads(
                    urllib.request.urlopen(f"{base_url}/latest/alarm-status").read().decode("utf-8")
                )
                summary = json.loads(urllib.request.urlopen(f"{base_url}/summary?count=1&subject=all").read().decode("utf-8"))

                self.assertTrue(root["ok"])
                self.assertIn("/latest/temp", root["endpoints"])
                self.assertTrue(health["ok"])
                self.assertEqual(latest["pressureHpa"], 1008.7)
                self.assertEqual(temp["value"], 23.4)
                self.assertEqual(humidity["value"], 51.2)
                self.assertEqual(threshold_status["thresholdStatus"]["pressure"]["status"], "normal")
                self.assertFalse(alarm_status["hasActiveAlarms"])
                self.assertEqual(summary["action"], "summarize_window")
                self.assertEqual(summary["summary"]["temperature"]["average"], 23.4)

                request = urllib.request.Request(f"{base_url}/latest", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 405)

                request = urllib.request.Request(f"{base_url}/latest/pressure", method="GET")
                pressure_error = None
                try:
                    urllib.request.urlopen(request)
                except urllib.error.HTTPError as exc:
                    pressure_error = exc
                self.assertIsNone(pressure_error)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_api_pressure_endpoint_reports_unavailable_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            observation = {
                "@context": {
                    "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                    "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                    "sensorId": "https://schema.org/identifier",
                    "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                    "observedAt": "https://schema.org/observationDate",
                    "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                    "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                    "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa"
                },
                "@type": "SensorObservation",
                "schemaVersion": "sensor-observation-v1",
                "sensorId": "arduino-ttyACM0",
                "sourcePort": "/dev/ttyACM0",
                "observedAt": "2026-03-30T11:42:23Z",
                "temperatureC": 19.76,
                "humidityPct": 37.52
            }
            latest_path.write_text(json.dumps(observation), encoding="utf-8")
            log_path.write_text(json.dumps(observation) + "\n", encoding="utf-8")

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(latest_path, log_path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f"{base_url}/latest/pressure")
                self.assertEqual(error.exception.code, 503)
                payload = json.loads(error.exception.read().decode("utf-8"))
                self.assertIn("missing pressure metric", payload["error"])

                threshold_status = json.loads(
                    urllib.request.urlopen(f"{base_url}/latest/threshold-status").read().decode("utf-8")
                )
                self.assertEqual(threshold_status["thresholdStatus"]["pressure"]["status"], "unavailable")
                self.assertFalse(threshold_status["thresholdStatus"]["pressure"]["available"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_webhook_returns_metric_reply_for_json_and_form_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            observations = [
                {
                    "@context": {
                        "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                        "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                        "sensorId": "https://schema.org/identifier",
                        "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                        "observedAt": "https://schema.org/observationDate",
                        "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                        "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                        "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa"
                    },
                    "@type": "SensorObservation",
                    "schemaVersion": "sensor-observation-v1",
                    "sensorId": "arduino-nano-33-ble-sense-rev2",
                    "sourcePort": "/dev/ttyUSB0",
                    "observedAt": "2026-03-29T15:29:00Z",
                    "temperatureC": 22.4,
                    "humidityPct": 50.2,
                    "pressureHpa": 1008.1
                },
                {
                    "@context": {
                        "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                        "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                        "sensorId": "https://schema.org/identifier",
                        "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                        "observedAt": "https://schema.org/observationDate",
                        "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                        "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                        "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa"
                    },
                    "@type": "SensorObservation",
                    "schemaVersion": "sensor-observation-v1",
                    "sensorId": "arduino-nano-33-ble-sense-rev2",
                    "sourcePort": "/dev/ttyUSB0",
                    "observedAt": "2026-03-29T15:30:00Z",
                    "temperatureC": 23.4,
                    "humidityPct": 51.2,
                    "pressureHpa": 1008.7
                },
            ]
            latest_path.write_text(json.dumps(observations[-1]), encoding="utf-8")
            log_path.write_text("\n".join(json.dumps(item) for item in observations) + "\n", encoding="utf-8")

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(latest_path, log_path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            try:
                json_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=json.dumps({"text": "what is the pressure?"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                json_response = json.loads(urllib.request.urlopen(json_request).read().decode("utf-8"))

                form_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=urllib.parse.urlencode({"Body": "temperature"}).encode("utf-8"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                form_response = json.loads(urllib.request.urlopen(form_request).read().decode("utf-8"))

                summary_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=json.dumps({"text": "summary last 2 temperature"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                summary_response = json.loads(urllib.request.urlopen(summary_request).read().decode("utf-8"))

                alarm_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=json.dumps({"text": "any alarm right now?"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                alarm_response = json.loads(urllib.request.urlopen(alarm_request).read().decode("utf-8"))

                self.assertEqual(json_response["action"], "read_latest")
                self.assertIn("1008.7", json_response["reply"])
                self.assertEqual(form_response["action"], "read_latest")
                self.assertIn("23.4", form_response["reply"])
                self.assertEqual(summary_response["action"], "summarize_window")
                self.assertEqual(summary_response["data"]["window"]["actualCount"], 2)
                self.assertEqual(alarm_response["action"], "get_alarm_status")
                self.assertFalse(alarm_response["data"]["hasActiveAlarms"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_webhook_admin_command_updates_temperature_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            log_path = Path(temp_dir) / "validated-observations.jsonl"
            config_path = Path(temp_dir) / "threshold-config.json"
            observation = {
                "@context": {
                    "@vocab": "https://sovereign-sensor-agent.local/ontology#",
                    "schemaVersion": "https://sovereign-sensor-agent.local/ontology#schemaVersion",
                    "sensorId": "https://schema.org/identifier",
                    "sourcePort": "https://sovereign-sensor-agent.local/ontology#sourcePort",
                    "observedAt": "https://schema.org/observationDate",
                    "temperatureC": "https://sovereign-sensor-agent.local/ontology#temperatureC",
                    "humidityPct": "https://sovereign-sensor-agent.local/ontology#humidityPct",
                    "pressureHpa": "https://sovereign-sensor-agent.local/ontology#pressureHpa"
                },
                "@type": "SensorObservation",
                "schemaVersion": "sensor-observation-v1",
                "sensorId": "arduino-ttyACM0",
                "sourcePort": "/dev/ttyACM0",
                "observedAt": "2026-03-31T12:00:00Z",
                "temperatureC": 29.0,
                "humidityPct": 37.52,
                "pressureHpa": 1007.2
            }
            latest_path.write_text(json.dumps(observation), encoding="utf-8")
            log_path.write_text(json.dumps(observation) + "\n", encoding="utf-8")

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(latest_path, log_path, config_path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            try:
                update_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=json.dumps({"text": "@ssa 8888 set temp 30"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                update_response = json.loads(urllib.request.urlopen(update_request).read().decode("utf-8"))
                config_response = json.loads(
                    urllib.request.urlopen(f"{base_url}/config/thresholds").read().decode("utf-8")
                )
                threshold_status = json.loads(
                    urllib.request.urlopen(f"{base_url}/latest/threshold-status").read().decode("utf-8")
                )

                self.assertEqual(update_response["action"], "update_thresholds")
                self.assertIn("30.0 C", update_response["reply"])
                self.assertEqual(config_response["thresholds"]["temperature"]["warningMax"], 30.0)
                self.assertEqual(threshold_status["thresholdStatus"]["temperature"]["status"], "normal")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()