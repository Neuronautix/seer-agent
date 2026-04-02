#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def post_chat_message(text: str) -> dict[str, object]:
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return {"ok": False, "skipped": True, "reason": "GOOGLE_CHAT_WEBHOOK_URL not configured"}

    payload = json.dumps({"text": text}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )
    timeout_seconds = float(os.environ.get("GOOGLE_CHAT_TIMEOUT_SECONDS", "5"))

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read()
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "skipped": False,
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "skipped": False,
            "status": exc.code,
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "skipped": False, "error": str(exc)}
