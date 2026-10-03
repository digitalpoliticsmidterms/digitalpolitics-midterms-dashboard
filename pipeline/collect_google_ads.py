#!/usr/bin/env python3
"""Collect public US Google political-ad snapshots for Digital Politics.

Google publishes this data in BigQuery's public ``google_political_ads``
dataset.  The source provides advertiser-level cumulative amounts, not a
pre-computed daily US total, so this program keeps a private daily snapshot
and calculates changes between snapshots.  It deliberately includes only
Google advertiser IDs explicitly reviewed in races_config.json for the
candidate-race layer.  Parties, PACs and other outside groups are never
discovered or added automatically.

Setup (once, on the Mac that runs the pipeline):
  brew install --cask google-cloud-sdk
  gcloud auth application-default login
  export GOOGLE_CLOUD_PROJECT='your-google-cloud-project-id'

Commands:
  python3 pipeline/collect_google_ads.py
  python3 pipeline/collect_google_ads.py --discover

``--discover`` writes candidate-name suggestions to a private review file. It
never changes the race configuration.  Copy only verified official campaign
advertiser IDs into a candidate's ``google_advertiser_ids`` field.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "races_config.json"
PUBLIC_OUT = ROOT / "site" / "google_data.json"
PRIVATE_DIR = Path(os.environ.get(
    "GOOGLE_ADS_RAW_DIR", Path.home() / "DigitalPolitics" / "raw" / "google_ads"
))
SOURCE_TABLE = "`bigquery-public-data.google_political_ads.advertiser_stats`"


def sql_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def bq(sql, project):
    if not shutil.which("bq"):
        raise RuntimeError(
            "Google Cloud CLI is not installed. Install it with `brew install --cask google-cloud-sdk`."
        )
    command = [
        "bq", "--project_id", project, "--location=US", "query",
        "--nouse_legacy_sql", "--format=json", sql,
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "BigQuery query failed.")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"BigQuery did not return JSON: {exc}") from exc


def configured_candidates(config):
    rows = []
    for race in config["races"]:
        for candidate in race["candidates"]:
            ids = candidate.get("google_advertiser_ids", [])
            rows.append({
                "key": f"{race['id']}|{candidate['name']}",
                "race": race["id"],
                "candidate": candidate["name"],
                "advertiser_ids": [str(value) for value in ids],
            })
    return rows


def collect(project, config):
    today = dt.date.today().isoformat()
    candidates = configured_candidates(config)
    ids = sorted({item for row in candidates for item in row["advertiser_ids"]})

    # ``advertiser_stats`` contains an aggregate per advertiser/region. US is
    # the region value used in Google's public political-ads dataset.
    national_sql = f"""
      SELECT
        ROUND(SUM(COALESCE(CAST(spend_usd AS NUMERIC), 0))) AS spend_usd
      FROM {SOURCE_TABLE}
      WHERE regions = 'US'
    """
    top_sql = f"""
      SELECT advertiser_id, advertiser_name,
             ROUND(SUM(COALESCE(CAST(spend_usd AS NUMERIC), 0))) AS spend_usd
      FROM {SOURCE_TABLE}
      WHERE regions = 'US'
      GROUP BY advertiser_id, advertiser_name
      ORDER BY spend_usd DESC
      LIMIT 10
    """
    national_rows = bq(national_sql, project)
    top_rows = bq(top_sql, project)

    advertiser_rows = []
    if ids:
        id_list = ", ".join(sql_string(value) for value in ids)
        advertiser_sql = f"""
          SELECT advertiser_id, advertiser_name,
                 ROUND(SUM(COALESCE(CAST(spend_usd AS NUMERIC), 0))) AS spend_usd
          FROM {SOURCE_TABLE}
          WHERE regions = 'US' AND advertiser_id IN ({id_list})
          GROUP BY advertiser_id, advertiser_name
        """
        advertiser_rows = bq(advertiser_sql, project)

    spend_by_id = {str(row["advertiser_id"]): float(row.get("spend_usd") or 0) for row in advertiser_rows}
    name_by_id = {str(row["advertiser_id"]): row.get("advertiser_name", "") for row in advertiser_rows}
    candidate_rows = []
    for candidate in candidates:
        matched = [identifier for identifier in candidate["advertiser_ids"] if identifier in spend_by_id]
        candidate_rows.append({
            "key": candidate["key"], "race": candidate["race"], "candidate": candidate["candidate"],
            "configured_advertiser_ids": candidate["advertiser_ids"],
            "matched_advertisers": [
                {"id": identifier, "name": name_by_id[identifier], "spend_usd": round(spend_by_id[identifier])}
                for identifier in matched
            ],
            "spend_usd": round(sum(spend_by_id[identifier] for identifier in matched)),
            "tracked": bool(candidate["advertiser_ids"]),
        })

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    snapshots_path = PRIVATE_DIR / "national_snapshots.json"
    snapshots = json.loads(snapshots_path.read_text()) if snapshots_path.exists() else []
    national_total = float((national_rows[0] if national_rows else {}).get("spend_usd") or 0)
    snapshots = [row for row in snapshots if row.get("date") != today]
    previous = snapshots[-1] if snapshots else None
    snapshots.append({"date": today, "spend_usd": round(national_total)})
    snapshots_path.write_text(json.dumps(snapshots[-400:], indent=2) + "\n")

    data = {
        "updated": today,
        "source": "Google Political Advertising Transparency Report / public BigQuery dataset",
        "scope": "US election ads from verified Google political advertisers. Candidate-race figures include only manually reviewed official campaign advertiser IDs; parties, PACs, super PACs and other outside groups are excluded.",
        "national": {
            "reported_cumulative_spend_usd": round(national_total),
            "change_since_prior_snapshot_usd": (
                round(national_total - float(previous["spend_usd"])) if previous else None
            ),
            "prior_snapshot_date": previous.get("date") if previous else None,
            "top_advertisers": [
                {"id": str(row["advertiser_id"]), "name": row.get("advertiser_name", ""),
                 "spend_usd": round(float(row.get("spend_usd") or 0))}
                for row in top_rows
            ],
        },
        "candidates": candidate_rows,
        "mapping_status": {
            "candidates_total": len(candidate_rows),
            "candidates_with_reviewed_ids": sum(row["tracked"] for row in candidate_rows),
            "matched_advertiser_ids": len(spend_by_id),
        },
        "methodology": "Google's public advertiser table is cumulative. Digital Politics takes and privately retains one US snapshot each day; the published change is the difference from the prior snapshot and can reflect late reporting or corrections. Google figures are not added to Meta figures automatically.",
    }
    PUBLIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {PUBLIC_OUT}: US cumulative ${national_total:,.0f}; "
          f"{data['mapping_status']['candidates_with_reviewed_ids']}/{len(candidate_rows)} candidates configured.")


def discover(project, config):
    # Suggestions are deliberately based on names only. A human must verify the
    # official campaign before an ID can be used in the public tracker.
    candidates = configured_candidates(config)
    surnames = {
        row["candidate"]: re.split(r"\\s+", row["candidate"].strip())[-1].casefold()
        for row in candidates
    }
    # Campaign accounts often use names such as “Smith for Congress,” rather
    # than a candidate's full name. This deliberately returns broad surname
    # matches for a human review instead of guessing ownership.
    pattern = "(" + "|".join(sorted({re.escape(value) for value in surnames.values()})) + ")"
    sql = f"""
      SELECT advertiser_id, advertiser_name,
             ROUND(SUM(COALESCE(CAST(spend_usd AS NUMERIC), 0))) AS spend_usd
      FROM {SOURCE_TABLE}
      WHERE regions = 'US'
        AND REGEXP_CONTAINS(LOWER(advertiser_name), r{sql_string(pattern)})
      GROUP BY advertiser_id, advertiser_name
      ORDER BY advertiser_name, spend_usd DESC
    """
    found = bq(sql, project)
    by_candidate = {name: [] for name in surnames}
    for row in found:
        name = row["advertiser_name"].casefold()
        item = {"id": str(row["advertiser_id"]), "name": row["advertiser_name"],
                "spend_usd": round(float(row.get("spend_usd") or 0))}
        for candidate, surname in surnames.items():
            if re.search(rf"\\b{re.escape(surname)}\\b", name):
                by_candidate[candidate].append(item)
    review = [{
        "race": row["race"], "candidate": row["candidate"],
        "suggestions": by_candidate[row["candidate"]],
        "instructions": "Verify this is the official candidate campaign, then copy its ID into google_advertiser_ids in races_config.json. Do not add PACs, party committees or outside groups.",
    } for row in candidates]
    path = HERE / "google_advertiser_review.json"
    path.write_text(json.dumps(review, indent=2) + "\n")
    print(f"Wrote review suggestions to {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    project_file = HERE / ".google_cloud_project"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project and project_file.exists():
        project = project_file.read_text(encoding="utf-8").strip()
    if not project:
        raise SystemExit("Set GOOGLE_CLOUD_PROJECT or create pipeline/.google_cloud_project before collecting Google political-ad data.")
    config = json.loads(CONFIG.read_text())
    if args.discover:
        discover(project, config)
    else:
        collect(project, config)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"Google political-ad collection failed: {error}", file=sys.stderr)
        sys.exit(1)
