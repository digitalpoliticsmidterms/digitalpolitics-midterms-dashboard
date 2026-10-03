#!/usr/bin/env python3
"""Build aggregate, public-safe data for the Digital Politics ad tracker."""

import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT = ROOT / "site" / "data.json"
PUBLIC_DAILY_UPDATE = ROOT / "classifications" / "public_daily_update.txt"
REVIEW_DIR = ROOT / "review"

MONTHS = {
    month: number
    for number, month in enumerate(
        "January February March April May June July August September October November December".split(),
        1,
    )
}

EXCLUDE_LOCATIONS = {
    "American Samoa",
    "Guam",
    "Northern Mariana Islands",
    "Puerto Rico",
    "Virgin Islands (U.S.)",
    "Unknown",
}


def report_date(filename):
    match = re.search(r"end-(\d{2})-([A-Za-z]+)-(\d{4})", filename)

    if not match:
        raise ValueError(f"Cannot read a report date from: {filename}")

    day, month, year = match.groups()
    return date(int(year), MONTHS[month], int(day)).isoformat()


def amount(value):
    return float((value or "0").replace(",", "").replace("≤", "").strip() or 0)


def rows_from_zip(path, suffix):
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))

        with archive.open(name) as raw:
            yield from csv.DictReader(
                io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            )


def states(path):
    result = [
        {
            "state": row["Location name"],
            "spend_usd": amount(row["Amount spent (USD)"]),
        }
        for row in rows_from_zip(path, "_locations.csv")
        if row["Location name"] not in EXCLUDE_LOCATIONS
    ]

    return sorted(result, key=lambda row: row["spend_usd"], reverse=True)


def advertisers(path):
    result = [
        {
            "page_id": row["Page ID"],
            "page_name": row["Page name"],
            "disclaimer": row["Disclaimer"],
            "spend_usd": amount(row["Amount spent (USD)"]),
            "ad_count": int(row["Number of ads in Library"] or 0),
        }
        for row in rows_from_zip(path, "_advertisers.csv")
    ]

    return sorted(result, key=lambda row: row["spend_usd"], reverse=True)


def dollars(value):
    return f"${value:,.0f}"


