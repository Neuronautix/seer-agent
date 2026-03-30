#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
START_SCRIPT="$ROOT_DIR/deploy/nanobot/start_nanobot.sh"

prompts=(
  "What is the current temperature?"
  "What is the current humidity?"
  "What is the current pressure?"
  "What is the threshold status?"
)

for prompt in "${prompts[@]}"; do
  echo "==> $prompt"
  "$START_SCRIPT" agent --no-markdown --message "$prompt"
  echo
done