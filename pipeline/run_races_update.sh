#!/bin/bash
# Daily toss-up races update: pull candidate ads from the Meta Ad Library API,
# rebuild docs/races.json, and publish it to GitHub Pages.

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "== $(date '+%Y-%m-%d %H:%M') toss-up races update"

git pull --rebase --autostash -q
python3 pipeline/ensure_tabs.py
python3 pipeline/update_races.py

git add docs/races.json docs/index.html

if git diff --cached --quiet; then
  echo "No change to publish."
  exit 0
fi

git commit -q -m "Update toss-up races tracker"
git push -q

echo "Published."