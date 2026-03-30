#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_PATH = ROOT_DIR / "logs" / "latest-observation.json"
DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("latest observation must be a JSON object")
    return payload


def _read_last_jsonl_record(path: Path) -> dict[str, Any]:
    latest_line: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                latest_line = stripped

    if latest_line is None:
        raise ValueError("validated observation log is empty")

    payload = json.loads(latest_line)
    if not isinstance(payload, dict):
        raise ValueError("latest validated observation must be a JSON object")
    return payload


def load_latest_observation(
    latest_path: Path = DEFAULT_LATEST_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
) -> dict[str, Any]:
    if latest_path.exists():
        return _read_json_file(latest_path)
    if log_path.exists():
        return _read_last_jsonl_record(log_path)
    raise FileNotFoundError("no validated observation file is available")


def main() -> int:
    try:
        observation = load_latest_observation()
        json.dump({"ok": True, "observation": observation}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())