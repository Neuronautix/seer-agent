# CLAUDE.md — Sovereign Sensor Agent

AI-assistant guide for working in this repository. Read this before making any changes.

---

## Project Summary

Sovereign Sensor Agent is a **local-first, privacy-preserving** system that:
1. Reads sensor data from an Arduino over serial.
2. Validates it through a deterministic Python pipeline.
3. Persists it to append-only local logs.
4. Exposes a **read-only** HTTP API and constrained LLM tool layer.
5. Optionally bridges to WhatsApp via a local node-based bridge.

The core design principle: **the Python pipeline is the only source of truth**. LLMs are observers, not writers.

---

## Repository Layout

```
sovereign-sensor-agent/
├── scripts/                    # Deterministic pipeline (source of truth)
│   ├── read_serial.py          # Serial ingestion & parsing
│   ├── build_observation.py    # Canonical JSON-LD builder
│   ├── ontology_guard.py       # Schema + range validation
│   ├── api_server.py           # Read-only HTTP API (port 8080)
│   ├── supervisor.py           # CLI command handler
│   ├── alarm_runtime.py        # Threshold evaluation & alarm logic
│   ├── observation_analysis.py # Windowed aggregation utilities
│   ├── ssa                     # Bash wrapper for systemd service control
│   ├── test_pipeline.py        # Main test suite (40+ unit tests)
│   ├── test_supervisor.py      # Supervisor tests
│   └── test_alarm_runtime.py   # Alarm runtime tests
│
├── workspace/                  # LLM-facing boundary (read-only tools only)
│   ├── POLICY.md               # Enforced LLM behavioral rules
│   ├── WHATSAPP_SYSTEM_PROMPT.md
│   └── tools/                  # Read-only Python tool wrappers for MCP
│       ├── get_latest_observation.py
│       ├── get_metric.py
│       ├── get_threshold_status.py
│       ├── get_alarm_status.py
│       └── summarize_window.py
│
├── deploy/
│   ├── systemd/                # systemd service unit files
│   └── nanobot/                # Nanobot/WhatsApp integration
│       ├── mcp_server.py       # MCP shim exposing workspace tools
│       ├── start_nanobot.sh    # Multi-mode startup (agent/gateway/whatsapp/up)
│       ├── config.template.json
│       ├── nanobot.env.example # Environment variable template
│       ├── whatsapp_alarm_daemon.py
│       ├── whatsapp_bridge.py
│       ├── whatsapp_login.py
│       ├── test_tool_layer.py  # Async MCP tool verification
│       └── test_agent_answers.sh
│
├── schemas/
│   ├── sensor-observation-v1.json  # Canonical observation contract (JSON Schema Draft 2020-12)
│   └── agent-action-v1.json        # LLM intent schema
│
├── logs/                       # Runtime data — git-ignored
│   ├── validated-observations.jsonl   # Append-only audit trail
│   ├── latest-observation.json        # Fast-path snapshot
│   └── rejected-lines.jsonl           # Validation failure log
│
├── requirements.txt            # Python deps: jsonschema, mcp, nanobot-ai
├── README.md                   # User-facing documentation
└── OPERATIONS.md               # Operational runbook
```

---

## Architecture: Data Flow

```
Arduino serial
    │
    ▼
read_serial.py          ← parses canonical (TEMP=...;HUM=...;TS=...) or human-readable lines
    │
    ▼
build_observation.py    ← builds canonical JSON-LD with UTC timestamp
    │
    ▼
ontology_guard.py       ← JSON Schema validation + runtime range checks
    │
    ├──► logs/validated-observations.jsonl   (append-only)
    └──► logs/latest-observation.json        (latest snapshot)
              │
              ▼
    api_server.py / workspace/tools / supervisor.py
              │
              ▼
    (optional) Nanobot MCP layer → LLM → WhatsApp
```

The LLM layer reads **only from validated log files**. It never touches the serial port or writes anything.

---

## Canonical Observation Schema

All validated observations follow this JSON-LD shape (defined in `schemas/sensor-observation-v1.json`):

```json
{
  "@context": { ... },
  "@type": "SensorObservation",
  "schemaVersion": "sensor-observation-v1",
  "sensorId": "arduino-ttyACM0",
  "sourcePort": "/dev/ttyACM0",
  "observedAt": "2026-03-29T11:12:13Z",
  "temperatureC": 23.4,
  "humidityPct": 51.2,
  "pressureHpa": 1008.7
}
```

