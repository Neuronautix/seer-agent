# Operations

This file describes how to run the Sovereign Sensor Agent stack manually on a Raspberry Pi and how to verify the Nanobot-facing tool layer without exposing anything publicly.

## Runtime Model

- `scripts/read_serial.py` reads Arduino serial lines and writes validated observations.
- `scripts/api_server.py` serves read-only HTTP endpoints from validated local files.
- `deploy/nanobot/mcp_server.py` wraps the existing read-only local tools as MCP tools.
- Nanobot runs against the isolated workspace in `deploy/nanobot/workspace`, not the repository root.

This matters because the installed Nanobot version always includes built-in filesystem tools for its configured workspace. The isolation boundary is enforced by giving Nanobot a minimal workspace that does not contain serial or ingestion code.

## Manual Startup

### 1. Start ingestion

From the repository root:

```bash
./.venv/bin/python scripts/read_serial.py --device /dev/ttyACM0 --baud 9600 --sensor-id arduino-ttyACM0
```

### 2. Start the read-only API

From the repository root:

```bash
./.venv/bin/python scripts/api_server.py --host 0.0.0.0 --port 8080
```

### 3. Prepare Nanobot config

```bash
cp deploy/nanobot/nanobot.env.example deploy/nanobot/nanobot.env
```

Edit `deploy/nanobot/nanobot.env` and set `GEMINI_API_KEY`.

Use a plain Gemini model name such as `gemini-2.5-flash`.
Do not prefix it with `gemini/` in `NANOBOT_MODEL`.
Set `SSA_ADMIN_PASSWORD=8888` and `SSA_WHATSAPP_ALERT_TO` for the direct chat that should receive temperature alarm messages.

### 4. Start Nanobot locally

Single-shot local questions:

```bash
./deploy/nanobot/start_nanobot.sh agent --message "What is the current temperature?"
```

Gateway mode:

```bash
./deploy/nanobot/start_nanobot.sh gateway
```

Full WhatsApp stack mode:

```bash
./deploy/nanobot/start_nanobot.sh up
```

## Local WhatsApp Link Setup

Nanobot's installed WhatsApp channel uses a local Node.js bridge based on WhatsApp Web.

Current prerequisite:

- Node.js and npm must be installed on the Raspberry Pi.

The current machine does not have them yet, so WhatsApp cannot be linked until that runtime is installed.

### Configure WhatsApp in `deploy/nanobot/nanobot.env`

Set or review these variables:

```bash
NANOBOT_ENABLE_WHATSAPP=true
NANOBOT_WHATSAPP_ALLOW_FROM=REPLACE_WITH_YOUR_WHATSAPP_ID
NANOBOT_WHATSAPP_ALLOW_SELF_MESSAGES=false
NANOBOT_WHATSAPP_SELF_CHAT_ONLY=false
NANOBOT_WHATSAPP_GROUP_POLICY=mention
NANOBOT_WHATSAPP_BRIDGE_URL=ws://localhost:3001
NANOBOT_WHATSAPP_BRIDGE_TOKEN=
SSA_ADMIN_PASSWORD=8888
SSA_WHATSAPP_ALERT_TO=REPLACE_WITH_YOUR_WHATSAPP_ID
```

Notes:

- `NANOBOT_WHATSAPP_ALLOW_FROM` should be a comma-separated allowlist of WhatsApp sender IDs.
- `NANOBOT_WHATSAPP_ALLOW_SELF_MESSAGES=true` enables the linked account's self-chat; the channel suppresses agent reply echoes to avoid loops.
- `NANOBOT_WHATSAPP_SELF_CHAT_ONLY=true` denies every non-self-chat WhatsApp conversation even if another sender ID is known.
- For direct chats, use the phone number or LID printed in the gateway logs for the allowed contact.
- `NANOBOT_WHATSAPP_GROUP_POLICY=mention` keeps group chats quiet unless the linked account is explicitly mentioned.
- The startup script now refuses `NANOBOT_WHATSAPP_ALLOW_FROM=*` unless you explicitly set `NANOBOT_WHATSAPP_ALLOW_ALL=true`.
- Keep WhatsApp disabled until Node.js is installed.
- `SSA_ADMIN_PASSWORD` controls the deterministic threshold-update command path and defaults to `8888`.
- `SSA_WHATSAPP_ALERT_TO` is the chat that receives outbound temperature alarm messages.

### Install the bridge prerequisite

Install Node.js 18+ and npm on the Raspberry Pi before trying the login step.

### Start the WhatsApp login flow

Once Node.js and npm are installed:

```bash
./deploy/nanobot/start_nanobot.sh whatsapp-login
```

That command uses the rendered Nanobot config and starts the bridge login flow so you can scan the QR code for WhatsApp Web.

If you keep that process running after login, it is already the active bridge process. In that case, do not start `whatsapp-bridge` as well.

