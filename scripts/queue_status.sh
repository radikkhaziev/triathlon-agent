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

echo "=== Dramatiq Keys ==="
ssh "$SSH_HOST" "$R KEYS 'dramatiq:*'" 2>/dev/null | sed 's/^/  /' || echo "  (none)"

echo ""
echo "=== Dramatiq Queues ==="
for key in $(ssh "$SSH_HOST" "$R KEYS 'dramatiq:*'" 2>/dev/null); do
  type=$(ssh "$SSH_HOST" "$R TYPE $key" 2>/dev/null | tr -d '[:space:]')
  case "$type" in
    list) size=$(ssh "$SSH_HOST" "$R LLEN $key" 2>/dev/null); echo "  $key (list): $size" ;;
    zset) size=$(ssh "$SSH_HOST" "$R ZCARD $key" 2>/dev/null); echo "  $key (zset): $size" ;;
    set)  size=$(ssh "$SSH_HOST" "$R SCARD $key" 2>/dev/null); echo "  $key (set):  $size" ;;
    *)    echo "  $key ($type)" ;;
  esac
done

echo ""
echo "=== Redis Info ==="
echo "  keys:   $(ssh "$SSH_HOST" "$R DBSIZE" 2>/dev/null | grep -o '[0-9]*')"
echo "  memory: $(ssh "$SSH_HOST" "$R INFO memory" 2>/dev/null | grep used_memory_human | cut -d: -f2 | tr -d '[:space:]')"
