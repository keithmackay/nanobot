#!/bin/bash
# brew-upgrade-claude-code.sh — Runs daily via launchd. No LLM needed.

LOG="/Users/keithmackay1/.nanobot/logs/brew-upgrade.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$(dirname "$LOG")"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

echo "[$DATE] Running brew upgrade claude-code" >> "$LOG"
brew upgrade claude-code 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}
echo "[$DATE] Exit: $EXIT_CODE" >> "$LOG"

# Repair symlink if brew left it broken (cask rename failures unlink /usr/local/bin/claude)
if ! command -v claude &>/dev/null || [ ! -e "$(command -v claude 2>/dev/null)" ]; then
    echo "[$DATE] WARNING: claude CLI not found after upgrade, attempting symlink repair..." >> "$LOG"
    LATEST=$(ls -1d /usr/local/Caskroom/claude-code/*/claude 2>/dev/null | sort -V | tail -1)
    if [ -n "$LATEST" ] && [ -x "$LATEST" ]; then
        ln -sf "$LATEST" /usr/local/bin/claude
        echo "[$DATE] Repaired: /usr/local/bin/claude -> $LATEST" >> "$LOG"
    else
        echo "[$DATE] ERROR: Could not find claude binary in Caskroom to repair symlink" >> "$LOG"
    fi
else
    echo "[$DATE] claude CLI OK: $(command -v claude)" >> "$LOG"
fi