### Start the WhatsApp bridge

After the account is linked, start this only if the login process is no longer running and nothing is already listening on port `3001`:

```bash
./deploy/nanobot/start_nanobot.sh whatsapp-bridge
```

The Nanobot gateway connects to this local WebSocket bridge at `ws://127.0.0.1:3001`.

### Start the full WhatsApp stack with one command

After the account is linked:

```bash
./scripts/ssa install
ssa up
```

That starts the bridge, the deterministic WhatsApp admin-and-alert daemon, and the Nanobot gateway through `sovereign-sensor-nanobot.service`.

### WhatsApp admin commands

Send one of these messages from an allowed chat:

- `8888 thresholds`
- `8888 set temp 30`
- `8888 set temp critical 35`

These commands write `threshold-config.json`, which is then read by the API and local tools.

### Automatic temperature alarm messages

When temperature enters a warning or critical state, the WhatsApp daemon sends an outbound message to `SSA_WHATSAPP_ALERT_TO`.

### Start the Nanobot gateway with WhatsApp enabled

With the bridge already running in another terminal:

```bash
./deploy/nanobot/start_nanobot.sh gateway
```

At that point the local Nanobot gateway can receive WhatsApp messages through the local bridge while still answering sensor questions only through the validated-file tool layer.

If `gateway` logs `Connect call failed ('127.0.0.1', 3001)`, the bridge is not running yet.

## Verification

### Verify ingestion is working

Check that validated files are being updated:

```bash
ls -l logs/
tail -n 3 logs/validated-observations.jsonl
sed -n '1p' logs/latest-observation.json
tail -n 5 logs/rejected-lines.jsonl
```

Healthy signs:

- `logs/latest-observation.json` exists.
- `logs/validated-observations.jsonl` contains recent validated records.
- `logs/rejected-lines.jsonl` only contains malformed or invalid sensor lines.

### Verify the API is working

```bash
curl -s http://127.0.0.1:8080/
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/latest/temp
curl -s http://127.0.0.1:8080/latest/humidity
curl -s http://127.0.0.1:8080/latest/pressure
curl -s http://127.0.0.1:8080/latest/threshold-status
```

### Verify the Nanobot-facing tool layer

This test does not require a Gemini key. It verifies the MCP shim exposes only the intended read-only tools and that they read from validated local files.

```bash
./.venv/bin/python deploy/nanobot/test_tool_layer.py
```

Expected result:

- Tool names include `get_latest_observation`, `get_metric`, and `get_threshold_status`.
- Temperature, humidity, pressure, and threshold status return `ok: true` payloads.
- Data comes from the existing read-only wrapper scripts under `workspace/tools/`.

### Verify Nanobot can answer local sensor questions

This step requires `GEMINI_API_KEY` in `deploy/nanobot/nanobot.env`.

Run the four smoke-test prompts:

```bash
./deploy/nanobot/test_agent_answers.sh
```

Or run them individually:

```bash
./deploy/nanobot/start_nanobot.sh agent --message "What is the current temperature?"
./deploy/nanobot/start_nanobot.sh agent --message "What is the current humidity?"
./deploy/nanobot/start_nanobot.sh agent --message "What is the current pressure?"
./deploy/nanobot/start_nanobot.sh agent --message "What is the threshold status?"
```

## Trust Boundary Check

The Nanobot deployment keeps the current architecture intact:

- Nanobot does not read `/dev/tty*` devices.
- Nanobot does not call `scripts/read_serial.py` or any raw-ingestion code.
- Nanobot does not read the repository root as its workspace.
- Nanobot gets live sensor values only through the three read-only tool wrappers already present in `workspace/tools/`.
- Shell execution is disabled in Nanobot config.

Remaining limitation:

- This Nanobot version still includes built-in filesystem tools for its configured workspace. The protection is workspace isolation, not tool removal.

## systemd

Service files are provided in `deploy/systemd/`:

- `sovereign-sensor-ingest.service`
- `sovereign-sensor-api.service`
- `sovereign-sensor-nanobot.service`

