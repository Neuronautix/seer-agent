#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy/nanobot"
WORKSPACE_DIR="$DEPLOY_DIR/workspace"
CONFIG_PATH="$DEPLOY_DIR/runtime-config.json"
ENV_FILE="$DEPLOY_DIR/nanobot.env"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
NANOBOT_BIN="$ROOT_DIR/.venv/bin/nanobot"
MCP_SERVER="$DEPLOY_DIR/mcp_server.py"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$WORKSPACE_DIR"

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

render_config() {
  validate_whatsapp_config
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
        "toolTimeout": 10,
        "enabledTools": [
          "get_latest_observation",
          "get_metric",
          "get_threshold_status"
        ]
      }
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
    echo "Usage: $0 {render-config|gateway|whatsapp-bridge|whatsapp-login|agent|status} [nanobot args...]" >&2
    exit 2
    ;;
esac