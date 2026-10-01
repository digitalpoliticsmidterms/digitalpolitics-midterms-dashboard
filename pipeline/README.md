# Toss-up races tab — pipeline

Builds `docs/races.json`, which powers `docs/races.html` (the "Toss-up races" tab).

**What it tracks:** every race the Cook Political Report rates *Toss Up* — 7 Senate (Sep 23), 5 governor (Sep 17), 22 House (Sep 25) — and only ads from each nominee's own campaign Pages. Parties, super PACs and attack Pages (e.g. "The Truth About Josh Turek", paid for by SLF PAC) are not counted.

**Files**
- `races_config.json` — races, nominees, and each candidate's Page IDs. This is the file you edit.
- `update_races.py` — pulls `ads_archive` for those Pages and writes `docs/races.json`.
- `run_races_update.sh` — pull → update → commit → push. This is what the schedule runs.
- `co.digitalpolitics.races.plist` — macOS launchd schedule (daily 08:55, after the nationwide run).

## One-time setup

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
