# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## Google Tasks MCP

- Enabled only when `NANOBOT_ENABLE_GOOGLE_TASKS=true`.
- Requires OAuth token file (default `deploy/nanobot/google-tasks-token.json`).
- Task events can optionally be mirrored to Google Chat via `GOOGLE_CHAT_WEBHOOK_URL`.
