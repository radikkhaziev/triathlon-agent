#!/usr/bin/env bash
# queue_status.sh — show dramatiq queue and Redis status on remote server
#
# Usage:
#   ./scripts/queue_status.sh
#
# Requirements: ssh access to "bot" host (configure in ~/.ssh/config)

set -euo pipefail

SSH_HOST="bot"
REMOTE_DIR="/opt/triatlon-bot/src"
R="docker compose -f ${REMOTE_DIR}/docker-compose.yml exec -T redis redis-cli"

ssh "$SSH_HOST" bash -s -- "$R" <<'REMOTE'
R=$1

echo "=== Dramatiq Keys ==="
keys=$($R KEYS 'dramatiq:*' 2>/dev/null)
if [ -z "$keys" ]; then
  echo "  (none)"
else
  echo "$keys" | sed 's/^/  /'

  echo ""
  echo "=== Dramatiq Queues ==="
  for key in $keys; do
    type=$($R TYPE "$key" 2>/dev/null | tr -d '[:space:]')
    case "$type" in
      list) size=$($R LLEN "$key" 2>/dev/null); echo "  $key (list): $size" ;;
      zset) size=$($R ZCARD "$key" 2>/dev/null); echo "  $key (zset): $size" ;;
      set)  size=$($R SCARD "$key" 2>/dev/null); echo "  $key (set):  $size" ;;
      *)    echo "  $key ($type)" ;;
    esac
  done
fi

echo ""
echo "=== Redis Info ==="
echo "  keys:   $($R DBSIZE 2>/dev/null | grep -o '[0-9]*')"
echo "  memory: $($R INFO memory 2>/dev/null | grep used_memory_human | cut -d: -f2 | tr -d '[:space:]')"
REMOTE
