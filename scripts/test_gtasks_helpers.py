#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
NANOBOT_DIR = ROOT_DIR / "deploy" / "nanobot"
sys.path.insert(0, str(NANOBOT_DIR))

from google_chat_sync import post_chat_message
from gtasks_client import normalize_due_datetime


class GoogleTasksHelperTests(unittest.TestCase):
    def test_normalize_due_datetime_keeps_utc_z(self) -> None:
        self.assertEqual(
            normalize_due_datetime("2026-04-02T12:34:56Z"),
            "2026-04-02T12:34:56.000Z",
        )

    def test_normalize_due_datetime_converts_offset_to_utc(self) -> None:
        self.assertEqual(
            normalize_due_datetime("2026-04-02T14:34:56+02:00"),
            "2026-04-02T12:34:56.000Z",
        )

    def test_chat_sync_skips_when_not_configured(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            result = post_chat_message("hello")
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])

    def test_chat_sync_reports_success(self) -> None:
        class _Response:
            status = 200

            def read(self) -> bytes:
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        with mock.patch.dict(
            "os.environ",
            {"GOOGLE_CHAT_WEBHOOK_URL": "https://chat.googleapis.com/v1/spaces/x/messages?key=a&token=b"},
            clear=True,
        ):
            with mock.patch("urllib.request.urlopen", return_value=_Response()):
                result = post_chat_message("task created")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertFalse(result["skipped"])


if __name__ == "__main__":
    unittest.main()