**Required fields:** `@context`, `@type`, `schemaVersion`, `sensorId`, `sourcePort`, `observedAt`, `temperatureC`, `humidityPct`

**Optional:** `pressureHpa`

**Runtime range constraints enforced by `ontology_guard.py`:**
- `temperatureC`: −40 to 85 °C
- `humidityPct`: 0 to 100 %
- `pressureHpa`: 300 to 1100 hPa
- `observedAt`: must be UTC ISO-8601 ending in `Z`

Any observation that fails schema or range checks is **rejected** and written to `logs/rejected-lines.jsonl` instead.

---

## Python Stack

- **Language:** Python 3.11+
- **Virtual environment:** `.venv/` at repo root (not committed)
- **Dependencies (`requirements.txt`):**
  - `jsonschema>=4.0,<5.0` — JSON Schema Draft 2020-12 validation
  - `mcp>=1.26,<2.0` — Model Context Protocol server/client
  - `nanobot-ai>=0.1.4.post6,<0.2` — Nanobot agent framework
- **No pytest:** tests use `unittest` from the standard library
- **Scripts use relative imports** within `scripts/` — run them from that directory or via the `.venv` Python

---

## Running Tests

There is no CI/CD. Run tests manually:

```bash
# From repo root
.venv/bin/python scripts/test_pipeline.py        # 40+ unit tests
.venv/bin/python scripts/test_supervisor.py      # supervisor tests
.venv/bin/python scripts/test_alarm_runtime.py   # alarm tests

# MCP tool layer verification (requires running API server)
.venv/bin/python deploy/nanobot/test_tool_layer.py

# Agent smoke tests (requires running agent)
bash deploy/nanobot/test_agent_answers.sh
```

Always run the relevant test file after modifying pipeline logic.

---

## Development Workflows

### Installing dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Running the pipeline manually

```bash
# Ingest from real serial device
.venv/bin/python scripts/read_serial.py --device /dev/ttyACM0 --baud 9600

# Ingest from stdin (for testing)
echo "TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z" | .venv/bin/python scripts/read_serial.py --stdin

# Start read-only API server (port 8080)
.venv/bin/python scripts/api_server.py

# Query via supervisor CLI
.venv/bin/python scripts/supervisor.py read_latest
.venv/bin/python scripts/supervisor.py get_threshold_status
```

### Running the Nanobot agent stack

```bash
# Copy and configure environment
cp deploy/nanobot/nanobot.env.example deploy/nanobot/nanobot.env
# Edit nanobot.env: set GEMINI_API_KEY and NANOBOT_WHATSAPP_ALLOW_FROM

# Start agent only (no WhatsApp)
bash deploy/nanobot/start_nanobot.sh agent

# Full stack: API + agent + WhatsApp bridge + alarm daemon
bash deploy/nanobot/start_nanobot.sh up
```

### Using the `ssa` CLI wrapper

```bash
scripts/ssa install    # copy systemd units, enable ingest+api+watchdog at boot
scripts/ssa up         # start nanobot stack
scripts/ssa down       # stop nanobot stack
scripts/ssa restart    # restart nanobot stack
scripts/ssa status     # full systemd status for all three services
scripts/ssa health     # operational summary: service states, API freshness, watchdog
scripts/ssa watchdog   # run freshness check now, write logs/watchdog-status.json
scripts/ssa deploy     # upgrade: stop → git pull → pip install → systemd reload → restart
scripts/ssa rollback   # revert to pre-deploy commit (requires prior ssa deploy)
scripts/ssa backup     # archive logs + threshold config + WhatsApp auth state
scripts/ssa restore <file>  # restore from backup tarball
scripts/ssa login      # WhatsApp QR code login
```

---

## Key Conventions

### Serial line formats

Two accepted formats from Arduino:

**Canonical (preferred):**
```
TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z
TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z
```

**Human-readable (also accepted):**
```
Temperature = 23.4 °C
Humidity = 51.2 %
Pressure = 1008.7 hPa
```

Parser strictly rejects: duplicate fields, unknown fields, malformed values.

### LLM Policy (enforced via `workspace/POLICY.md`)

The LLM (Nanobot/agent) **must only emit these intents:**
- `read_latest`
- `summarize_window`
- `get_threshold_status`
- `request_export`
- `propose_annotation`

