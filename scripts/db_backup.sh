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

echo "==> Dumping database on remote server (via docker compose)..."

# pg_dump runs inside the db container; -T disables pseudo-TTY so binary dump streams cleanly
ssh "$SSH_HOST" "cd '$REMOTE_DIR' && docker compose exec -T db pg_dump -Fc -U postgres triathlon-intervals" > "$DUMP_FILE"

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

  LOCAL_DB_URL=$(grep -E '^DATABASE_URL=' "$LOCAL_ENV" | cut -d= -f2- | tr -d "\"'")

  if [[ -z "$LOCAL_DB_URL" ]]; then
    echo "ERROR: DATABASE_URL not found in local .env" >&2
    exit 1
  fi

  # Strip +asyncpg driver suffix so psql understands the URL
  CLEAN_URL="${LOCAL_DB_URL/+asyncpg/}"
  DB_NAME=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$CLEAN_URL').path.lstrip('/'))")

  echo "==> Dropping local database '$DB_NAME'..."
  docker exec -i postgres psql -U postgres postgres -c "DROP DATABASE IF EXISTS \"$DB_NAME\";"

  echo "==> Creating empty database '$DB_NAME'..."
  docker exec -i postgres psql -U postgres postgres -c "CREATE DATABASE \"$DB_NAME\";"

  echo "==> Restoring into '$DB_NAME'..."
  docker exec -i postgres pg_restore --no-owner --no-privileges -U postgres -d "$DB_NAME" < "$DUMP_FILE"
  echo "==> Restore complete."
fi
