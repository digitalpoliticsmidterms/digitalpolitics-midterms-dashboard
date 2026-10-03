#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Collect Meta's US daily report before rebuilding the dashboard.
META_REPORT_DIR="$ROOT/data/raw" \
node "$ROOT/scripts/download_meta_daily_report.mjs"

# Optional Google layer. It activates once pipeline/.google_cloud_project has
# been created during the one-time BigQuery setup.
if [[ -n "${GOOGLE_CLOUD_PROJECT:-}" || -f "$ROOT/pipeline/.google_cloud_project" ]]; then
  python3 "$ROOT/pipeline/collect_google_ads.py"
else
  echo "Google political-ad collection not configured; skipping."
fi

python3 scripts/build_dashboard.py
python3 scripts/send_review_email.py

cp -R site/. docs/
python3 pipeline/ensure_tabs.py
python3 pipeline/ensure_google_panel.py

git add docs

if ! git diff --cached --quiet; then
  git commit -m "Update daily midterms dashboard"
  git push
fi
