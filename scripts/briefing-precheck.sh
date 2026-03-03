#!/bin/bash
# briefing-precheck.sh
# Called before the 6am briefing LLM session.
# Exits 0 if briefing should proceed; exits 1 if it already exists (skip).
# Also scaffolds frontmatter so the LLM doesn't need to.

TODAY=$(date '+%Y-%m-%d')
DATE_FORMATTED=$(date '+%A, %B %-d, %Y')
BRIEFING_DIR="/Users/keithmackay1/KeithVault/Briefings"
BRIEFING_PATH="$BRIEFING_DIR/$TODAY.md"

if [ -f "$BRIEFING_PATH" ]; then
    # Check if it has the full briefing content (not just the context eval stub)
    if grep -q "## The Big One" "$BRIEFING_PATH" 2>/dev/null; then
        echo "SKIP: Briefing already complete at $BRIEFING_PATH"
        exit 1
    fi
    echo "EXISTS_PARTIAL: Context eval stub found, briefing will be inserted above it."
    exit 0
fi

# Scaffold frontmatter + skeleton so LLM can start writing immediately
mkdir -p "$BRIEFING_DIR"
cat > "$BRIEFING_PATH" << FRONTMATTER
---
date: $TODAY
type: daily-briefing
tags: [briefing, daily, ai, tech, strategy]
context: briefings
subtype: daily
interpreter: Mac
created: [[$TODAY]]
---

# Briefing: $DATE_FORMATTED

FRONTMATTER

echo "CREATED_SKELETON: $BRIEFING_PATH"
exit 0
