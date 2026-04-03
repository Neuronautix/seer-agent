#!/usr/bin/env python3
"""
Pre-flight environment checker for Sovereign Sensor Agent services.

Used as ExecStartPre= in systemd service files.  Also callable via:
  ssa check [--service ingest|api|nanobot]

Exit codes:
  0  all checks pass
  1  one or more required checks failed (service should not start)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_ADMIN_PASSWORD = "8888"


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}", file=sys.stderr)


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)


def check_logs_dir() -> bool:
    logs_dir = ROOT_DIR / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        test_file = logs_dir / ".write_check"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        _ok(f"logs/ directory is writable ({logs_dir})")
        return True
    except OSError as exc:
        _fail(f"logs/ directory is not writable: {exc}")
        return False


def check_admin_password() -> bool:
    password = os.environ.get("SSA_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    if password == DEFAULT_ADMIN_PASSWORD:
        _warn(
            f"SSA_ADMIN_PASSWORD is still the default '{DEFAULT_ADMIN_PASSWORD}'. "
            "Change it in nanobot.env before exposing the webhook publicly."
        )
    else:
        _ok("SSA_ADMIN_PASSWORD is set to a non-default value")
    return True  # warning only, not a failure


def check_serial_device() -> bool:
    device = os.environ.get("SSA_SERIAL_DEVICE", "/dev/ttyACM0")
    path = Path(device)
    if not path.exists():
        _fail(f"Serial device not found: {device}  (is the Arduino connected?)")
        return False
    if not os.access(device, os.R_OK):
        _fail(
            f"Serial device exists but is not readable: {device}  "
            "(add your user to the 'dialout' group: sudo usermod -aG dialout $USER)"
        )
        return False
    _ok(f"Serial device accessible: {device}")
    return True


def check_python_venv() -> bool:
    venv_python = ROOT_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        _fail(
            f"Virtual environment not found at {venv_python}. "
            "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        )
        return False
    _ok(f"Virtual environment found: {venv_python}")
    return True


def check_gemini_api_key() -> bool:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        _fail("GEMINI_API_KEY is not set. Add it to deploy/nanobot/nanobot.env")
        return False
    _ok("GEMINI_API_KEY is set")
    return True


def check_whatsapp_allow_from() -> bool:
    allow_from = os.environ.get("NANOBOT_WHATSAPP_ALLOW_FROM", "").strip()
    if not allow_from and os.environ.get("NANOBOT_ENABLE_WHATSAPP", "false") == "true":
        _warn(
            "NANOBOT_ENABLE_WHATSAPP=true but NANOBOT_WHATSAPP_ALLOW_FROM is empty. "
            "No inbound messages will be accepted."
        )
    else:
        _ok("WhatsApp allowlist configured (or WhatsApp disabled)")
    return True  # warning only


def check_threshold_config() -> bool:
    config_path = ROOT_DIR / "threshold-config.json"
    if config_path.exists():
        try:
            import json
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                _fail("threshold-config.json is not a JSON object")
                return False
            _ok(f"threshold-config.json is valid ({config_path})")
        except Exception as exc:
            _fail(f"threshold-config.json is invalid: {exc}")
            return False
    else:
        _ok("threshold-config.json not present (defaults will be used)")
    return True


def run_checks(service: str) -> bool:
    print(f"=== Pre-flight check: {service} service ===")
    results: list[bool] = []

    # Checks common to all services
    results.append(check_logs_dir())
    results.append(check_python_venv())
    results.append(check_threshold_config())
    results.append(check_admin_password())

    if service in {"ingest", "all"}:
        results.append(check_serial_device())

    if service in {"nanobot", "all"}:
        results.append(check_gemini_api_key())
        results.append(check_whatsapp_allow_from())

    passed = all(results)
    print()
    if passed:
        print(f"All checks passed for {service} service.")
    else:
        print(f"One or more checks FAILED for {service} service — aborting startup.", file=sys.stderr)
    return passed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--service",
        choices=["ingest", "api", "nanobot", "all"],
        default="all",
        help="Which service is being started (default: all — runs all checks)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ok = run_checks(args.service)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
