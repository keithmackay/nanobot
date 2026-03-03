#!/bin/bash
# briefing-fetch-data.sh
# Pre-fetches GitHub trending and RSS feeds BEFORE the 6am LLM session.
# Run this ~5:50am via a separate launchd timer or n8n trigger.
# Writes to /tmp/briefing-data/ for the LLM to read directly.

set -euo pipefail

OUT="/tmp/briefing-data"
mkdir -p "$OUT"

# 1. GitHub trending (no auth, public API)
echo "Fetching GitHub trending..."
bash "$(dirname "$0")/github-trending.sh" > "$OUT/github-trending.json" 2>/dev/null || echo '[]' > "$OUT/github-trending.json"

# 2. HN Top Stories (Algolia API — no auth)
echo "Fetching HN top stories..."
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | \
  python3 -c "
import json,sys,urllib.request
ids = json.load(sys.stdin)[:15]
stories = []
for sid in ids:
    try:
        with urllib.request.urlopen(f'https://hacker-news.firebaseio.com/v0/item/{sid}.json', timeout=3) as r:
            s = json.load(r)
            stories.append({'title': s.get('title',''), 'url': s.get('url',''), 'score': s.get('score',0), 'by': s.get('by','')})
    except: pass
print(json.dumps(stories, indent=2))
" > "$OUT/hn-top.json" 2>/dev/null || echo '[]' > "$OUT/hn-top.json"

# 3. AI/Tech RSS (Techcrunch, The Verge, MIT Tech Review)
echo "Fetching RSS feeds..."
python3 << 'PYEOF'
import urllib.request, json, re, os
from xml.etree import ElementTree as ET

FEEDS = [
    ("techcrunch-ai",  "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("verge-ai",       "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("mit-tech",       "https://feeds.feedburner.com/mit/xia"),
]

OUT = "/tmp/briefing-data"
all_items = []

def strip_tags(text):
    return re.sub('<[^>]+>', '', text or '').strip()[:300]

for name, url in FEEDS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            root = ET.parse(r).getroot()
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        items = root.findall('.//item') or root.findall('.//atom:entry', ns)
        for item in items[:5]:
            title = item.findtext('title') or item.findtext('atom:title', namespaces=ns) or ''
            link  = item.findtext('link')  or item.findtext('atom:link', namespaces=ns) or ''
            desc  = item.findtext('description') or item.findtext('atom:summary', namespaces=ns) or ''
            if isinstance(link, str) and link.startswith('http'):
                all_items.append({"source": name, "title": title.strip(), "link": link.strip(), "summary": strip_tags(desc)})
    except Exception as e:
        pass

with open(f"{OUT}/rss-feeds.json", "w") as f:
    json.dump(all_items, f, indent=2)
print(f"RSS: {len(all_items)} items")
PYEOF

echo "Done. Data in $OUT/"
ls -la "$OUT/"
