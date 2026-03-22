#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
SSH_HOST="bot"
REMOTE_TOKEN_DIR="/opt/triatlon-bot/.garminconnect"
LOCAL_TOKEN_DIR="$HOME/.garminconnect"
COMPOSE_DIR="/opt/triatlon-bot"

# --- Copy tokens ---
echo "→ Copying Garmin tokens to $SSH_HOST:$REMOTE_TOKEN_DIR"
scp "$LOCAL_TOKEN_DIR"/* "$SSH_HOST:$REMOTE_TOKEN_DIR/"

# --- Restart bot ---
echo "→ Restarting bot service"
ssh "$SSH_HOST" "cd $COMPOSE_DIR && docker compose restart bot"

echo "✓ Done"
