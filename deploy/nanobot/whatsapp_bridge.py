#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from nanobot.channels.whatsapp import _ensure_bridge_setup
from nanobot.config.loader import set_config_path
from nanobot.config.paths import get_runtime_subdir


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        print("usage: whatsapp_bridge.py <config-path>", file=sys.stderr)
        return 2

    config_path = Path(args[0]).expanduser().resolve()
    set_config_path(config_path)

    bridge_dir = _ensure_bridge_setup()
    npm_path = shutil.which("npm")
    if not npm_path:
        print("npm not found. Please install Node.js >= 18.", file=sys.stderr)
        return 1

    env = {**os.environ}
    bridge_token = os.environ.get("NANOBOT_WHATSAPP_BRIDGE_TOKEN", "")
    if bridge_token:
        env["BRIDGE_TOKEN"] = bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    if _port_in_use("127.0.0.1", 3001):
        print(
            "WhatsApp bridge already appears to be running on ws://127.0.0.1:3001. "
            "Reuse that process or stop it before starting a new bridge.",
            file=sys.stderr,
        )
        return 1

    completed = subprocess.run([npm_path, "start"], cwd=bridge_dir, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())