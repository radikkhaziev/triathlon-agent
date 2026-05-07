#!/bin/bash
# PostToolUse hook: auto-format and lint Python files after Edit/Write.
# Degrades gracefully on hosts without poetry/jq.

command -v jq >/dev/null 2>&1 || exit 0
command -v poetry >/dev/null 2>&1 || exit 0

FILE=$(jq -r '.tool_input.file_path' < /dev/stdin)
echo "$FILE" | grep -q '\.py$' || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}" || exit 0
poetry run black --quiet "$FILE"
poetry run isort --quiet "$FILE"
poetry run flake8 "$FILE" 2>&1
