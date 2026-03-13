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

# Repair if brew left claude broken (cask rename failures unlink /usr/local/bin/claude)
if ! command -v claude &>/dev/null || [ ! -e "$(command -v claude 2>/dev/null)" ]; then
    echo "[$DATE] WARNING: claude CLI not found after upgrade, attempting symlink repair..." >> "$LOG"

    # Step 1: try to relink from existing Caskroom binary (fast, no download)
    LATEST=$(ls -1d /usr/local/Caskroom/claude-code/*/claude 2>/dev/null | sort -V | tail -1)
    if [ -n "$LATEST" ] && [ -x "$LATEST" ]; then
        ln -sf "$LATEST" /usr/local/bin/claude
        echo "[$DATE] Repaired symlink: /usr/local/bin/claude -> $LATEST" >> "$LOG"

    # Step 2: Caskroom is empty (brew purged it during failed upgrade) — reinstall from scratch
    else
        echo "[$DATE] Caskroom empty, running brew reinstall --cask claude-code..." >> "$LOG"
        brew reinstall --cask claude-code 2>&1 | tee -a "$LOG"
        REINSTALL_EXIT=${PIPESTATUS[0]}
        echo "[$DATE] Reinstall exit: $REINSTALL_EXIT" >> "$LOG"

        if command -v claude &>/dev/null; then
            echo "[$DATE] Reinstall successful: $(command -v claude)" >> "$LOG"
        else
            echo "[$DATE] ERROR: reinstall completed but claude still not found" >> "$LOG"
        fi
    fi
else
    echo "[$DATE] claude CLI OK: $(command -v claude)" >> "$LOG"
fi
