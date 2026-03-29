#!/usr/bin/env bash
# db_backup.sh — dump remote PostgreSQL via SSH, restore locally if needed
#
# Usage:
#   ./scripts/db_backup.sh              # dump to ./backups/
#   ./scripts/db_backup.sh --restore    # dump + restore into local DB (DATABASE_URL from local .env)
#
# Requirements: ssh access to "bot" host (configure in ~/.ssh/config)

set -euo pipefail

SSH_HOST="bot"
REMOTE_DIR="/opt/triatlon-bot/src"
BACKUP_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/triathlon_${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"

echo "==> Reading DATABASE_URL from $SSH_HOST:$REMOTE_DIR/.env"

# Extract DATABASE_URL from remote .env
REMOTE_DB_URL=$(ssh "$SSH_HOST" "grep -E '^DATABASE_URL=' '$REMOTE_DIR/.env' | cut -d= -f2-")

if [[ -z "$REMOTE_DB_URL" ]]; then
  echo "ERROR: DATABASE_URL not found in remote .env" >&2
  exit 1
fi

echo "==> Dumping database on remote server..."

# Run pg_dump remotely, stream dump here
# Uses custom format (-Fc) — compatible with pg_restore
ssh "$SSH_HOST" "pg_dump -Fc '$REMOTE_DB_URL'" > "$DUMP_FILE"

SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo "==> Done: $DUMP_FILE ($SIZE)"

# --restore flag: load dump into local DB
if [[ "${1:-}" == "--restore" ]]; then
  echo "==> Loading local .env for DATABASE_URL..."

  LOCAL_ENV="$(dirname "$0")/../.env"
  if [[ ! -f "$LOCAL_ENV" ]]; then
    echo "ERROR: local .env not found at $LOCAL_ENV" >&2
    exit 1
  fi

  LOCAL_DB_URL=$(grep -E '^DATABASE_URL=' "$LOCAL_ENV" | cut -d= -f2-)

  if [[ -z "$LOCAL_DB_URL" ]]; then
    echo "ERROR: DATABASE_URL not found in local .env" >&2
    exit 1
  fi

  echo "==> Restoring into local database..."
  # --clean drops existing objects before restore
  pg_restore --clean --no-owner --no-privileges -d "$LOCAL_DB_URL" "$DUMP_FILE"
  echo "==> Restore complete."
fi