The LLM **must never:**
- Generate canonical observation JSON-LD
- Write to logs, schemas, or storage
- Read `/dev/tty*` directly
- Bypass schema validation
- Execute shell commands (disabled in Nanobot config)

Any non-read-only action must be returned as a **proposal object** only, subject to deterministic supervisor review.

### Threshold & alarm configuration

Thresholds are persisted in `threshold-config.json` (runtime-generated, not committed). Admin commands require the `SSA_ADMIN_PASSWORD` token (default is the literal placeholder `CHANGE_ME`; must be set to a real value before enabling the WhatsApp bridge).

### WhatsApp message filtering

- Only messages prefixed with `@ssa` are processed by the agent
- Allowed senders are allowlisted via `NANOBOT_WHATSAPP_ALLOW_FROM` in `nanobot.env`
- The alarm daemon independently monitors thresholds and sends unprompted alerts

---

## Environment Variables

Configured via `deploy/nanobot/nanobot.env` (copy from `nanobot.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key for Nanobot | *(required)* |
| `NANOBOT_MODEL` | Model identifier | `gemini-2.5-flash` |
| `NANOBOT_HOST` | Agent listen address | `127.0.0.1` |
| `NANOBOT_PORT` | Agent listen port | `18790` |
| `NANOBOT_ENABLE_WHATSAPP` | Enable WhatsApp bridge | `false` |
| `NANOBOT_WHATSAPP_ALLOW_FROM` | Allowed WhatsApp sender ID | *(required if WhatsApp enabled)* |
| `NANOBOT_WHATSAPP_BRIDGE_URL` | WhatsApp bridge WebSocket URL | `ws://localhost:3001` |
| `NANOBOT_WHATSAPP_BRIDGE_TOKEN` | Bridge auth token | *(required if WhatsApp enabled)* |
| `SSA_ADMIN_PASSWORD` | Password for admin threshold commands | `CHANGE_ME` |
| `SSA_WHATSAPP_ALERT_TO` | WhatsApp ID for alarm notifications | *(required for alarms)* |

Never commit `nanobot.env`; it is git-ignored.

---

## Systemd Services

Three services managed by `scripts/ssa`:

| Service | File | Description |
|---------|------|-------------|
| `sovereign-sensor-ingest` | `deploy/systemd/sovereign-sensor-ingest.service` | Serial ingestion (reads Arduino, writes logs) |
| `sovereign-sensor-api` | `deploy/systemd/sovereign-sensor-api.service` | HTTP API server (depends on ingest) |
| `sovereign-sensor-nanobot` | `deploy/systemd/sovereign-sensor-nanobot.service` | WhatsApp + agent stack (depends on API) |

---

## What Not To Do

- **Do not** generate or write canonical observation JSON from LLM code paths
- **Do not** add write operations to anything in `workspace/tools/`
- **Do not** modify `schemas/` without updating the validation logic in `ontology_guard.py` and the test suite
- **Do not** add cloud egress or external API calls to the deterministic pipeline (`scripts/`)
- **Do not** commit `nanobot.env`, `.venv/`, or any `logs/*.json` files (all git-ignored)
- **Do not** skip running tests after modifying pipeline logic — there is no CI to catch regressions

---

## Extending the System

### Adding a new metric (e.g., CO2 ppm)

1. Update `schemas/sensor-observation-v1.json` — add field with range constraints
2. Update `build_observation.py` — map raw key to canonical field name
3. Update `ontology_guard.py` — add runtime range check
4. Update `scripts/read_serial.py` — add parsing pattern
5. Update workspace tools if the metric should be queryable by the LLM
6. Add tests in `test_pipeline.py` covering the new field

### Adding a new API endpoint

1. Add handler function in `scripts/api_server.py`
2. Register in the routing table inside `make_handler()`
3. Add tests in `test_pipeline.py`

### Adding a new MCP tool

1. Create a read-only Python script in `workspace/tools/`
2. Register the tool in `deploy/nanobot/mcp_server.py`
3. Verify with `deploy/nanobot/test_tool_layer.py`

---

## Git Workflow

- Main branch: `main`
- Feature work goes on dedicated branches
- No automated CI — run tests manually before committing
- Commit messages should be descriptive and reference the component changed (e.g., `Fix pressure parsing in read_serial.py`)
