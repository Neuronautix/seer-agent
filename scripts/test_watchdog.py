#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchdog import check_freshness, main


class TestCheckFreshness(unittest.TestCase):
    def test_returns_no_data_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = check_freshness(Path(tmp) / "latest-observation.json")
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "no_data")
            self.assertIn("checkedAt", result)

    def test_fresh_observation_returns_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            latest_path.write_text(
                json.dumps({"observedAt": recent_ts, "sensorId": "test"}), encoding="utf-8"
            )
            result = check_freshness(latest_path, threshold_seconds=300)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "fresh")
            self.assertTrue(result["isFresh"])
            self.assertLessEqual(result["freshnessAgeSeconds"], 60)
            self.assertEqual(result["lastObservationAt"], recent_ts)
            self.assertEqual(result["sensorId"], "test")

    def test_stale_observation_returns_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            old_ts = "2020-01-01T00:00:00Z"
            latest_path.write_text(json.dumps({"observedAt": old_ts}), encoding="utf-8")
            result = check_freshness(latest_path, threshold_seconds=300)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "stale")
            self.assertFalse(result["isFresh"])
            self.assertGreater(result["freshnessAgeSeconds"], 300)

    def test_exactly_at_threshold_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=299)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            latest_path.write_text(json.dumps({"observedAt": ts}), encoding="utf-8")
            result = check_freshness(latest_path, threshold_seconds=300)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "fresh")

    def test_returns_error_for_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            latest_path.write_text("not valid json {{", encoding="utf-8")
            result = check_freshness(latest_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "error")
            self.assertIn("message", result)

    def test_returns_error_when_observed_at_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            latest_path.write_text(json.dumps({"temperatureC": 22.0}), encoding="utf-8")
            result = check_freshness(latest_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "error")

    def test_returns_error_for_malformed_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            latest_path.write_text(json.dumps({"observedAt": "not-a-timestamp"}), encoding="utf-8")
            result = check_freshness(latest_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "error")

    def test_threshold_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            latest_path.write_text(json.dumps({"observedAt": ts}), encoding="utf-8")
            # With threshold of 30s, a 60s old observation should be stale
            result_stale = check_freshness(latest_path, threshold_seconds=30)
            self.assertFalse(result_stale["ok"])
            self.assertEqual(result_stale["status"], "stale")
            # With threshold of 120s it should be fresh
            result_fresh = check_freshness(latest_path, threshold_seconds=120)
            self.assertTrue(result_fresh["ok"])
            self.assertEqual(result_fresh["status"], "fresh")


class TestWatchdogMain(unittest.TestCase):
    def test_exits_0_for_fresh_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            status_path = Path(tmp) / "watchdog-status.json"
            recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            latest_path.write_text(json.dumps({"observedAt": recent_ts}), encoding="utf-8")
            exit_code = main([
                "--latest-file", str(latest_path),
                "--status-file", str(status_path),
                "--quiet",
            ])
            self.assertEqual(exit_code, 0)
            written = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertTrue(written["ok"])
            self.assertEqual(written["status"], "fresh")

    def test_exits_1_for_stale_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            status_path = Path(tmp) / "watchdog-status.json"
            latest_path.write_text(json.dumps({"observedAt": "2020-01-01T00:00:00Z"}), encoding="utf-8")
            exit_code = main([
                "--latest-file", str(latest_path),
                "--status-file", str(status_path),
                "--quiet",
            ])
            self.assertEqual(exit_code, 1)
            written = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertFalse(written["ok"])
            self.assertEqual(written["status"], "stale")

    def test_exits_1_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = main([
                "--latest-file", str(Path(tmp) / "missing.json"),
                "--status-file", str(Path(tmp) / "status.json"),
                "--quiet",
            ])
            self.assertEqual(exit_code, 1)

    def test_status_file_written_with_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_path = Path(tmp) / "latest-observation.json"
            status_path = Path(tmp) / "watchdog-status.json"
            ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            latest_path.write_text(json.dumps({"observedAt": ts}), encoding="utf-8")
            main([
                "--latest-file", str(latest_path),
                "--status-file", str(status_path),
                "--threshold", "300",
                "--quiet",
            ])
            written = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(written["freshnessThresholdSeconds"], 300)


if __name__ == "__main__":
    unittest.main()
