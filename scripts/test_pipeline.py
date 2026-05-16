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
from api_server import build_health_payload, make_handler
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
                precise_summary = json.loads(
                    urllib.request.urlopen(
                        f"{base_url}/summary?since_minutes=30&bucket_minutes=1&subject=temperature"
                    ).read().decode("utf-8")
                )

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
                self.assertEqual(precise_summary["window"]["requestedSinceMinutes"], 30)
                self.assertEqual(precise_summary["window"]["bucketMinutes"], 1)

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

                precise_summary_request = urllib.request.Request(
                    f"{base_url}/webhook",
                    data=json.dumps({"text": "summary last 10 minutes temperature one reading per minute"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                precise_summary_response = json.loads(
                    urllib.request.urlopen(precise_summary_request).read().decode("utf-8")
                )

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
                self.assertEqual(precise_summary_response["action"], "summarize_window")
                self.assertEqual(precise_summary_response["data"]["window"]["requestedSinceMinutes"], 10)
                self.assertEqual(precise_summary_response["data"]["window"]["bucketMinutes"], 1)
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
                    data=json.dumps({"text": "@ssa CHANGE_ME set temp 30"}).encode("utf-8"),
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


class TestApiHistoryAndCors(unittest.TestCase):
    """Tests for /history endpoint, CORS headers, and /latest envelope."""

    _OBSERVATION = {
        "@context": {"@vocab": "https://sovereign-sensor-agent.local/ontology#"},
        "@type": "SensorObservation",
        "schemaVersion": "sensor-observation-v1",
        "sensorId": "arduino-ttyACM0",
        "sourcePort": "/dev/ttyACM0",
        "observedAt": "2026-04-02T12:00:00Z",
        "temperatureC": 23.4,
        "humidityPct": 51.2,
        "pressureHpa": 1008.7,
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._latest_path = tmp / "latest-observation.json"
        self._log_path = tmp / "obs.jsonl"
        self._latest_path.write_text(json.dumps(self._OBSERVATION), encoding="utf-8")
        self._log_path.write_text(json.dumps(self._OBSERVATION) + "\n", encoding="utf-8")
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self._latest_path, self._log_path),
        )
        thread = Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        self._base = f"http://127.0.0.1:{self._server.server_address[1]}"

    def tearDown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._tmp.cleanup()

    def _get(self, path: str):
        return urllib.request.urlopen(f"{self._base}{path}")

    def test_latest_response_has_ok_true(self) -> None:
        payload = json.loads(self._get("/latest").read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "get_latest")
        # Sensor fields still accessible at top level (backward compatible)
        self.assertEqual(payload["temperatureC"], 23.4)
        self.assertEqual(payload["pressureHpa"], 1008.7)

    def test_history_endpoint_returns_points_array(self) -> None:
        payload = json.loads(
            self._get("/history?since_minutes=60&bucket_minutes=5&subject=all").read().decode("utf-8")
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "get_history")
        self.assertIn("points", payload)
        self.assertIsInstance(payload["points"], list)
        self.assertGreater(payload["window"]["pointCount"], 0)
        point = payload["points"][0]
        self.assertIn("observedAt", point)
        self.assertIn("temperatureC", point)
        self.assertIn("humidityPct", point)

    def test_history_subject_filter_excludes_other_fields(self) -> None:
        payload = json.loads(
            self._get("/history?since_minutes=60&bucket_minutes=5&subject=temperature").read().decode("utf-8")
        )
        self.assertEqual(payload["subject"], "temperature")
        point = payload["points"][0]
        self.assertIn("temperatureC", point)
        self.assertNotIn("humidityPct", point)
        self.assertNotIn("pressureHpa", point)

    def test_history_default_params(self) -> None:
        payload = json.loads(self._get("/history").read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["sinceMinutes"], 60)
        self.assertEqual(payload["window"]["bucketMinutes"], 5)

    def test_cors_header_present_on_get(self) -> None:
        response = self._get("/health")
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("GET", response.headers.get("Access-Control-Allow-Methods", ""))

    def test_cors_options_preflight(self) -> None:
        req = urllib.request.Request(f"{self._base}/latest", method="OPTIONS")
        response = urllib.request.urlopen(req)
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_root_endpoint_list_includes_history_and_config(self) -> None:
        payload = json.loads(self._get("/").read().decode("utf-8"))
        endpoints = payload["endpoints"]
        self.assertTrue(any("/history" in ep for ep in endpoints))
        self.assertTrue(any("/config/thresholds" in ep for ep in endpoints))

    def test_content_type_includes_charset(self) -> None:
        response = self._get("/health")
        ct = response.headers.get("Content-Type", "")
        self.assertIn("application/json", ct)
        self.assertIn("charset=utf-8", ct)


class TestBuildHealthPayload(unittest.TestCase):
    def test_returns_waiting_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            result = build_health_payload(latest_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "waiting_for_data")
            self.assertFalse(result["latestObservationAvailable"])
            self.assertNotIn("freshnessAgeSeconds", result)

    def test_returns_ready_with_freshness_for_recent_observation(self) -> None:
        from datetime import datetime, timezone, timedelta

        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            observation = {"observedAt": recent_ts, "temperatureC": 22.0, "humidityPct": 50.0}
            latest_path.write_text(json.dumps(observation), encoding="utf-8")

            result = build_health_payload(latest_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["latestObservationAvailable"])
            self.assertEqual(result["lastObservationAt"], recent_ts)
            self.assertLessEqual(result["freshnessAgeSeconds"], 60)
            self.assertTrue(result["isFresh"])

    def test_returns_stale_for_old_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            old_ts = "2020-01-01T00:00:00Z"
            observation = {"observedAt": old_ts, "temperatureC": 22.0, "humidityPct": 50.0}
            latest_path.write_text(json.dumps(observation), encoding="utf-8")

            result = build_health_payload(latest_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "stale")
            self.assertTrue(result["latestObservationAvailable"])
            self.assertFalse(result["isFresh"])
            self.assertGreater(result["freshnessAgeSeconds"], 300)

    def test_falls_back_gracefully_for_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            latest_path.write_text("not valid json", encoding="utf-8")

            result = build_health_payload(latest_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["latestObservationAvailable"])

    def test_falls_back_gracefully_when_observed_at_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_path = Path(temp_dir) / "latest-observation.json"
            latest_path.write_text(json.dumps({"temperatureC": 22.0}), encoding="utf-8")

            result = build_health_payload(latest_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["latestObservationAvailable"])
            self.assertNotIn("freshnessAgeSeconds", result)


class TestNewApiEndpoints(unittest.TestCase):
    """Tests for /metrics, /export, /diagnostics/rejected, and webhook rate limiting."""

    _OBS = {
        "@context": {"@vocab": "https://sovereign-sensor-agent.local/ontology#"},
        "@type": "SensorObservation",
        "schemaVersion": "sensor-observation-v1",
        "sensorId": "arduino-ttyACM0",
        "sourcePort": "/dev/ttyACM0",
        "observedAt": "2026-04-02T12:00:00Z",
        "temperatureC": 23.4,
        "humidityPct": 51.2,
        "pressureHpa": 1008.7,
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._latest = tmp / "latest.json"
        self._log = tmp / "obs.jsonl"
        self._rejected = tmp / "rejected.jsonl"
        self._latest.write_text(json.dumps(self._OBS), encoding="utf-8")
        self._log.write_text(json.dumps(self._OBS) + "\n", encoding="utf-8")
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self._latest, self._log, rejected_path=self._rejected),
        )
        Thread(target=self._server.serve_forever, daemon=True).start()
        self._base = f"http://127.0.0.1:{self._server.server_address[1]}"

    def tearDown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._tmp.cleanup()

    def _get(self, path: str):
        return urllib.request.urlopen(f"{self._base}{path}")

    def test_metrics_endpoint_returns_prometheus_text(self) -> None:
        resp = self._get("/metrics")
        ct = resp.headers.get("Content-Type", "")
        self.assertIn("text/plain", ct)
        body = resp.read().decode("utf-8")
        self.assertIn("ssa_temperature_celsius 23.4", body)
        self.assertIn("ssa_humidity_percent 51.2", body)
        self.assertIn("ssa_alarm_active", body)
        self.assertIn("# HELP", body)
        self.assertIn("# TYPE", body)

    def test_export_csv_returns_valid_csv(self) -> None:
        resp = self._get("/export?since_minutes=60&format=csv&subject=all")
        ct = resp.headers.get("Content-Type", "")
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn("text/csv", ct)
        self.assertIn("attachment", cd)
        self.assertIn(".csv", cd)
        body = resp.read().decode("utf-8")
        self.assertIn("observedAt", body)
        self.assertIn("temperatureC", body)
        self.assertIn("23.4", body)

    def test_export_jsonl_returns_newline_delimited_json(self) -> None:
        resp = self._get("/export?since_minutes=60&format=jsonl&subject=temperature")
        ct = resp.headers.get("Content-Type", "")
        self.assertIn("ndjson", ct)
        lines = [l for l in resp.read().decode("utf-8").splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)
        parsed = json.loads(lines[0])
        self.assertIn("temperatureC", parsed)
        self.assertNotIn("humidityPct", parsed)

    def test_diagnostics_rejected_returns_empty_when_no_file(self) -> None:
        payload = json.loads(self._get("/diagnostics/rejected").read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["totalRejected"], 0)
        self.assertEqual(payload["returnedCount"], 0)
        self.assertEqual(payload["entries"], [])

    def test_diagnostics_rejected_uses_handler_rejected_path(self) -> None:
        self._rejected.write_text(
            '{"raw":"bad line","error":"invalid"}\n',
            encoding="utf-8",
        )
        payload = json.loads(self._get("/diagnostics/rejected").read().decode("utf-8"))
        self.assertEqual(payload["totalRejected"], 1)
        self.assertEqual(payload["returnedCount"], 1)
        self.assertEqual(payload["entries"][0]["error"], "invalid")

    def test_root_lists_new_endpoints(self) -> None:
        payload = json.loads(self._get("/").read().decode("utf-8"))
        endpoints = payload["endpoints"]
        self.assertTrue(any("/metrics" in e for e in endpoints))
        self.assertTrue(any("/export" in e for e in endpoints))
        self.assertTrue(any("/diagnostics/rejected" in e for e in endpoints))

    def test_webhook_rate_limit_returns_429(self) -> None:
        from api_server import _rate_limit_state, _rate_limit_lock
        # Clear state before test to avoid cross-test contamination
        with _rate_limit_lock:
            _rate_limit_state.clear()

        data = json.dumps({"text": "temperature"}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        responses = []
        for _ in range(15):
            req = urllib.request.Request(
                f"{self._base}/webhook", data=data, headers=headers, method="POST"
            )
            try:
                r = urllib.request.urlopen(req)
                responses.append(r.status)
            except urllib.error.HTTPError as exc:
                responses.append(exc.code)
        self.assertIn(429, responses)


class TestRotateLogs(unittest.TestCase):
    """Tests for scripts/rotate_logs.py."""

    def _make_obs(self, observed_at: str) -> dict:
        return {
            "@type": "SensorObservation",
            "temperatureC": 22.0,
            "humidityPct": 50.0,
            "observedAt": observed_at,
        }

    def test_rotate_dry_run_does_not_modify_file(self) -> None:
        from rotate_logs import rotate
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "obs.jsonl"
            obs = [self._make_obs(f"2026-01-0{i+1}T12:00:00Z") for i in range(3)]
            log.write_text("\n".join(json.dumps(o) for o in obs) + "\n", encoding="utf-8")
            original = log.read_text(encoding="utf-8")
            result = rotate(log, Path(tmp) / "archive", keep_days=30, thin_after_days=7, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertEqual(log.read_text(encoding="utf-8"), original)

    def test_rotate_archives_old_observations(self) -> None:
        from rotate_logs import rotate
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "obs.jsonl"
            archive_dir = Path(tmp) / "archive"
            recent_obs = self._make_obs("2026-04-02T12:00:00Z")
            old_obs = self._make_obs("2026-01-01T12:00:00Z")
            log.write_text(
                json.dumps(recent_obs) + "\n" + json.dumps(old_obs) + "\n",
                encoding="utf-8",
            )
            result = rotate(log, archive_dir, keep_days=30, thin_after_days=7, dry_run=False)
            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["total_out"], 1)
            gz_files = list(archive_dir.glob("*.jsonl.gz"))
            self.assertEqual(len(gz_files), 1)
            with gzip.open(gz_files[0], "rt", encoding="utf-8") as gz:
                archived_content = gz.read()
            self.assertIn("2026-01-01", archived_content)

    def test_rotate_skips_when_log_missing(self) -> None:
        from rotate_logs import rotate
        with tempfile.TemporaryDirectory() as tmp:
            result = rotate(Path(tmp) / "missing.jsonl", Path(tmp) / "archive",
                            keep_days=30, thin_after_days=7, dry_run=False)
            self.assertTrue(result.get("skipped"))

    def test_rotate_thins_mid_range_to_one_per_hour(self) -> None:
        from rotate_logs import rotate
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "obs.jsonl"
            archive_dir = Path(tmp) / "archive"
            # 4 observations in same hour, 10 days ago (in thin range)
            observations = [
                self._make_obs("2026-03-23T10:00:00Z"),
                self._make_obs("2026-03-23T10:15:00Z"),
                self._make_obs("2026-03-23T10:30:00Z"),
                self._make_obs("2026-03-23T10:45:00Z"),
            ]
            log.write_text("\n".join(json.dumps(o) for o in observations) + "\n", encoding="utf-8")
            result = rotate(log, archive_dir, keep_days=30, thin_after_days=7, dry_run=False)
            # All 4 are in the thin window (10d > 7d, < 30d), so thinned to 1
            self.assertEqual(result["kept_thinned"], 1)
            self.assertEqual(result["total_out"], 1)


class TestCheckEnv(unittest.TestCase):
    """Tests for scripts/check_env.py."""

    def test_check_logs_dir_passes_for_writable_dir(self) -> None:
        from check_env import check_logs_dir
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("check_env.ROOT_DIR", Path(tmp)):
                self.assertTrue(check_logs_dir())

    def test_admin_password_warns_but_passes_on_default(self) -> None:
        from check_env import check_admin_password
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"SSA_ADMIN_PASSWORD": "CHANGE_ME"}, clear=False):
            self.assertTrue(check_admin_password())

    def test_admin_password_ok_when_changed(self) -> None:
        from check_env import check_admin_password
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"SSA_ADMIN_PASSWORD": "supersecret"}, clear=False):
            self.assertTrue(check_admin_password())

    def test_run_checks_all_includes_service_specific_checks(self) -> None:
        from check_env import run_checks
        import unittest.mock as mock

        calls: list[str] = []

        with mock.patch("check_env.check_logs_dir", return_value=True), \
             mock.patch("check_env.check_python_venv", return_value=True), \
             mock.patch("check_env.check_threshold_config", return_value=True), \
             mock.patch("check_env.check_admin_password", return_value=True), \
             mock.patch("check_env.check_serial_device", side_effect=lambda: calls.append("serial") or True), \
             mock.patch("check_env.check_gemini_api_key", side_effect=lambda: calls.append("gemini") or True), \
             mock.patch("check_env.check_whatsapp_allow_from", side_effect=lambda: calls.append("allow") or True):
            self.assertTrue(run_checks("all"))

        self.assertEqual(calls, ["serial", "gemini", "allow"])


