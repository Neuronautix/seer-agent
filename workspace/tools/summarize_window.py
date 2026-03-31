#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from observation_analysis import load_config, read_recent_observations, summarize_window

DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"
DEFAULT_CONFIG = load_config()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize the last N validated observations")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--subject", choices=["all", "temperature", "humidity", "pressure"], default="all")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observations = read_recent_observations(Path(args.log_file), count=args.count)
        payload = summarize_window(observations, DEFAULT_CONFIG, requested_count=args.count, subject=args.subject)
        payload.update(
            {
                "sensorId": observations[-1].get("sensorId"),
                "sourcePort": observations[-1].get("sourcePort"),
                "schemaVersion": observations[-1].get("schemaVersion"),
            }
        )
        json.dump(payload, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())