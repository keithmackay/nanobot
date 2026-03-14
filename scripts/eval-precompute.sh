#!/bin/bash
# eval-precompute.sh — Pre-computes context eval data before the 3am cron job.
# Runs at 2:55am via com.keithmackay1.eval-precompute LaunchAgent.
# Output written to /tmp/eval-data-YYYY-MM-DD.md for the eval cron to read.

OUTPUT="/tmp/eval-data-$(date +%Y-%m-%d).md"

/usr/local/bin/python3 \
    /Users/keithmackay1/Projects/nanobot/scripts/gather_eval_data.py \
    --days 2 \
    --project nanobot \
    > "$OUTPUT"

echo "eval-precompute: wrote $OUTPUT ($(wc -l < "$OUTPUT") lines)"