class TestStatusCommand(unittest.TestCase):
    """Tests for @ssa CHANGE_ME status and @ssa CHANGE_ME health commands."""

    _OBS = {
        "@type": "SensorObservation",
        "sensorId": "arduino-test",
        "observedAt": "2026-04-02T12:00:00Z",
        "temperatureC": 23.4,
        "humidityPct": 51.2,
        "pressureHpa": 1008.7,
    }

    def test_status_command_returns_system_status(self) -> None:
        from alarm_runtime import handle_admin_message
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            latest = tmp_path / "latest.json"
            log = tmp_path / "obs.jsonl"
            latest.write_text(json.dumps(self._OBS), encoding="utf-8")
            log.write_text(json.dumps(self._OBS) + "\n", encoding="utf-8")
            response = handle_admin_message(
                "@ssa CHANGE_ME status",
                log_path=log,
                latest_path=latest,
                rejected_path=tmp_path / "rejected.jsonl",
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["action"], "system_status")
            reply = response["reply"]
            self.assertIn("Status at", reply)
            self.assertIn("arduino-test", reply)
            self.assertIn("Log:", reply)

    def test_status_handles_non_object_latest_payload(self) -> None:
        from alarm_runtime import handle_admin_message
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            latest = tmp_path / "latest.json"
            latest.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            response = handle_admin_message(
                "@ssa CHANGE_ME status",
                latest_path=latest,
                log_path=tmp_path / "obs.jsonl",
                rejected_path=tmp_path / "rejected.jsonl",
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertIn("invalid latest observation format", response["reply"])

    def test_health_alias_works(self) -> None:
        from alarm_runtime import handle_admin_message
        response = handle_admin_message("@ssa CHANGE_ME health")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["action"], "system_status")

    def test_status_included_in_help(self) -> None:
        from alarm_runtime import handle_admin_message
        response = handle_admin_message("@ssa CHANGE_ME help")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("status", response["reply"])


if __name__ == "__main__":
    unittest.main()