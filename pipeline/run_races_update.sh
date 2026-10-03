#!/bin/bash
# Daily toss-up races update: pull candidate ads from the Meta Ad Library API,
# rebuild docs/races.json, and publish it to GitHub Pages.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
echo "== $(date '+%Y-%m-%d %H:%M') toss-up races update"

git pull --rebase --autostash -q
python3 pipeline/ensure_tabs.py   # nationwide job can regenerate index.html without the tab bar
python3 pipeline/ensure_google_panel.py
GOOGLE_ARGS=()
if [[ -f site/google_data.json ]]; then
  GOOGLE_ARGS=(--google-data site/google_data.json)
  cp site/google_data.json docs/google_data.json
fi
python3 pipeline/update_races.py "${GOOGLE_ARGS[@]}"

git add docs/races.json docs/index.html
if [[ -f docs/google_data.json ]]; then
  git add docs/google_data.json
fi
if git diff --cached --quiet; then
  echo "No change to publish."
  exit 0
fi
git commit -q -m "Update toss-up races tracker"
git push -q
echo "Published."
