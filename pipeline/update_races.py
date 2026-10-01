#!/usr/bin/env python3
"""Digital Politics — competitive-race ad tracker.

Pulls every ad run from the candidates' own Facebook Pages (Meta Ad Library
API, ads_archive) for the Cook Political Report toss-up races listed in
races_config.json, and writes the public summary to ../docs/races.json.

Commands
  python3 update_races.py                 # daily update (default)
  python3 update_races.py discover        # suggest Page IDs for candidates with none
  python3 update_races.py discover --apply  # also write high-confidence matches to the config
  python3 update_races.py --from-raw FILE # rebuild races.json from a saved raw pull (no API call)

Token: FB_ACCESS_TOKEN env var, or a macOS Keychain item named
"digitalpolitics-meta-ads" (security add-generic-password -s digitalpolitics-meta-ads -a "$USER" -w TOKEN).
Raw API responses are kept privately in RACES_RAW_DIR (default ~/DigitalPolitics/raw/races),
never in docs/.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "races_config.json"
OUT = HERE.parent / "docs" / "races.json"
RAW_DIR = Path(os.environ.get("RACES_RAW_DIR", Path.home() / "DigitalPolitics" / "raw" / "races"))
API_VERSION = os.environ.get("META_API_VERSION", "v26.0")
BASE = f"https://graph.facebook.com/{API_VERSION}/ads_archive"
FIELDS = ",".join([
    "id", "page_id", "page_name", "bylines", "currency",
    "ad_creation_time", "ad_delivery_start_time", "ad_delivery_stop_time",
    "ad_creative_bodies", "ad_creative_link_titles", "publisher_platforms",
    "spend", "impressions", "demographic_distribution", "delivery_by_region",
])
PAC_WORDS = re.compile(r"\b(PAC|FUND|LEADERSHIP|MAJORITY|ACTION|COMMITTEE FOR|AMERICANS FOR|NRCC|DCCC|NRSC|DSCC|SLF|CLF|HMP|PROJECT|ALLIANCE|COALITION|FORWARD|INC\.?)\b", re.I)
US_STATES = {"Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
             "District of Columbia", "Washington, District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho",
             "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
             "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
             "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
             "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
             "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"}


METHODOLOGY = [
    "Digital Politics selected every race the Cook Political Report rated a Toss Up for Senate, Governor and House, and then identified each nominee's own Facebook Pages (Pages whose ads carried the candidate committee's 'Paid for by' disclaimer). Super PACs, parties and outside groups were excluded, including Pages set up to attack a candidate.",
    "Each day the tracker queries Meta's Ad Library API for every political ad those Pages delivered in the United States since {date}. Meta reports spend and impressions as ranges for each ad's lifetime, so totals are shown as ranges with a midpoint estimate. Ads that began before the window may include earlier spending. Weekly figures are estimates that spread each ad's reported spend evenly across its delivery dates. Audience shares are weighted by estimated impressions and describe who saw the ads, not who the campaign targeted.",
    'Raw API responses are retained privately.',
]

# ---------------------------------------------------------------- API access
def get_token():
    tok = os.environ.get("FB_ACCESS_TOKEN")
    if tok:
        return tok.strip()
    if sys.platform == "darwin":
        try:
            return subprocess.check_output(
                ["security", "find-generic-password", "-s", "digitalpolitics-meta-ads", "-w"],
                stderr=subprocess.DEVNULL, text=True).strip()
        except subprocess.CalledProcessError:
            pass
    sys.exit("No Meta token: set FB_ACCESS_TOKEN or add the 'digitalpolitics-meta-ads' Keychain item.")


def api_get(url, params=None, tries=6):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    delay = 5
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {}
            code = err.get("code")
            # 4/17/613 = rate limits; 1/2 = transient; 190 = bad/expired token
            if code == 190:
                sys.exit("Meta rejected the access token (expired or invalid). Generate a new long-lived token.")
            if code in (1, 2, 4, 17, 613) or e.code >= 500:
                print(f"  API busy (code {code}); retrying in {delay}s", file=sys.stderr)
                time.sleep(delay); delay = min(delay * 2, 300)
                continue
            sys.exit(f"Meta API error {e.code}: {err.get('message') or body[:300]}")
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  network error ({e}); retrying in {delay}s", file=sys.stderr)
            time.sleep(delay); delay = min(delay * 2, 300)
    sys.exit("Meta API kept failing; giving up for today.")


def paged(params, token, max_pages=200):
    params = dict(params, access_token=token)
    payload = api_get(BASE, params)
    pages = 0
    while True:
        yield from payload.get("data", [])
        nxt = payload.get("paging", {}).get("next")
        pages += 1
        if not nxt or pages >= max_pages:
            return
        time.sleep(0.4)
        payload = api_get(nxt)


def fetch_ads(page_ids, window_start, token):
    ads = {}
    ids = sorted(set(page_ids))
    for i in range(0, len(ids), 10):  # search_page_ids accepts up to 10 Pages
        batch = ids[i:i + 10]
        print(f"Fetching Pages {i + 1}-{i + len(batch)} of {len(ids)}")
        for ad in paged({
            "search_page_ids": json.dumps(batch),
            "ad_reached_countries": json.dumps(["US"]),
            "ad_type": "POLITICAL_AND_ISSUE_ADS",
            "ad_active_status": "ALL",
            "ad_delivery_date_min": window_start,
            "fields": FIELDS,
            "limit": 250,
        }, token):
            ads[ad["id"]] = ad
    return list(ads.values())


# ---------------------------------------------------------------- discovery
def surname(name):
    parts = [p for p in re.split(r"\s+", name) if p and not p.endswith(".")]
    return parts[-1].lower()


def discover(cfg, token, apply=False):
    out, applied = [], 0
    for race in cfg["races"]:
        for cand in race["candidates"]:
            if cand["page_ids"]:
                continue
            last = surname(cand["name"])
            pages = defaultdict(lambda: {"ads": 0, "bylines": set()})
            for ad in paged({
                "search_terms": cand["name"],
                "search_type": "KEYWORD_EXACT_PHRASE",
                "ad_reached_countries": json.dumps(["US"]),
                "ad_type": "POLITICAL_AND_ISSUE_ADS",
                "ad_active_status": "ALL",
                "ad_delivery_date_min": cfg["window_start"],
                "fields": "page_id,page_name,bylines",
                "limit": 250,
            }, token, max_pages=4):
                p = pages[ad["page_id"]]
                p["page_name"] = ad.get("page_name", "")
                p["ads"] += 1
                if ad.get("bylines"):
                    p["bylines"].add(ad["bylines"])
            suggestions = []
            for pid, p in pages.items():
                byl = " | ".join(sorted(p["bylines"]))
                name_hit = last in p["page_name"].lower()
                byline_hit = last in byl.lower()
                pac = bool(PAC_WORDS.search(byl)) and not re.search(r"for (congress|senate|governor|" + re.escape(race["state"].lower()) + ")", byl.lower())
                attack = bool(re.search(r"\b(truth|real|revealed|dangerous|let us down|exposed|failed)\b", p["page_name"], re.I))
                conf = "high" if name_hit and byline_hit and not pac and not attack else ("possible" if (name_hit or byline_hit) and not attack else "low")
                suggestions.append({"page_id": pid, "page_name": p["page_name"], "bylines": byl, "ads": p["ads"], "confidence": conf})
            suggestions.sort(key=lambda s: ({"high": 0, "possible": 1, "low": 2}[s["confidence"]], -s["ads"]))
            out.append({"race": race["id"], "candidate": cand["name"], "suggestions": suggestions[:8]})
            top = [s for s in suggestions if s["confidence"] == "high"]
            flag = ", ".join(f"{s['page_name']} ({s['page_id']}, {s['bylines']})" for s in top) or "no confident match"
            print(f"{race['id']:8} {cand['name']:28} {flag}")
            if apply and top:
                cand["page_ids"] = [s["page_id"] for s in top]
                cand["committee"] = cand.get("committee") or top[0]["bylines"].split(" | ")[0]
                cand["page_ids_source"] = "discover (auto, review)"
                applied += 1
    path = HERE / "discovered_pages.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\nSuggestions written to {path}")
    if apply:
        CONFIG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False))
        print(f"Added Pages for {applied} candidates to races_config.json — review before publishing.")


# ---------------------------------------------------------------- aggregation
def bound(d, key):
    v = (d or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rng(d):
    lo, hi = bound(d, "lower_bound"), bound(d, "upper_bound")
    lo = lo or 0.0
    return lo, (hi if hi is not None else lo), hi is None


def day(s):
    return dt.date.fromisoformat(s[:10]) if s else None


def candidate_key(race, cand):
    return f"{race['id']}|{cand['name']}"


def build(cfg, ads, run_date, previous):
    today = run_date
    window_start = dt.date.fromisoformat(cfg["window_start"])
    page_owner = {}
    for race in cfg["races"]:
        for cand in race["candidates"]:
            for pid in cand["page_ids"]:
                page_owner[str(pid)] = (race, cand)

    agg = {}
    for race in cfg["races"]:
        for cand in race["candidates"]:
            agg[candidate_key(race, cand)] = {
                "ads": 0, "active": 0, "new_7d": 0, "lo": 0.0, "hi": 0.0, "imp_lo": 0.0, "imp_hi": 0.0,
                "open_ended": 0, "platforms": defaultdict(int), "age": defaultdict(float), "gender": defaultdict(float),
                "region": defaultdict(float), "demo_weight": 0.0, "region_weight": 0.0, "weekly": defaultdict(float),
                "bylines": defaultdict(int), "pages": {}, "top": [], "non_usd": 0}

    for ad in ads:
        owner = page_owner.get(str(ad.get("page_id")))
        if not owner:
            continue
        race, cand = owner
        a = agg[candidate_key(race, cand)]
        if ad.get("currency") and ad["currency"] != "USD":
            a["non_usd"] += 1
            continue
        s_lo, s_hi, s_open = rng(ad.get("spend"))
        i_lo, i_hi, i_open = rng(ad.get("impressions"))
        s_mid, i_mid = (s_lo + s_hi) / 2, (i_lo + i_hi) / 2
        a["ads"] += 1
        a["lo"] += s_lo; a["hi"] += s_hi
        a["imp_lo"] += i_lo; a["imp_hi"] += i_hi
        a["open_ended"] += int(s_open or i_open)
        a["pages"][str(ad.get("page_id"))] = ad.get("page_name", "")
        if ad.get("bylines"):
            a["bylines"][ad["bylines"]] += 1
        start, stop = day(ad.get("ad_delivery_start_time")), day(ad.get("ad_delivery_stop_time"))
        if stop is None or stop >= today:
            a["active"] += 1
        created = day(ad.get("ad_creation_time")) or start
        if created and (today - created).days < 7:
            a["new_7d"] += 1
        for p in ad.get("publisher_platforms") or []:
            a["platforms"][p] += 1
        for row in ad.get("demographic_distribution") or []:
            pct = float(row.get("percentage") or 0)
            a["age"][row.get("age", "unknown")] += pct * i_mid
            a["gender"][row.get("gender", "unknown")] += pct * i_mid
            a["demo_weight"] += pct * i_mid
        for row in ad.get("delivery_by_region") or []:
            pct = float(row.get("percentage") or 0)
            a["region"][row.get("region", "Unknown")] += pct * i_mid
            a["region_weight"] += pct * i_mid
        # Estimated spend timeline: each ad's reported spend spread evenly over its delivery days.
        if start:
            end = min(stop or today, today)
            if end < start:
                end = start
            n = (end - start).days + 1
            for k in range(n):
                d = start + dt.timedelta(days=k)
                week = d - dt.timedelta(days=d.weekday())
                a["weekly"][week.isoformat()] += s_mid / n
        body = (ad.get("ad_creative_bodies") or [""])[0] or (ad.get("ad_creative_link_titles") or [""])[0] or ""
        a["top"].append({
            "id": ad["id"], "start": start.isoformat() if start else None, "stop": stop.isoformat() if stop else None,
            "text": re.sub(r"\s+", " ", body).strip()[:220], "spend_lo": s_lo, "spend_hi": s_hi,
            "imp_lo": i_lo, "imp_hi": i_hi, "mid": s_mid})

    # Day-on-day comparison baseline. A same-day re-run keeps the earlier baseline.
    if previous and previous.get("updated") == today.isoformat():
        baseline = previous.get("baseline") or {}
    elif previous:
        baseline = {"date": previous.get("updated"), "candidates": {
            f"{r['id']}|{c['name']}": {"spend_mid": c["spend_mid"], "ads": c["ads"], "tracked": c["tracked"]}
            for r in previous.get("races", []) for c in r.get("candidates", [])}}
    else:
        baseline = {}
    prev = baseline.get("candidates", {})

    weeks = sorted({w for a in agg.values() for w in a["weekly"] if w >= (window_start - dt.timedelta(days=window_start.weekday())).isoformat()})
    races_out = []
    for race in cfg["races"]:
        cands = []
        for cand in race["candidates"]:
            k = candidate_key(race, cand)
            a = agg[k]
            mid = (a["lo"] + a["hi"]) / 2
            dw, rw = a["demo_weight"] or 1, a["region_weight"] or 1
            regions = sorted(((r, v / rw) for r, v in a["region"].items()), key=lambda x: -x[1])
            in_state = sum(v for r, v in regions if r == race["state"]) if a["region_weight"] else None
            p = prev.get(k)
            cands.append({
                "name": cand["name"], "party": cand["party"], "incumbent": cand.get("incumbent", False),
                "tracked": bool(cand["page_ids"]) or bool(cand.get("no_meta_ads")), "no_meta_ads": cand.get("no_meta_ads"), "pages": [{"id": pid, "name": a["pages"].get(str(pid), "")} for pid in cand["page_ids"]],
                "committee": max(a["bylines"], key=a["bylines"].get) if a["bylines"] else cand.get("committee"),
                "ads": a["ads"], "active_ads": a["active"], "new_ads_7d": a["new_7d"],
                "spend_lo": round(a["lo"]), "spend_hi": round(a["hi"]), "spend_mid": round(mid),
                "impressions_lo": round(a["imp_lo"]), "impressions_hi": round(a["imp_hi"]),
                "open_ended_ads": a["open_ended"], "non_usd_ads": a["non_usd"],
                "spend_change_mid": round(mid - p["spend_mid"]) if p and p.get("tracked") else None,
                "ads_change": a["ads"] - p["ads"] if p and p.get("tracked") else None,
                "in_state_share": round(in_state, 4) if in_state is not None else None,
                "top_regions": [{"region": r, "share": round(v, 4)} for r, v in regions[:5]],
                "age": {k2: round(v / dw, 4) for k2, v in sorted(a["age"].items())} if a["demo_weight"] else {},
                "gender": {k2: round(v / dw, 4) for k2, v in sorted(a["gender"].items())} if a["demo_weight"] else {},
                "platforms": dict(sorted(a["platforms"].items(), key=lambda x: -x[1])),
                "weekly_est": [round(a["weekly"].get(w, 0)) for w in weeks],
                "top_ads": [{k2: v for k2, v in t.items() if k2 != "mid"} for t in sorted(a["top"], key=lambda t: -t["mid"])[:5]],
            })
        total = sum(c["spend_mid"] for c in cands)
        by_party = defaultdict(int)
        for c in cands:
            by_party[c["party"]] += c["spend_mid"]
        races_out.append({
            "id": race["id"], "chamber": race["chamber"], "state": race["state"], "label": race["label"],
            "held_by": race["held_by"], "rating": "Toss Up",
            "rating_date": cfg["ratings_source"]["as_of"][race["chamber"]],
            "spend_mid": total, "spend_by_party": dict(by_party),
            "candidates": sorted(cands, key=lambda c: -c["spend_mid"]),
        })

    races_out.sort(key=lambda r: -r["spend_mid"])
    all_c = [dict(c, race=r["id"], race_label=r["label"], chamber=r["chamber"]) for r in races_out for c in r["candidates"]]
    tracked = [c for c in all_c if c["tracked"]]
    history = (previous or {}).get("history", [])
    history = [h for h in history if h["date"] != today.isoformat()]
    history.append({"date": today.isoformat(), "spend_mid": sum(c["spend_mid"] for c in all_c),
                    "by_party": {p: sum(c["spend_mid"] for c in all_c if c["party"] == p) for p in sorted({c["party"] for c in all_c})},
                    "ads": sum(c["ads"] for c in all_c)})
    return {
        "updated": today.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": cfg["window_start"],
        "change_since": baseline.get("date"),
        "baseline": baseline,
        "ratings_source": cfg["ratings_source"],
        "weeks": weeks,
        "summary": {
            "races": len(races_out),
            "candidates": len(all_c), "candidates_tracked": len(tracked),
            "spend_lo": sum(c["spend_lo"] for c in all_c), "spend_hi": sum(c["spend_hi"] for c in all_c),
            "spend_mid": sum(c["spend_mid"] for c in all_c),
            "ads": sum(c["ads"] for c in all_c), "active_ads": sum(c["active_ads"] for c in all_c),
            "by_chamber": {ch: sum(r["spend_mid"] for r in races_out if r["chamber"] == ch) for ch in ("senate", "governor", "house")},
            "by_party": history[-1]["by_party"],
        },
        "top_candidates": [{k: c[k] for k in ("name", "party", "race", "race_label", "chamber", "spend_lo", "spend_hi", "spend_mid", "ads", "active_ads", "spend_change_mid")}
                           for c in sorted(tracked, key=lambda c: -c["spend_mid"])[:10]],
        "untracked": [{"name": c["name"], "race": c["race"]} for c in all_c if not c["tracked"]],
        "races": races_out,
        "history": history[-120:],
        "methodology": "\n\n".join(p.format(date=f"{w:%B} {w.day}, {w.year}") for w in [dt.date.fromisoformat(cfg["window_start"])] for p in METHODOLOGY),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="update", choices=["update", "discover"])
    ap.add_argument("--apply", action="store_true", help="discover: write high-confidence Pages to the config")
    ap.add_argument("--from-raw", help="rebuild from a saved raw pull instead of calling the API")
    ap.add_argument("--date", help="override run date (YYYY-MM-DD)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    cfg = json.loads(CONFIG.read_text())

    if args.command == "discover":
        return discover(cfg, get_token(), apply=args.apply)

    run_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    if args.from_raw:
        ads = json.loads(Path(args.from_raw).read_text())["ads"]
    else:
        page_ids = [pid for r in cfg["races"] for c in r["candidates"] for pid in c["page_ids"]]
        ads = fetch_ads(page_ids, cfg["window_start"], get_token())
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw = RAW_DIR / f"races_{run_date.isoformat()}.json"
        raw.write_text(json.dumps({"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(), "page_ids": page_ids, "ads": ads}))
        print(f"{len(ads)} ads saved privately to {raw}")

    out = Path(args.out)
    previous = json.loads(out.read_text()) if out.exists() else None
    data = build(cfg, ads, run_date, previous)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    s = data["summary"]
    print(f"Wrote {out}: {s['races']} races, {s['candidates_tracked']}/{s['candidates']} candidates tracked, "
          f"{s['ads']} ads, ${s['spend_lo']:,.0f}–${s['spend_hi']:,.0f} reported spend")
    if data["untracked"]:
        print(f"{len(data['untracked'])} candidates have no Page IDs yet — run `python3 update_races.py discover`.")


if __name__ == "__main__":
    main()
