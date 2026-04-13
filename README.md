# Sovereign Sensor Agent

A local-first, privacy-preserving system for reading environmental sensor data from an Arduino, validating it through a deterministic Python pipeline, and exposing it through a constrained read-only interface.

**Core principle:** the Python pipeline is the only source of truth. Any LLM or chat interface is limited to reading validated local files — it does not generate observations, write to storage, or access the serial port.

Operational runbook and day-to-day commands are in [OPERATIONS.md](OPERATIONS.md).

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Repository Structure](#repository-structure)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Running the Pipeline](#running-the-pipeline)
7. [Testing](#testing)
8. [Arduino Serial Format](#arduino-serial-format)
9. [API Reference](#api-reference)
10. [Validation and Safety Model](#validation-and-safety-model)
11. [WhatsApp Integration](#whatsapp-integration)
12. [Deployment on Raspberry Pi](#deployment-on-raspberry-pi)
13. [Default Thresholds](#default-thresholds)
14. [Logs and Data Files](#logs-and-data-files)
15. [Troubleshooting](#troubleshooting)
16. [Roadmap](#roadmap)
17. [Contributing](#contributing)
18. [License](#license)

---

## Overview

The system performs five jobs:

1. Reads serial sensor lines from an Arduino (or compatible serial device).
2. Converts them into a canonical JSON-LD observation.
3. Validates the observation against a JSON Schema and runtime range checks.
4. Persists validated data to local append-only logs and exposes a read-only HTTP API.
5. Optionally allows a constrained chat layer (WhatsApp via Nanobot) to answer sensor questions using only validated local files.

### What this is not

- Not a hosted service or cloud deployment.
- Not a write-capable automation agent.
- Not a system that lets an LLM bypass the validation pipeline.
- Not a complete production deployment — webhook authentication, public ingress, and provider configuration are left to the deployer.

---

## Architecture

```
Arduino (serial)
       │
       ▼
read_serial.py          ← parses canonical or human-readable lines
       │
       ▼
build_observation.py    ← builds canonical JSON-LD with UTC timestamp
       │
       ▼
ontology_guard.py       ← JSON Schema + runtime range validation
       │
       ├──► logs/validated-observations.jsonl   (append-only history)
       └──► logs/latest-observation.json        (latest snapshot)
                │
                ▼
     api_server.py  /  workspace/tools  /  supervisor.py
                │
                ▼
     (optional) Nanobot MCP layer → LLM → WhatsApp
```

### Core Components

**Deterministic pipeline** (`scripts/`):
- `read_serial.py` — ingests and parses Arduino serial lines
- `build_observation.py` — constructs canonical JSON-LD observations
- `ontology_guard.py` — enforces schema and runtime range constraints
- `api_server.py` — read-only HTTP API (port 8080) and minimal webhook endpoint
- `supervisor.py` — CLI for querying latest values and threshold status
- `alarm_runtime.py` — threshold evaluation and alarm state logic

**LLM boundary** (`workspace/`):
- `tools/` — read-only Python tool wrappers (MCP-compatible)
- `POLICY.md` — enforced behavioral rules for any LLM connected to this system
- `WHATSAPP_SYSTEM_PROMPT.md` — system prompt for WhatsApp-facing agents

**Schemas** (`schemas/`):
- `sensor-observation-v1.json` — canonical observation contract (JSON Schema Draft 2020-12)
- `agent-action-v1.json` — constrained agent intent schema

**Deployment** (`deploy/`):
- `systemd/` — service and timer unit files
- `nanobot/` — Nanobot agent, MCP server, WhatsApp bridge, and startup scripts

---

## Repository Structure

```text
sovereign-sensor-agent/
├── scripts/
│   ├── read_serial.py
│   ├── build_observation.py
│   ├── ontology_guard.py
│   ├── api_server.py
│   ├── supervisor.py
│   ├── alarm_runtime.py
│   ├── observation_analysis.py
│   ├── ssa                         # CLI wrapper for systemd service control
│   ├── test_pipeline.py
│   ├── test_supervisor.py
│   └── test_alarm_runtime.py
├── workspace/
│   ├── POLICY.md
│   ├── WHATSAPP_SYSTEM_PROMPT.md
│   └── tools/
│       ├── get_latest_observation.py
│       ├── get_metric.py
│       ├── get_threshold_status.py
│       ├── get_alarm_status.py
│       └── summarize_window.py
├── schemas/
│   ├── sensor-observation-v1.json
│   └── agent-action-v1.json
├── deploy/
│   ├── systemd/
│   └── nanobot/
│       ├── mcp_server.py
│       ├── start_nanobot.sh
│       ├── config.template.json
│       ├── nanobot.env.example
│       ├── whatsapp_alarm_daemon.py
│       ├── whatsapp_bridge.py
│       ├── test_tool_layer.py
│       └── test_agent_answers.sh
├── logs/                           # runtime data, git-ignored
├── requirements.txt
├── OPERATIONS.md
└── README.md
```

---

## Requirements

- Linux (Raspberry Pi OS or similar).
- Python 3.11+.
- An Arduino or compatible serial device.
- The active user must have read permissions on the serial device (typically via the `dialout` group).
- Node.js 18+ and `npm` — required only for the WhatsApp bridge.

---

## Installation

```bash
git clone <repo-url> sovereign-sensor-agent
cd sovereign-sensor-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Confirm your serial device is visible:

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

If you see a permission error on the serial device, add your user to the `dialout` group:

```bash
sudo usermod -aG dialout $USER
# log out and back in for the change to take effect
```

---

## Running the Pipeline

All commands below are run from the repository root.

### Ingest live Arduino data

```bash
./.venv/bin/python scripts/read_serial.py --device /dev/ttyACM0 --baud 9600 --sensor-id arduino-ttyACM0
```

Replace `/dev/ttyACM0` with your actual device path if different (e.g. `/dev/ttyUSB0`).

### Test ingestion from stdin

```bash
printf 'TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z\n' \
  | ./.venv/bin/python scripts/read_serial.py --stdin --max-lines 1
```

Without pressure:

```bash
printf 'TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z\n' \
  | ./.venv/bin/python scripts/read_serial.py --stdin --max-lines 1
```

### Start the read-only API

```bash
./.venv/bin/python scripts/api_server.py --host 0.0.0.0 --port 8080
```

### Query the API locally

```bash
curl -s http://127.0.0.1:8080/
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/latest
curl -s http://127.0.0.1:8080/latest/temp
curl -s http://127.0.0.1:8080/latest/humidity
curl -s http://127.0.0.1:8080/latest/pressure
curl -s http://127.0.0.1:8080/latest/threshold-status
```

### Query using the supervisor CLI

```bash
./.venv/bin/python scripts/supervisor.py read_latest temperature
./.venv/bin/python scripts/supervisor.py read_latest humidity
./.venv/bin/python scripts/supervisor.py read_latest pressure
./.venv/bin/python scripts/supervisor.py get_threshold_status
```

### Query the workspace tool wrappers directly

```bash
./.venv/bin/python workspace/tools/get_latest_observation.py
./.venv/bin/python workspace/tools/get_metric.py temperature
./.venv/bin/python workspace/tools/get_metric.py pressure
./.venv/bin/python workspace/tools/get_threshold_status.py
```

---

## Testing

Run the test suites from the repository root:

```bash
./.venv/bin/python scripts/test_pipeline.py       # 40+ unit tests
./.venv/bin/python scripts/test_supervisor.py     # supervisor tests
./.venv/bin/python scripts/test_alarm_runtime.py  # alarm tests
```

Run the MCP tool layer test (requires a running API server):

```bash
./.venv/bin/python deploy/nanobot/test_tool_layer.py
```

There is no CI. Run the relevant test file after any change to pipeline logic.

---

## Arduino Serial Format

The ingestion script accepts two formats.

**Canonical (preferred):**

```text
TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z
TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z
```

**Human-readable (also accepted):**

```text
Temperature = 19.95 °C
Humidity    = 37.28 %
```

Rules:

- `TEMP`, `HUM`, and `TS` are required in canonical format.
- `PRESS` is optional.
- Duplicate fields, unknown fields, and non-numeric values are rejected.
- All observations must pass JSON Schema validation and runtime range checks before being persisted.

Runtime range constraints enforced by `ontology_guard.py`:

| Field | Min | Max |
|-------|-----|-----|
| `temperatureC` | −40 °C | 85 °C |
| `humidityPct` | 0 % | 100 % |
| `pressureHpa` | 300 hPa | 1100 hPa |

Timestamps must be UTC ISO-8601 ending in `Z`.

---

## API Reference

### `GET /`

Service summary with available endpoints.

### `GET /health`

Returns API readiness and observation freshness.

```json
{
  "ok": true,
  "status": "ready",
  "latestObservationAvailable": true,
  "lastObservationAt": "2026-04-01T10:00:00Z",
  "freshnessAgeSeconds": 42,
  "isFresh": true
}
```

`status` values:
- `"ready"` — data is under 5 minutes old
- `"stale"` — data exists but is older than 5 minutes
- `"waiting_for_data"` — no observation file has been written yet

### `GET /latest`

Full latest validated observation.

### `GET /latest/temp`

Latest validated temperature.

### `GET /latest/humidity`

Latest validated humidity.

### `GET /latest/pressure`

Latest validated pressure when the sensor provides it. Returns an unavailable error if pressure is absent from the latest observation.

### `GET /latest/threshold-status`

Threshold evaluations for all configured metrics. Missing metrics are reported as `unavailable`.

### `GET /config/thresholds`

Currently active threshold configuration.

### `POST /webhook`

Minimal local chat endpoint for provider integration. Accepts `application/json` or `application/x-www-form-urlencoded`.

Sensor query example:

```bash
curl -s -X POST http://127.0.0.1:8080/webhook \
  -H 'Content-Type: application/json' \
  -d '{"text":"what is the temperature?"}'
```

Administrative threshold commands go through the same endpoint and require the value of `SSA_ADMIN_PASSWORD`:

```bash
curl -s -X POST http://127.0.0.1:8080/webhook \
  -H 'Content-Type: application/json' \
  -d '{"text":"<admin-password> thresholds"}'

curl -s -X POST http://127.0.0.1:8080/webhook \
  -H 'Content-Type: application/json' \
  -d '{"text":"<admin-password> set temp 30"}'

curl -s -X POST http://127.0.0.1:8080/webhook \
  -H 'Content-Type: application/json' \
  -d '{"text":"<admin-password> set temp critical 35"}'
```

Replace `<admin-password>` with the value you set in `SSA_ADMIN_PASSWORD`.

---

## Validation and Safety Model

This project enforces a strict boundary between deterministic code and LLM behavior:

- Canonical observations are created only by Python pipeline code.
- LLM responses must come from validated local files only.
- The LLM must never read from `/dev/tty*` devices directly.
- The LLM must never write to logs, schemas, or any storage.
- Non-read-only actions are returned as proposals only, subject to deterministic review.

This separation makes the system easier to audit and safer to expose through a messaging interface.

---

## WhatsApp Integration

The repository includes an optional local WhatsApp bridge built on [Nanobot](https://github.com/nanobot-ai/nanobot) and WhatsApp Web. Everything runs on-device with no cloud relay.

### Prerequisites

- A Gemini API key.
- Node.js 18+ and `npm` installed on the host device.

### Configuration

Copy the example env file and set your values:

```bash
cp deploy/nanobot/nanobot.env.example deploy/nanobot/nanobot.env
```

Key variables (see `nanobot.env.example` for the full list):

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Your Gemini API key |
| `NANOBOT_ENABLE_WHATSAPP` | Set `true` to enable the WhatsApp bridge |
| `NANOBOT_WHATSAPP_ALLOW_FROM` | Allowlisted sender IDs (comma-separated; do not use `*`) |
| `SSA_ADMIN_PASSWORD` | Password for admin threshold commands — **change this before use** |
| `SSA_WHATSAPP_ALERT_TO` | WhatsApp ID that receives automatic threshold alarm notifications |

Never commit `nanobot.env`. It is already in `.gitignore`.

### Supported queries

Messages must start with `@ssa`. Messages without this prefix are silently ignored.

```
@ssa what is the temperature?
@ssa what is the humidity?
@ssa what is the pressure?
@ssa what is the threshold status?
@ssa summarize last 10 readings
@ssa summarize last 30 minutes
```

### Google Tasks commands (when enabled)

```
@ssa add task buy filters tomorrow 09:00
@ssa list tasks
@ssa complete task TASK_ID
```

### Admin threshold commands

```
@ssa <admin-password> thresholds
@ssa <admin-password> set temp 30
@ssa <admin-password> set temp critical 35
```

Replace `<admin-password>` with the value of `SSA_ADMIN_PASSWORD` from your `nanobot.env`.

### What the agent cannot do

- Write to sensor logs or modify observations.
- Control hardware or trigger actions on the device.
- Answer questions outside sensor data (and optionally Google Tasks).
- Execute shell commands (disabled in Nanobot config).

### WhatsApp setup steps

1. Configure `nanobot.env` as described above.
2. Verify the read-only tool layer (no Gemini key required):
   ```bash
   ./.venv/bin/python deploy/nanobot/test_tool_layer.py
   ```
3. Link your WhatsApp account:
   ```bash
   ./deploy/nanobot/start_nanobot.sh whatsapp-login
   ```
   Scan the QR code for WhatsApp Web.
4. Install systemd services and start the full stack:
   ```bash
   ./scripts/ssa install
   ssa up
   ```

For Google Tasks integration, set `NANOBOT_ENABLE_GOOGLE_TASKS=true`, provide `GOOGLE_TASKS_CLIENT_SECRET_FILE`, then run:

```bash
ssa tasks-login
```

### Testing agent responses locally

This requires `GEMINI_API_KEY` in `nanobot.env`:

```bash
./deploy/nanobot/start_nanobot.sh agent --message "What is the current temperature?"
./deploy/nanobot/start_nanobot.sh agent --message "What is the threshold status?"
```

Or run the full smoke test:

```bash
./deploy/nanobot/test_agent_answers.sh
```

---

## Deployment on Raspberry Pi

### Systemd Services

Three services are provided in `deploy/systemd/`:

| Service | Boot | Description |
|---------|------|-------------|
| `sovereign-sensor-ingest` | Auto | Serial ingestion — reads Arduino, writes logs |
| `sovereign-sensor-api` | Auto | HTTP API server — depends on ingest |
| `sovereign-sensor-nanobot` | Manual | WhatsApp + agent stack — start with `ssa up` |

A watchdog timer (`sovereign-sensor-watchdog.timer`) runs a freshness check every 5 minutes and writes `logs/watchdog-status.json`.

Install everything in one command:

```bash
./scripts/ssa install
```

This creates `/usr/local/bin/ssa`, copies service and timer files to `/etc/systemd/system/`, reloads systemd, and enables ingest, API, and the watchdog timer at boot. The Nanobot service is intentionally left disabled and started manually.

### ssa Command Reference

`scripts/ssa` is the single entry point for all day-to-day operations:

```bash
ssa install              # first-time setup: service files + enable ingest+api+watchdog at boot
ssa up                   # start the Nanobot/WhatsApp stack
ssa down                 # stop the Nanobot/WhatsApp stack
ssa restart              # restart the Nanobot/WhatsApp stack
ssa status               # systemd status for all services

ssa health               # operational summary: states, API freshness, watchdog result
ssa watchdog             # run a one-shot freshness check

ssa deploy               # upgrade: stop → git pull → pip install → reload → restart
ssa rollback             # revert to the commit saved by the last ssa deploy
ssa backup [dir]         # archive logs, threshold config, WhatsApp auth state
ssa restore <file>       # restore from backup tarball
ssa rotate               # archive older observations and thin mid-range history
ssa check                # run pre-flight environment checks

ssa login                # WhatsApp QR-code login
ssa tasks-login          # Google Tasks OAuth login
ssa bridge               # start the WhatsApp bridge manually
ssa gateway              # start the Nanobot gateway only
```

### Inspect service logs

```bash
ssa status
ssa health
journalctl -u sovereign-sensor-ingest.service -f
journalctl -u sovereign-sensor-api.service -f
```

---

## Default Thresholds

Unless overridden by `threshold-config.json`:

| Metric | Warning | Critical |
|--------|---------|----------|
| Temperature | 28.0 °C | 35.0 °C |
| Humidity | 70.0 % | 85.0 % |
| Pressure (low band) | 980.0 hPa | 960.0 hPa |
| Pressure (high band) | 1035.0 hPa | 1060.0 hPa |

---

## Logs and Data Files

All log files are git-ignored. Back them up with `ssa backup`.

| File | Description |
|------|-------------|
| `logs/validated-observations.jsonl` | Append-only validated observation history |
| `logs/latest-observation.json` | Latest validated observation snapshot |
| `logs/rejected-lines.jsonl` | Structured record of rejected serial lines and validation failures |

---

## Troubleshooting

### `bash: .python: command not found`

Use `./.venv/bin/python` or `python3` explicitly.

### No serial device found

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

If nothing appears, check the USB cable, board power, and that the kernel has detected the device.

### Permission denied on serial device

```bash
sudo usermod -aG dialout $USER
# log out and back in
```

### API returns no latest observation

The ingestion script must produce at least one validated observation before the API has data to serve. Start ingestion first, then confirm:

```bash
curl -s http://127.0.0.1:8080/health
```

### No reply from WhatsApp

Work through this checklist in order:

**1. Does the message start with `@ssa`?**
The agent silently ignores every message that does not begin with `@ssa`. This is intentional.

```
@ssa temperature        ← answered
temperature             ← silently ignored
```

**2. Is Nanobot running?**

```bash
ssa up
ssa status              # sovereign-sensor-nanobot should show as active
```

**3. Is the bridge connected?**

```bash
sudo journalctl -u sovereign-sensor-nanobot.service -n 50 --no-pager
```

If logs show `Connect call failed ('127.0.0.1', 3001)`, the bridge is not running:

```bash
ssa bridge
```

**4. Is your sender ID in the allowlist?**

Check `NANOBOT_WHATSAPP_ALLOW_FROM` in `deploy/nanobot/nanobot.env`. The gateway logs print the sender ID on each incoming message.

**5. Is the ingest pipeline producing fresh data?**

```bash
ssa health
curl -s http://127.0.0.1:8080/health
```

If `status` is `stale` or `waiting_for_data`, start ingestion:

```bash
sudo systemctl start sovereign-sensor-ingest.service
```

---

## Roadmap

### Near term

- Webhook authentication and signature verification for provider integrations.
- Structured request logging for the webhook.
- Deployment guide for ngrok, cloudflared, or reverse proxy setup.

### Medium term

- Rolling summaries over the last N observations.
- Log rotation and retention policy.
- Richer threshold configuration with per-device overrides.
- Integration tests covering ingest + API end-to-end.

### Long term

- Multiple sensor devices and named sources.
- Lightweight authenticated dashboard.
- Provider adapters for Twilio or Meta WhatsApp.
- Signed exports preserving the read-only LLM boundary.

---

## Contributing

Bug reports and pull requests are welcome.

Before contributing:

1. Run the full test suite and confirm it passes.
2. Keep pipeline logic (`scripts/`) and LLM-facing code (`workspace/`) strictly separated.
3. Do not add write operations to `workspace/tools/`.
4. Do not modify `schemas/` without updating `ontology_guard.py` and the test suite.

See [CLAUDE.md](CLAUDE.md) for architecture conventions and the guide for extending the system.

---

## License

No license has been added to this repository yet. If you intend to use or distribute this project, add an explicit `LICENSE` file (e.g. MIT or Apache 2.0) before publishing.
