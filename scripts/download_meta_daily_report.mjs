#!/usr/bin/env node
/**
 * Download Meta Ad Library's public US "Last day" report.
 *
 * Run:
 *   HEADFUL=1 META_REPORT_DIR="$PWD/data/raw" \
 *   node scripts/download_meta_daily_report.mjs
 */

import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, rename, writeFile, access, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const REPORT_URL =
  "https://www.facebook.com/ads/library/report/?source=onboarding";

const outDir = path.resolve(process.env.META_REPORT_DIR || "./data/raw");
const headless = process.env.HEADFUL !== "1";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (data) => createHash("sha256").update(data).digest("hex");

async function clickExact(page, text) {
  const locator = page.getByText(text, { exact: true });
  const count = await locator.count();

  if (!count) {
    throw new Error(
      `Could not find "${text}". Meta may have changed the report page.`
    );
  }

  await locator.last().click();
}

async function chooseUnitedStates(page) {
  const countryBox = page.locator('[role="combobox"]').first();

  await countryBox.click();

  // Meta virtualises the country list; the search input appears after opening.
  const countrySearch = page.locator('input[role="combobox"]').last();

  await countrySearch.fill("United States");

  const unitedStates = page.getByRole("option", {
    name: "United States",
    exact: true
  });

  await unitedStates.waitFor({ timeout: 10_000 });
  await unitedStates.click();

  await page.waitForTimeout(1_500);
}

async function chooseExportLastDay(page) {
  /*
   * This is Meta's separate "Download a full report" range control.
   * It is not the chart's date-range tab bar.
   */
  const rangeButton = page.getByRole("button", {
    name: /^(Last day|Last 7 days|Last 30 days|Last 90 days|All dates)$/
  }).last();

  await rangeButton.click();

  const dailyOption = page.getByRole("menuitem", {
    name: "Last day",
    exact: true
  }).last();

  await dailyOption.waitFor({ state: "visible", timeout: 10_000 });
  await dailyOption.click();

  await page
    .getByRole("button", { name: "Last day", exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 10_000 });
}

async function selectedEndDate(page) {
  const picker = page.locator('input[placeholder="dd/mm/yyyy"]');

  const value = (
    await picker.inputValue({ timeout: 10_000 }).catch(() => "")
  ).trim();

  if (/^[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}$/.test(value)) {
    return value;
  }

  return null;
}

async function main() {
  await mkdir(outDir, { recursive: true });

  const browser = await chromium.launch({ headless });

  const context = await browser.newContext({
    acceptDownloads: true,
    locale: "en-US"
  });

  const page = await context.newPage();

  try {
    await page.goto(REPORT_URL, {
      waitUntil: "domcontentloaded",
      timeout: 60_000
    });

    await page
      .getByText("Meta Ad Library report", { exact: true })
      .first()
      .waitFor({ timeout: 30_000 });

    await chooseUnitedStates(page);
    await chooseExportLastDay(page);
    await sleep(800);

    const reportEndDate = await selectedEndDate(page);

    if (!reportEndDate) {
      throw new Error(
        "Could not read Meta's report end date. No file was saved."
      );
    }

    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 60_000 }),
      clickExact(page, "Download Report")
    ]);

    const originalName =
      download.suggestedFilename() || "meta_ad_library_report.zip";

    // Wait for the download to complete before Chromium closes.
    const tempPath = await download.path();

    if (!tempPath) {
      throw new Error("Download did not provide a local file.");
    }

    // Prevent a 90-day archive from entering the daily corpus.
    if (!originalName.endsWith("_US_yesterday.zip")) {
      throw new Error(
        `Expected a US Last day report, but Meta returned: ${originalName}`
      );
    }

    const safeDate = reportEndDate.replaceAll(" ", "-");

    const destination = path.join(
      outDir,
      `meta-us-last-day-end-${safeDate}__${originalName}`
    );

    try {
      await access(destination);
      console.log(
        `Already present; leaving existing raw report untouched: ${destination}`
      );
      return;
    } catch {
      // Expected for a new report date.
    }

    await rename(tempPath, destination);

    const bytes = await readFile(destination);

    const manifest = {
      source: REPORT_URL,
      country: "United States",
      range: "Last day",
      report_end_date_displayed_by_meta: reportEndDate,
      downloaded_at_utc: new Date().toISOString(),
      filename: path.basename(destination),
      bytes: bytes.length,
      sha256: sha256(bytes),
      notes:
        "Raw public Meta report retained unchanged. Do not sum overlapping report windows."
    };

    await writeFile(
      `${destination}.manifest.json`,
      `${JSON.stringify(manifest, null, 2)}\n`
    );

    console.log(`Saved ${destination}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(`META REPORT DOWNLOAD FAILED: ${error.message}`);
  process.exitCode = 1;
});