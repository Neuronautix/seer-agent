#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path

from gtasks_client import DEFAULT_TOKEN_PATH, SCOPES


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "Missing google-auth-oauthlib dependency. Install requirements.txt and retry."
        ) from exc

    client_secret_file = os.environ.get("GOOGLE_TASKS_CLIENT_SECRET_FILE", "").strip()
    if not client_secret_file:
        raise SystemExit(
            "GOOGLE_TASKS_CLIENT_SECRET_FILE is required and must point to your OAuth client JSON file."
        )

    token_path = Path(os.environ.get("GOOGLE_TASKS_TOKEN_FILE", str(DEFAULT_TOKEN_PATH))).expanduser()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    port = int(os.environ.get("GOOGLE_TASKS_OAUTH_PORT", "8765"))
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=port)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "tokenPath": str(token_path),
                "scopes": SCOPES,
                "message": "Google Tasks OAuth completed.",
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
