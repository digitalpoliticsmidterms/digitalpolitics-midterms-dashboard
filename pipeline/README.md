# Toss-up races tab — pipeline

Builds `docs/races.json`, which powers `docs/races.html` (the "Toss-up races" tab).

**What it tracks:** every race the Cook Political Report rates *Toss Up* — 7 Senate (Sep 23), 5 governor (Sep 17), 22 House (Sep 25) — and only ads from each nominee's own campaign Pages. Parties, super PACs and attack Pages (e.g. "The Truth About Josh Turek", paid for by SLF PAC) are not counted.

**Google layer:** the same candidate-only boundary applies. Google advertiser accounts are never inferred in the published tracker: add only a reviewed official campaign account ID in the candidate's `google_advertiser_ids` array in `races_config.json`. The collector takes a daily snapshot of Google's public cumulative US political-ad table, then derives the change from the prior snapshot.

**Files**
- `races_config.json` — races, nominees, and each candidate's Page IDs. This is the file you edit.
- `update_races.py` — pulls `ads_archive` for those Pages and writes `docs/races.json`.
- `run_races_update.sh` — pull → update → commit → push. This is what the schedule runs.
- `collect_google_ads.py` — optional public-BigQuery collector for nationwide Google data and reviewed candidate campaign accounts.
- `co.digitalpolitics.races.plist` — macOS launchd schedule (daily 08:55, after the nationwide run).

## One-time setup

### Optional: Google political-ad layer

1. Create a small Google Cloud project with BigQuery enabled. Public-dataset storage is free; Google bills only for queries, and the first 1 TB/month is free. Install the command-line tool and authenticate on the machine running launchd:
   ```
   brew install --cask google-cloud-sdk
   gcloud auth application-default login
   ```
2. Save the project ID locally (this file is ignored by Git):
   ```
   printf '%s\n' 'YOUR_PROJECT_ID' > pipeline/.google_cloud_project
   ```
3. Generate review suggestions, then verify every result in Google's Ads Transparency Center before copying IDs into `races_config.json`:
   ```
   python3 pipeline/collect_google_ads.py --discover
   open pipeline/google_advertiser_review.json
   ```
   Use this form for a reviewed match:
   ```json
   "google_advertiser_ids": ["AR01234567890123456789"]
   ```
4. Run a first collection:
   ```
   python3 pipeline/collect_google_ads.py
   ```
   `scripts/run_daily_pipeline.sh` will then collect Google automatically before rebuilding the nationwide page. The race job reads the resulting `site/google_data.json` and displays verified candidate Google spend alongside Meta, without combining the two sources.

1. **Long-lived token.** Graph Explorer tokens expire within hours. Exchange one for a 60-day token (Graph Explorer → "i" icon next to the token → *Open in Access Token Tool* → *Extend Access Token*). Save it in your Keychain, not in a file:
   ```
   security add-generic-password -s digitalpolitics-meta-ads -a "$USER" -w 'PASTE_TOKEN'
   ```
   A 60-day token issued now lasts past Nov 3. To replace it later, add `-U` to the same command.

2. **Fill in the missing Pages.** 16 of 70 nominees are seeded from your nationwide data. Find the rest:
   ```
   python3 pipeline/update_races.py discover
   ```
   This searches the Ad Library for each nominee's name and lists the Pages running ads about them, ranked by whether the Page name *and* the "Paid for by" line match the candidate. It prints the confident matches and writes all suggestions to `pipeline/discovered_pages.json`. Review them, then either paste the IDs into `races_config.json` or run `discover --apply` to add the high-confidence matches automatically (re-check the ones marked `page_ids_source`).

3. **First run, by hand:**
   ```
   bash pipeline/run_races_update.sh
   ```

4. **Schedule it.** Replace `REPO_PATH` in the plist with this repo's path on your Mac, then:
   ```
   cp pipeline/co.digitalpolitics.races.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/co.digitalpolitics.races.plist
   ```
   Log: `/tmp/digitalpolitics-races.log`. If you'd rather add it to the scheduler that already runs the nationwide report, call `pipeline/run_races_update.sh` from there instead.

## Notes
- Raw API responses are saved privately to `~/DigitalPolitics/raw/races/` (override with `RACES_RAW_DIR`), never to `docs/`.
- Ads counted: delivered in the US since **Jun 23, 2026**, the start of the nationwide 90-day baseline (`window_start` in the config). Meta reports each ad's lifetime spend as a range, so ads that started earlier can carry earlier spend.
- Cook ratings change. When they do, edit `races_config.json` (add/remove races, update `ratings_source.as_of`) and the next run picks it up.
- If the token expires, the run stops with a clear message and nothing is published.