def write_review_brief(daily):
    """Write three distinct, ready-to-review public-update options."""
    if not daily:
        return

    latest = daily[-1]
    latest_states = latest["states"]
    latest_advertisers = latest["top_advertisers"]
    total = latest["national_spend_usd"]
    top_state = latest_states[0]
    top_advertiser = latest_advertisers[0]

    lines = [
        f"# Daily Meta ad review — {latest['report_end_date']}",
        "",
        "Choose, edit, or combine one option below. Nothing is published automatically.",
        "",
        "## Option 1 — National movement",
        "",
    ]

    if len(daily) > 1:
        previous = daily[-2]
        difference = total - previous["national_spend_usd"]
        direction = "up" if difference >= 0 else "down"

        lines.append(
            f"Reported US political, election and issue-ad spend on Facebook was "
            f"{dollars(total)} on {latest['report_end_date']}, {direction} "
            f"{dollars(abs(difference))} "
            f"({abs(difference) / previous['national_spend_usd'] * 100:.1f}%) "
            f"from the prior observed report."
        )
    else:
        lines.append(
            f"Reported US political, election and issue-ad spend on Facebook was "
            f"{dollars(total)} on {latest['report_end_date']}."
        )

    lines.extend(
        [
            "",
            "## Option 2 — State concentration",
            "",
            (
                f"{top_state['state']} recorded the largest share of reported US "
                f"political, election and issue-ad spend on Facebook on "
                f"{latest['report_end_date']}: {dollars(top_state['spend_usd'])}, "
                f"or {top_state['spend_usd'] / total * 100:.1f}% of the day's total."
            ),
            "",
            "## Option 3 — Leading advertiser",
            "",
            (
                f"{top_advertiser['page_name']} was the leading reported advertiser "
                f"on Facebook on {latest['report_end_date']}, with "
                f"{dollars(top_advertiser['spend_usd'])} in spend across "
                f"{top_advertiser['ad_count']:,} ads in Meta's Ad Library."
            ),
            "",
            "## Publish one approved update",
            "",
            (
                "Copy one approved sentence into "
                "classifications/public_daily_update.txt, then rerun "
                "python3 scripts/build_dashboard.py. Leave that file empty to "
                "publish no daily update."
            ),
        ]
    )

    REVIEW_DIR.mkdir(exist_ok=True)

    review_path = REVIEW_DIR / f"daily-meta-ad-review-{latest['report_end_date']}.md"

    review_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main():
    reports = sorted(RAW_DIR.glob("*.zip"))

    baseline_zip = next(
        (path for path in reports if "last_90_days" in path.name),
        None,
    )

    if not baseline_zip:
        raise SystemExit("No 90-day baseline ZIP found in data/raw.")

    baseline_end = report_date(baseline_zip.name)
    baseline_states = states(baseline_zip)
    baseline_advertisers = advertisers(baseline_zip)

    daily = []

    for path in reports:
        if "yesterday" not in path.name:
            continue

        daily_states = states(path)
        daily_advertisers = advertisers(path)

        daily.append(
            {
                "report_end_date": report_date(path.name),
                "national_spend_usd": sum(
                    row["spend_usd"] for row in daily_states
                ),
                "states": daily_states,
                "top_advertisers": daily_advertisers[:100],
            }
        )

    daily.sort(key=lambda row: row["report_end_date"])
    write_review_brief(daily)

    later_daily = [
        row for row in daily if row["report_end_date"] > baseline_end
    ]

    cumulative_states = defaultdict(
        float,
        {row["state"]: row["spend_usd"] for row in baseline_states},
    )

    cumulative_advertisers = {}

    for row in baseline_advertisers:
        key = (row["page_id"], row["disclaimer"])

        cumulative_advertisers[key] = {
            "page_id": row["page_id"],
            "page_name": row["page_name"],
            "disclaimer": row["disclaimer"],
            "spend_usd": row["spend_usd"],
            "ad_count": row["ad_count"],
        }

    for day in later_daily:
        for row in day["states"]:
            cumulative_states[row["state"]] += row["spend_usd"]

        source = next(
            path
            for path in reports
            if "yesterday" in path.name
            and report_date(path.name) == day["report_end_date"]
        )

        for row in advertisers(source):
            key = (row["page_id"], row["disclaimer"])

            if key not in cumulative_advertisers:
                cumulative_advertisers[key] = {
                    "page_id": row["page_id"],
                    "page_name": row["page_name"],
                    "disclaimer": row["disclaimer"],
                    "spend_usd": 0,
                    "ad_count": 0,
                }

            cumulative_advertisers[key]["spend_usd"] += row["spend_usd"]
            cumulative_advertisers[key]["ad_count"] += row["ad_count"]

    cumulative_state_rows = sorted(
        (
            {"state": state, "spend_usd": spend}
            for state, spend in cumulative_states.items()
        ),
        key=lambda row: row["spend_usd"],
        reverse=True,
    )

    output = {
        "baseline": {
            "report_end_date": baseline_end,
            "national_spend_usd": sum(
                row["spend_usd"] for row in baseline_states
            ),
        },
        "cumulative": {
            "national_spend_usd": sum(
                row["spend_usd"] for row in baseline_states
            )
            + sum(row["national_spend_usd"] for row in later_daily),
            "advertiser_count": len(cumulative_advertisers),
            "top_advertisers": sorted(
                cumulative_advertisers.values(),
                key=lambda row: row["spend_usd"],
                reverse=True,
            )[:100],
            "states": cumulative_state_rows,
            "daily_reports_added": len(later_daily),
        },
        "daily_observations": daily,
        "daily_update": (
            PUBLIC_DAILY_UPDATE.read_text(encoding="utf-8").strip()
            if PUBLIC_DAILY_UPDATE.exists()
            else ""
        ),
        "methodology": (
            "Digital Politics automatically collects Meta’s public United States "
            "‘Last day’ Ad Library report and retains each raw report privately. "
            "The cumulative figure begins with the 90-day baseline ending Sept 20, "
            "2026, then adds subsequent daily reports. Figures cover political, "
            "election and issue ads delivered via Facebook in the United States, "
            "including Washington, DC, but excluding territories and Meta’s "
            "‘Unknown’ location. State delivery does not establish congressional-"
            "district targeting, campaign coordination or electoral effect."
        ),
    }

    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Built {OUTPUT}")
    print(f"Daily observations: {len(daily)}")
    print(f"Later reports added to cumulative: {len(later_daily)}")

    if daily:
        review_path = REVIEW_DIR / f"daily-meta-ad-review-{daily[-1]['report_end_date']}.md"
        print(f"Review brief: {review_path}")


if __name__ == "__main__":
    main()