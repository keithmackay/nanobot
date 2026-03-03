#!/bin/bash
# github-trending.sh — Fetch trending repos without an LLM.
# Usage: github-trending.sh [language]
# Output: JSON to stdout and /tmp/github-trending.json

LANGUAGE="${1:-}"
# macOS/Linux compatible date
DATE=$(python3 -c "from datetime import date, timedelta; print(date.today() - timedelta(days=7))")
OUT="/tmp/github-trending.json"

if [ -n "$LANGUAGE" ]; then
    QUERY="language:${LANGUAGE}+created:>${DATE}"
else
    QUERY="created:>${DATE}+stars:>50"
fi

URL="https://api.github.com/search/repositories?q=${QUERY}&sort=stars&order=desc&per_page=10"

curl -s -H "Accept: application/vnd.github+json" "$URL" | \
  python3 -c "
import json,sys
data = json.load(sys.stdin)
items = data.get('items', [])
result = []
for r in items:
    result.append({
        'name': r['full_name'],
        'description': r.get('description',''),
        'stars': r['stargazers_count'],
        'language': r.get('language',''),
        'url': r['html_url'],
        'topics': r.get('topics',[])[:5]
    })
print(json.dumps(result, indent=2))
" | tee "$OUT"
