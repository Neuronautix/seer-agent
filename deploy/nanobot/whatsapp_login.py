#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from nanobot.cli.commands import channels_login
from nanobot.config.loader import set_config_path


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        print("usage: whatsapp_login.py <config-path>", file=sys.stderr)
        return 2

    config_path = Path(args[0]).expanduser().resolve()
    set_config_path(config_path)
    channels_login("whatsapp", force=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())