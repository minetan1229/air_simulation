#!/usr/bin/env bash
# notify_discord.sh - Wrapper around tools/notify_discord.py
set -euo pipefail

STATUS="${1:-info}"
TITLE="${2:-Notification}"
SUMMARY="${3:-}"
EXTRA="${4:-}"

# Resolve python interpreter
if command -v python >/dev/null 2>&1; then
    PY_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
else
    echo "No python interpreter found" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -n "$EXTRA" ]; then
    "$PY_CMD" tools/notify_discord.py "$STATUS" "$TITLE" "$SUMMARY" "$EXTRA"
else
    "$PY_CMD" tools/notify_discord.py "$STATUS" "$TITLE" "$SUMMARY"
fi