Install them with:

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable NetworkManager-wait-online.service || true
sudo systemctl enable systemd-networkd-wait-online.service || true
sudo systemctl enable sovereign-sensor-ingest.service
sudo systemctl enable sovereign-sensor-api.service
sudo systemctl start sovereign-sensor-ingest.service
sudo systemctl start sovereign-sensor-api.service
```

Recommended default boot profile:

- Enable only `sovereign-sensor-ingest.service` and `sovereign-sensor-api.service` at boot.
- Start `sovereign-sensor-nanobot.service` manually only when you want the WhatsApp or Nanobot layer online.

Start Nanobot manually when needed:

```bash
sudo systemctl start sovereign-sensor-nanobot.service
sudo systemctl status sovereign-sensor-nanobot.service
```

Stop it again when you no longer need it:

```bash
sudo systemctl stop sovereign-sensor-nanobot.service
```

Check status with:

```bash
sudo systemctl status sovereign-sensor-ingest.service
sudo systemctl status sovereign-sensor-api.service
sudo systemctl status sovereign-sensor-nanobot.service
journalctl -u sovereign-sensor-nanobot.service -f
```

These units now wait on `network-online.target`, so after a reboot the stack starts automatically once the Pi has reached online network state.

This deployment is not a Pod today. It is a set of host-level `systemd` services on the Raspberry Pi. Pod-style packaging is tracked separately in `SOLID_POD_IMPLEMENTATION.md`.

### Freshness watchdog timer

A systemd timer runs `watchdog.py` every 5 minutes after boot and writes `logs/watchdog-status.json`.

Enable it alongside the other units:

```bash
sudo systemctl enable sovereign-sensor-watchdog.timer
sudo systemctl start sovereign-sensor-watchdog.timer
```

Run a one-shot freshness check at any time:

```bash
ssa watchdog
```

View the last result:

```bash
cat logs/watchdog-status.json
```

## Deploy, Rollback, Backup, and Restore

### First-time installation

Clone the repository and run install once:

```bash
git clone <repo-url> sovereign-sensor-agent
cd sovereign-sensor-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp deploy/nanobot/nanobot.env.example deploy/nanobot/nanobot.env
# edit nanobot.env: set GEMINI_API_KEY and SSA_ADMIN_PASSWORD
scripts/ssa install          # copies service files, enables ingest+api+watchdog at boot
sudo systemctl start sovereign-sensor-ingest.service sovereign-sensor-api.service
```

`ssa install` does the following in one step:
- Creates `/usr/local/bin/ssa` symlink
- Copies all `deploy/systemd/*.service` and `*.timer` to `/etc/systemd/system/`
- Runs `systemctl daemon-reload`
- Enables `sovereign-sensor-ingest`, `sovereign-sensor-api`, and `sovereign-sensor-watchdog.timer` at boot
- Does **not** enable `sovereign-sensor-nanobot` (that stays manual)

### Upgrading to a new version

```bash
ssa deploy
```

This performs a zero-downtime-minimising upgrade:
1. Saves the current git commit to `.last-deploy-commit`
2. Stops ingest and API
3. `git pull` on the current tracking branch
4. `pip install -r requirements.txt` (reinstalls only changed packages)
5. Copies updated service files to `/etc/systemd/system/` and reloads systemd
6. Restarts ingest and API

Verify after deploying:

```bash
ssa health
```

### Rolling back to the previous version

If a deploy introduces a regression, roll back with:

```bash
ssa rollback
```

This resets the working tree to the commit saved by the last `ssa deploy`, reinstalls dependencies from that commit's `requirements.txt`, reloads systemd units, and restarts services.

Roll back is only available once per deploy (the saved commit is removed after use). For older revisions, roll back manually:

```bash
git log --oneline -10           # find the target commit hash
sudo systemctl stop sovereign-sensor-ingest.service sovereign-sensor-api.service
git reset --hard <commit-hash>
.venv/bin/pip install -r requirements.txt
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start sovereign-sensor-ingest.service sovereign-sensor-api.service
```

### Backing up runtime data

Backs up `logs/`, `threshold-config.json`, and WhatsApp auth state (if present) into a timestamped tarball:

```bash
ssa backup                  # saves to backups/ssa-backup-<timestamp>.tar.gz
ssa backup /mnt/usb/backups # save to a custom path
```

The backup **does not** include `.venv/` or source code (both are reproducible).

Keep at least one recent backup before any `ssa deploy` or hardware change.

### Restoring from a backup

```bash
ssa restore backups/ssa-backup-20260401T120000Z.tar.gz
```

This stops services, extracts the tarball into the repository root (restoring `logs/` and threshold config in place), then restarts ingest and API.

WhatsApp auth state is restored automatically if it was included in the backup. No re-login is needed.

### What each backup contains

| Path | Description |
|------|-------------|
| `logs/validated-observations.jsonl` | Append-only sensor audit trail |
| `logs/latest-observation.json` | Latest snapshot |
| `logs/rejected-lines.jsonl` | Validation failure log |
| `logs/watchdog-status.json` | Last watchdog result |
| `threshold-config.json` | User-configured thresholds (if set) |
| `deploy/nanobot/auth_info_multidevice/` | WhatsApp session auth (if present) |

`nanobot.env` is **not** included because it contains secrets. Back it up separately if needed.

## What Is Still Missing

- A completed WhatsApp bridge login session.
- Node.js and npm for Nanobot's built-in WhatsApp bridge.
- Any public or provider-facing webhook exposure.

Those are intentionally out of scope for this local testable deployment layer.