#!/usr/bin/env python3
"""Verify the public dashboard and retry one failed Pages deployment.

Uses public read-only GitHub APIs and the existing Git push credentials.
Never republishes an old commit or changes dashboard content.
"""
import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "digitalpoliticsmidterms/digitalpolitics-midterms-dashboard"
LIVE = "https://digitalpoliticsmidterms.github.io/digitalpolitics-midterms-dashboard/data.json"


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "DigitalPolitics-publication-check", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def publication_matches(expected, live):
    return all(live.get(key) == expected.get(key) for key in ("daily_observations", "cumulative", "baseline", "daily_update"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait per deployment attempt")
    parser.add_argument("--check-only", action="store_true", help="Do not retry or create commits")
    args = parser.parse_args()
    expected = json.loads((ROOT / "docs/data.json").read_text())
    latest = expected["daily_observations"][-1]["report_end_date"]
    for attempt in range(2):
        sha = git("rev-parse", "HEAD")
        deadline = time.monotonic() + args.timeout
        failure = None
        while True:
            try:
                live = get_json(LIVE + "?publication_check=" + str(time.time_ns()))
                if publication_matches(expected, live):
                    print(f"Confirmed live dashboard includes {latest} and matches the local report data.", flush=True)
                    return
                runs = get_json(f"https://api.github.com/repos/{REPO}/actions/runs?head_sha={sha}&per_page=20")["workflow_runs"]
                pages = [r for r in runs if r["name"] == "pages build and deployment"]
                if pages:
                    run = pages[0]
                    print(f"Pages deployment: {run['status']} / {run['conclusion']} — {run['html_url']}", flush=True)
                    if run["status"] == "completed" and run["conclusion"] != "success":
                        failure = run["conclusion"]
                        break
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
                print(f"Publication check temporarily unavailable: {error}", flush=True)
            if time.monotonic() >= deadline:
                raise SystemExit(f"Publication not confirmed for {latest}. Check https://github.com/{REPO}/actions. No extra deployment was triggered while another may still be running.")
            time.sleep(min(15, max(0, deadline - time.monotonic())))
        if args.check_only or attempt == 1:
            raise SystemExit(f"Pages deployment {failure}; dashboard publication not confirmed. Check https://github.com/{REPO}/actions.")
        # Retry only the current pushed main commit. Refuse unrelated staged work.
        if git("branch", "--show-current") != "main" or git("diff", "--cached", "--name-only"):
            raise SystemExit("Cannot safely retry: main branch and an empty staging area are required.")
        if git("ls-remote", "origin", "refs/heads/main").split()[0] != sha:
            raise SystemExit("Remote main changed; refusing to trigger publication from an older commit.")
        print("Retrying failed Pages publication once with a content-free commit.", flush=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Retry failed GitHub Pages publication"], cwd=ROOT, check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
