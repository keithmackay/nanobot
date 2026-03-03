#!/bin/bash
# brew-upgrade-claude-code.sh — Runs daily via launchd. No LLM needed.

LOG="/Users/keithmackay1/.nanobot/logs/brew-upgrade.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$(dirname "$LOG")"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

echo "[$DATE] Running brew upgrade claude-code" >> "$LOG"
brew upgrade claude-code 2>&1 | tee -a "$LOG"
echo "[$DATE] Exit: $?" >> "$LOG"
