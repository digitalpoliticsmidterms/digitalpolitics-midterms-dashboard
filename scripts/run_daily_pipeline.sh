#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 scripts/build_dashboard.py
python3 pipeline/ensure_tabs.py
python3 scripts/send_review_email.py
cp -R site/. docs/
git add docs
if ! git diff --cached --quiet; then
  git commit -m "Update daily midterms dashboard"
  git push
fi
