#!/bin/bash
# briefing-generate.sh
# Generates the daily briefing using Claude CLI (non-interactive).
# Called by com.keithmackay1.briefing-generate LaunchAgent at 6am.
# Reads prefetch data from /tmp/briefing-data/ (written by briefing-prefetch at 5:50am).

set -euo pipefail

TODAY=$(date '+%Y-%m-%d')
DATE_FORMATTED=$(date '+%A, %B %-d, %Y')
BRIEFING_DIR="/Users/keithmackay1/KeithVault/Briefings"
BRIEFING_PATH="$BRIEFING_DIR/$TODAY.md"
DATA_DIR="/tmp/briefing-data"
TEMPLATE="$BRIEFING_DIR/DAILY_BRIEFING_TEMPLATE.md"
LOG="/Users/keithmackay1/.nanobot/logs/briefing-generate.log"

echo "[$TODAY $(date '+%H:%M:%S')] Starting briefing generation" | tee -a "$LOG"

# --- Run precheck ---
PRECHECK_OUT=$(/Users/keithmackay1/Projects/nanobot/scripts/briefing-precheck.sh 2>&1)
PRECHECK_STATUS=$?
echo "Precheck: $PRECHECK_OUT" | tee -a "$LOG"

if [ $PRECHECK_STATUS -eq 1 ]; then
    echo "Briefing already complete — skipping." | tee -a "$LOG"
    exit 0
fi

# --- Load prefetch data ---
HN_DATA=$(cat "$DATA_DIR/hn-top.json" 2>/dev/null || echo '[]')
RSS_DATA=$(cat "$DATA_DIR/rss-feeds.json" 2>/dev/null || echo '[]')
ARXIV_DATA=$(cat "$DATA_DIR/arxiv-papers.json" 2>/dev/null || echo '[]')
GITHUB_DATA=$(cat "$DATA_DIR/github-trending.json" 2>/dev/null || echo '[]')
TEMPLATE_CONTENT=$(cat "$TEMPLATE" 2>/dev/null || echo '')

# --- Build the prompt (write to temp file to avoid heredoc delimiter collisions) ---
PROMPT_FILE="/tmp/briefing-prompt-$TODAY.txt"

{
    printf 'You are Mac, Keith'\''s personal AI assistant. Generate his daily briefing for %s.\n\n' "$DATE_FORMATTED"
    printf 'The briefing skeleton has already been created at: %s\n\n' "$BRIEFING_PATH"
    printf 'TASK: Write the complete briefing content and save it to %s using the Bash tool (overwrite the file).\n\n' "$BRIEFING_PATH"
    printf 'USE THIS EXACT FORMAT from the template below (the ## sections, callouts, frontmatter, etc).\n\n'
    printf 'TEMPLATE REFERENCE:\n%s\n\n---\n\n' "$TEMPLATE_CONTENT"
    printf 'PRE-FETCHED DATA (use this as your primary source — do NOT make up URLs):\n\n'
    printf '## Hacker News Top Stories:\n%s\n\n' "$HN_DATA"
    printf '## AI/Tech RSS Feeds (TechCrunch, The Verge, MIT Tech Review):\n%s\n\n' "$RSS_DATA"
    printf '## arXiv Papers (AI/ML/CogSci from last 24-48h):\n%s\n\n' "$ARXIV_DATA"
    printf '## GitHub Trending:\n%s\n\n---\n\n' "$GITHUB_DATA"
    printf 'WRITING RULES:\n'
    printf '1. Every item MUST have a real URL from the data above. If you do not have a URL for something, skip it.\n'
    printf '2. "The Big One" = the most strategically significant story, chosen from the data above. 2-3 paragraphs.\n'
    printf '3. "So What?" annotations are MANDATORY for Big One and all major items.\n'
    printf '4. Voice: Sharp, opinionated, occasionally funny. EY-Parthenon lens for business items.\n'
    printf '5. No item longer than 3 lines. Total scan time: 3-5 minutes.\n'
    printf '6. Sections to include (in order):\n'
    printf '   - YAML frontmatter (date, type, tags, context, subtype, interpreter: Mac, created: [[%s]])\n' "$TODAY"
    printf '   - 30-Second Version callout\n'
    printf '   - Jump to links\n'
    printf '   - The Big One\n'
    printf '   - AI & Tech Moves (4-6 items)\n'
    printf '   - Research Worth Reading / arXiv (3-5 papers from the data)\n'
    printf '   - Tools & Repos Worth Knowing (from GitHub trending)\n'
    printf '   - Software M&A & Strategy (if any from RSS)\n'
    printf '   - Trending in Tech (from HN top stories)\n'
    printf '   - Tasks Keith Could Finish Today (2-3 quick wins, real tasks from his profile: EY projects, NEU teaching, homestead, writing)\n'
    printf '   - Tasks We Could Finish Together Today (2-3 items Mac can help with)\n'
    printf '   - Jody Might Like (1-2 warm non-work items)\n'
    printf '   - Context Optimization Report (placeholder: "No context eval data available for today.")\n'
    printf '\nWrite the briefing now. Use the Write or Bash tool to write the full file to %s.\n' "$BRIEFING_PATH"
} > "$PROMPT_FILE"

PROMPT=$(cat "$PROMPT_FILE")

# --- Invoke Claude ---
echo "Invoking claude CLI..." | tee -a "$LOG"

HOME=/Users/keithmackay1 \
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin" \
/usr/local/bin/claude \
    --dangerously-skip-permissions \
    --model sonnet \
    -p "$PROMPT" \
    >> "$LOG" 2>&1

EXIT_CODE=$?
echo "[$TODAY $(date '+%H:%M:%S')] Claude exited with code $EXIT_CODE" | tee -a "$LOG"

# Verify file was written
if [ -f "$BRIEFING_PATH" ] && grep -q "## The Big One" "$BRIEFING_PATH" 2>/dev/null; then
    echo "SUCCESS: Briefing written to $BRIEFING_PATH" | tee -a "$LOG"

    # --- Send Discord notification via nanobot bot token ---
    DISCORD_TOKEN=$(python3 -c "import json; print(json.load(open('/Users/keithmackay1/.nanobot/config.json'))['channels']['discord']['token'])" 2>/dev/null || echo "")
    PENN_CHANNEL="1477444747046944929"

    if [ -n "$DISCORD_TOKEN" ]; then
        # Extract top story headline (Big One section title)
        BIG_ONE=$(grep -A2 "## The Big One" "$BRIEFING_PATH" 2>/dev/null | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-120)
        MSG="📋 **Daily Briefing — ${DATE_FORMATTED}** is ready.\n**The Big One:** ${BIG_ONE}"

        CURL_RESULT=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            "https://discord.com/api/v10/channels/${PENN_CHANNEL}/messages" \
            -H "Authorization: Bot ${DISCORD_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"${MSG}\"}")

        if [ "$CURL_RESULT" = "200" ] || [ "$CURL_RESULT" = "201" ]; then
            echo "Discord notification sent to Penn channel (HTTP $CURL_RESULT)" | tee -a "$LOG"
        else
            echo "Discord notification failed (HTTP $CURL_RESULT)" | tee -a "$LOG"
        fi
    else
        echo "Discord token not found — skipping notification" | tee -a "$LOG"
    fi

    exit 0
else
    echo "ERROR: Briefing file missing or incomplete after claude run" | tee -a "$LOG"
    exit 1
fi
