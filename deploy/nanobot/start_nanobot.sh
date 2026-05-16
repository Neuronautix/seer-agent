#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy/nanobot"
WORKSPACE_DIR="$DEPLOY_DIR/workspace"
CONFIG_PATH="$DEPLOY_DIR/runtime-config.json"
ENV_FILE="$DEPLOY_DIR/nanobot.env"
LOG_DIR="$ROOT_DIR/logs"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
NANOBOT_BIN="$ROOT_DIR/.venv/bin/nanobot"
MCP_SERVER="$DEPLOY_DIR/mcp_server.py"
GTASKS_MCP_SERVER="$DEPLOY_DIR/gtasks_mcp_server.py"
GTASKS_OAUTH_SCRIPT="$DEPLOY_DIR/gtasks_oauth.py"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$WORKSPACE_DIR"

BRIDGE_PID=""
ALARM_DAEMON_PID=""
GATEWAY_PID=""

is_port_open() {
  local host="$1"
  local port="$2"
  "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

wait_for_port() {
  local host="$1"
  local port="$2"
  local attempts="${3:-40}"

  for ((i=0; i<attempts; i++)); do
    if is_port_open "$host" "$port"; then
      return 0
    fi
    sleep 0.5
  done

  return 1
}

start_bridge_background() {
  mkdir -p "$LOG_DIR"
  if is_port_open 127.0.0.1 3001; then
    echo "WhatsApp bridge already running on ws://127.0.0.1:3001"
    return 0
  fi

  "$PYTHON_BIN" "$DEPLOY_DIR/whatsapp_bridge.py" "$CONFIG_PATH" >"$LOG_DIR/whatsapp-bridge.log" 2>&1 &
  BRIDGE_PID=$!

  if ! wait_for_port 127.0.0.1 3001 60; then
    echo "WhatsApp bridge failed to start. See $LOG_DIR/whatsapp-bridge.log" >&2
    return 1
  fi
}

start_alarm_daemon_background() {
  mkdir -p "$LOG_DIR"
  "$PYTHON_BIN" "$DEPLOY_DIR/whatsapp_alarm_daemon.py" "$CONFIG_PATH" >"$LOG_DIR/whatsapp-alarm-daemon.log" 2>&1 &
  ALARM_DAEMON_PID=$!
}

stop_stack() {
  local pids=()
  [[ -n "$GATEWAY_PID" ]] && pids+=("$GATEWAY_PID")
  [[ -n "$ALARM_DAEMON_PID" ]] && pids+=("$ALARM_DAEMON_PID")
  [[ -n "$BRIDGE_PID" ]] && pids+=("$BRIDGE_PID")
  if [[ ${#pids[@]} -gt 0 ]]; then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}

run_whatsapp_stack() {
  ensure_key
  render_config
  trap stop_stack EXIT INT TERM
  start_bridge_background
  start_alarm_daemon_background
  "$NANOBOT_BIN" gateway --config "$CONFIG_PATH" --workspace "$WORKSPACE_DIR" "$@" &
  GATEWAY_PID=$!
  wait -n "$GATEWAY_PID" "$ALARM_DAEMON_PID" ${BRIDGE_PID:+"$BRIDGE_PID"}
}

validate_whatsapp_config() {
  if [[ "${NANOBOT_ENABLE_WHATSAPP:-false}" != "true" ]]; then
    return
  fi

  local allow_from_raw="${NANOBOT_WHATSAPP_ALLOW_FROM:-}"
  local trimmed_allow_from="${allow_from_raw//[[:space:]]/}"
  if [[ -z "$trimmed_allow_from" ]]; then
    echo "NANOBOT_WHATSAPP_ALLOW_FROM must list explicit WhatsApp sender IDs when WhatsApp is enabled." >&2
    echo "Use the phone number or LID shown in the gateway logs, separated by commas if you need more than one." >&2
    exit 1
  fi

  if [[ "$trimmed_allow_from" == "*" && "${NANOBOT_WHATSAPP_ALLOW_ALL:-false}" != "true" ]]; then
    echo "Refusing to start with NANOBOT_WHATSAPP_ALLOW_FROM=* because that allows replies to any inbound chat." >&2
    echo "Set NANOBOT_WHATSAPP_ALLOW_FROM to your own allowed sender ID, or set NANOBOT_WHATSAPP_ALLOW_ALL=true if you really want the unsafe behavior." >&2
    exit 1
  fi
}

validate_gtasks_config() {
  if [[ "${NANOBOT_ENABLE_GOOGLE_TASKS:-false}" != "true" ]]; then
    return
  fi

  local token_file="${GOOGLE_TASKS_TOKEN_FILE:-$DEPLOY_DIR/google-tasks-token.json}"
  if [[ ! -f "$token_file" ]]; then
    echo "Google Tasks is enabled but token file is missing: $token_file" >&2
    echo "Run: $0 gtasks-login (with GOOGLE_TASKS_CLIENT_SECRET_FILE set in deploy/nanobot/nanobot.env)" >&2
    exit 1
  fi
}

render_whatsapp_config() {
  local enabled="false"
  local allow_self_messages="false"
  local self_chat_only="false"
  if [[ "${NANOBOT_ENABLE_WHATSAPP:-false}" == "true" ]]; then
    enabled="true"
  fi
  if [[ "${NANOBOT_WHATSAPP_ALLOW_SELF_MESSAGES:-false}" == "true" ]]; then
    allow_self_messages="true"
  fi
  if [[ "${NANOBOT_WHATSAPP_SELF_CHAT_ONLY:-false}" == "true" ]]; then
    self_chat_only="true"
  fi

  local allow_from_raw="${NANOBOT_WHATSAPP_ALLOW_FROM:-}"
  local allow_from_json=""
  local first="true"
  IFS=',' read -r -a allow_from_values <<< "$allow_from_raw"
  for value in "${allow_from_values[@]}"; do
    value="${value#${value%%[![:space:]]*}}"
    value="${value%${value##*[![:space:]]}}"
    [[ -z "$value" ]] && continue
    if [[ "$first" == "true" ]]; then
      allow_from_json="\"$value\""
      first="false"
    else
      allow_from_json="$allow_from_json, \"$value\""
    fi
  done
  cat <<JSON
    ,
    "whatsapp": {
      "enabled": $enabled,
      "bridgeUrl": "${NANOBOT_WHATSAPP_BRIDGE_URL:-ws://localhost:3001}",
      "bridgeToken": "${NANOBOT_WHATSAPP_BRIDGE_TOKEN:-}",
      "allowFrom": [$allow_from_json],
      "allowSelfMessages": $allow_self_messages,
      "selfChatOnly": $self_chat_only,
      "groupPolicy": "${NANOBOT_WHATSAPP_GROUP_POLICY:-mention}"
    }
JSON
}

render_gtasks_server_config() {
  if [[ "${NANOBOT_ENABLE_GOOGLE_TASKS:-false}" != "true" ]]; then
    return
  fi

  cat <<JSON
      ,
      "gtasks": {
        "type": "stdio",
        "command": "$PYTHON_BIN",
        "args": [
          "$GTASKS_MCP_SERVER"
        ],
        "toolTimeout": 30,
        "enabledTools": [
          "list_tasks",
          "create_task",
          "complete_task"
        ]
      }
JSON
}

warn_default_password() {
  local pwd="${SSA_ADMIN_PASSWORD:-}"
  if [[ -z "$pwd" || "$pwd" == "CHANGE_ME" ]]; then
    echo "WARNING: SSA_ADMIN_PASSWORD is not set or uses the placeholder value." >&2
    echo "Set a strong password in deploy/nanobot/nanobot.env before exposing the server." >&2
  fi
}

render_config() {
  warn_default_password
  validate_whatsapp_config
  validate_gtasks_config
  cat > "$CONFIG_PATH" <<JSON
{
  "agents": {
    "defaults": {
      "workspace": "$WORKSPACE_DIR",
      "provider": "gemini",
      "model": "${NANOBOT_MODEL:-gemini-2.5-flash}",
      "maxTokens": 2048,
      "contextWindowTokens": 16384,
      "temperature": 0.1,
      "maxToolIterations": 8,
      "timezone": "${TZ:-UTC}"
    }
  },
  "providers": {
    "gemini": {
      "apiKey": "${GEMINI_API_KEY:-}"
    }
  },
  "gateway": {
    "host": "${NANOBOT_HOST:-127.0.0.1}",
    "port": ${NANOBOT_PORT:-18790},
    "heartbeat": {
      "enabled": false
    }
  },
  "channels": {
    "sendProgress": false,
    "sendToolHints": false
$(render_whatsapp_config)
  },
  "tools": {
    "restrictToWorkspace": true,
    "exec": {
      "enable": false
    },
    "mcpServers": {
      "sensor": {
        "type": "stdio",
        "command": "$PYTHON_BIN",
        "args": [
          "$MCP_SERVER"
        ],
        "toolTimeout": 30,
        "enabledTools": [
          "get_latest_observation",
          "get_metric",
          "get_threshold_status",
          "get_alarm_status",
          "summarize_window"
        ]
      }
$(render_gtasks_server_config)
    }
  }
}
JSON
}

ensure_key() {
  if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "GEMINI_API_KEY is required. Copy deploy/nanobot/nanobot.env.example to deploy/nanobot/nanobot.env and set the key." >&2
    exit 1
  fi
}

COMMAND="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$COMMAND" in
  render-config)
    render_config
    cat "$CONFIG_PATH"
    ;;
  gateway)
    ensure_key
    render_config
    exec "$NANOBOT_BIN" gateway --config "$CONFIG_PATH" --workspace "$WORKSPACE_DIR" "$@"
    ;;
  whatsapp-bridge)
    render_config
    exec "$PYTHON_BIN" "$DEPLOY_DIR/whatsapp_bridge.py" "$CONFIG_PATH" "$@"
    ;;
  whatsapp-login)
    render_config
    exec "$PYTHON_BIN" "$DEPLOY_DIR/whatsapp_login.py" "$CONFIG_PATH" "$@"
    ;;
  gtasks-login|google-tasks-login)
    exec "$PYTHON_BIN" "$GTASKS_OAUTH_SCRIPT" "$@"
    ;;
  up|stack)
    run_whatsapp_stack "$@"
    ;;
  agent)
    ensure_key
    render_config
    exec "$NANOBOT_BIN" agent --config "$CONFIG_PATH" --workspace "$WORKSPACE_DIR" "$@"
    ;;
  status)
    ensure_key
    render_config
    echo "Runtime config: $CONFIG_PATH"
    cat "$CONFIG_PATH"
    ;;
  *)
    echo "Usage: $0 {render-config|gateway|whatsapp-bridge|whatsapp-login|gtasks-login|up|stack|agent|status} [nanobot args...]" >&2
    exit 2
    ;;
esac
