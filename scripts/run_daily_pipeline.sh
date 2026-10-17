#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The separate 08:15 LaunchAgent normally collects this report. Avoid opening
# Meta a second time when that job has just saved a report. If no recent report
# exists, collect here as a fallback.
recent_meta_report="$(python3 - "$ROOT/data/raw" <<'PY'
import sys
import time
from pathlib import Path

raw_dir = Path(sys.argv[1])
cutoff = time.time() - 30 * 60
print("yes" if any(
    path.stat().st_mtime >= cutoff
    for path in raw_dir.glob("*_US_yesterday.zip")
) else "no")
PY
)"

collection_failed=0
if [[ "$recent_meta_report" == "yes" ]]; then
  echo "Recent Meta daily report found; skipping the redundant collection."
elif ! META_REPORT_DIR="$ROOT/data/raw" \
  node "$ROOT/scripts/download_meta_daily_report.mjs"; then
  echo "Meta report collection failed; continuing with reports already on disk." >&2
  collection_failed=1
fi

# Optional Google layer. It activates once pipeline/.google_cloud_project has
# been created during the one-time BigQuery setup.
if [[ -n "${GOOGLE_CLOUD_PROJECT:-}" || -f "$ROOT/pipeline/.google_cloud_project" ]]; then
  if command -v gcloud >/dev/null 2>&1; then
    python3 "$ROOT/pipeline/collect_google_ads.py"
  else
    echo "Google collection skipped: Google Cloud CLI is unavailable. Continuing Meta update."
  fi
else
  echo "Google political-ad collection not configured; skipping."
fi

python3 scripts/build_dashboard.py

cp -R site/. docs/
python3 pipeline/ensure_tabs.py
python3 pipeline/ensure_google_panel.py

git add docs

if ! git diff --cached --quiet; then
  git commit -m "Update daily midterms dashboard"
fi

# Always retry a previous unsuccessful push, even if this run has no new data.
git push
python3 scripts/verify_pages_publication.py

# Publish first. A temporary SMTP/DNS failure must not prevent the dashboard
# update from reaching GitHub Pages.
if [[ "$collection_failed" -eq 1 ]]; then
  echo "Dashboard published from available reports; review email skipped because Meta collection failed." >&2
  exit 1
fi

if [[ -f scripts/send_review_email.py ]]; then
  if ! python3 scripts/send_review_email.py; then
    echo "Dashboard published, but the review email failed to send." >&2
  fi
else
  echo "Dashboard published; scripts/send_review_email.py is not present, so no review email was sent." >&2
fi
