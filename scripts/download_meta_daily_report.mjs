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

  // Wait for the control, regardless of Meta's location-based default country.
  await countryBox.waitFor({ state: "visible", timeout: 120_000 });
  await countryBox.click();

  // Meta virtualises the country list; the search input appears after opening.
  const countrySearch = page.locator('input[role="combobox"]').last();

  await countrySearch.fill("United States");

  const unitedStates = page.getByRole("option", {
    name: "United States",
    exact: true
  });

  await unitedStates.waitFor({ timeout: 60_000 });
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

function normalizeReportDate(value) {
  const months = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];
  const text = value.trim();
  const us = text.match(/^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$/);
  const dayFirst = text.match(/^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$/);
  if (!us && !dayFirst) return null;
  const monthText = us ? us[1] : dayFirst[2];
  const day = Number(us ? us[2] : dayFirst[1]);
  const year = Number(us ? us[3] : dayFirst[3]);
  const month = months.findIndex((name) =>
    name.toLowerCase() === monthText.toLowerCase() ||
    name.slice(0, 3).toLowerCase() === monthText.toLowerCase());
  if (month < 0) return null;
  const date = new Date(Date.UTC(year, month, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month ||
      date.getUTCDate() !== day) return null;
  return `${String(day).padStart(2, "0")} ${months[month]} ${year}`;
}

async function selectedEndDate(page) {
  const picker = page.locator(
    'input[placeholder="mm/dd/yyyy"], input[placeholder="dd/mm/yyyy"]'
  );
  const value = await picker.inputValue({ timeout: 60_000 }).catch(() => "");
  return normalizeReportDate(value);
}

async function main() {
  await mkdir(outDir, { recursive: true });

  const browser = await chromium.launch({ headless });

  const context = await browser.newContext({
    acceptDownloads: true,
    locale: "en-US"
  });

  const page = await context.newPage();
  page.setDefaultTimeout(60_000);

  try {
    await page.goto(REPORT_URL, {
      waitUntil: "domcontentloaded",
      timeout: 120_000
    });

    await chooseUnitedStates(page);
    await chooseExportLastDay(page);
    await sleep(800);

    const reportEndDate = await selectedEndDate(page);

    if (!reportEndDate) {
      console.error("DATE CHECK URL:", page.url());
      const fields = await page.locator("input").evaluateAll((inputs) =>
        inputs.map((input) => ({
          placeholder: input.getAttribute("placeholder"),
          label: input.getAttribute("aria-label"),
          type: input.type,
          value: /date|dd|mm|yyyy/i.test(
            [input.type, input.getAttribute("placeholder"), input.getAttribute("aria-label")].join(" ")
          ) ? input.value : "[omitted]"
        }))
      );
      console.error("DATE CHECK FIELDS:", JSON.stringify(fields, null, 2));
      console.error("DATE CHECK PAGE TEXT:",
        (await page.locator("body").innerText()).slice(0, 12000));
      throw new Error(
        "Could not read Meta's report end date. No file was saved."
      );
    }

    const diagnosticsDir = path.resolve(outDir, "../../review");
    const networkEvents = [];
    const safeUrl = (url) => {
      try { const parsed = new URL(url); return parsed.origin + parsed.pathname; }
      catch { return "[unavailable]"; }
    };
    const record = (event) => {
      networkEvents.push(event);
      if (networkEvents.length > 100) networkEvents.shift();
    };
    const onResponse = (response) =>
      record({ url: safeUrl(response.url()), status: response.status() });
    const onRequest = (request) =>
      record({ url: safeUrl(request.url()), method: request.method(), event: "request" });
    const onFailed = (request) => record({
      url: safeUrl(request.url()), failure: request.failure()?.errorText
    });
    context.on("request", onRequest);
    context.on("response", onResponse);
    context.on("requestfailed", onFailed);

    let download;
    try {
      // Meta's export control is an anchor, not a button. The menu's
      // "Download report" entry has role menuitem and must not be clicked here.
      const exportLink = page.getByRole("link", { name: /^Download report$/i });
      await exportLink.waitFor({ state: "visible", timeout: 60_000 });
      console.log("Downloading United States Last day report ending", reportEndDate);
      [download] = await Promise.all([
        page.waitForEvent("download", { timeout: 60_000 }),
        exportLink.click()
      ]);
    } catch (error) {
      // Save evidence before finally closes the browser, without hiding the error.
      try {
        await mkdir(diagnosticsDir, { recursive: true });
        const prefix = path.join(diagnosticsDir,
          `meta-download-failure-${new Date().toISOString().replace(/[:.]/g, "-")}`);
        const pages = [];
        for (const [index, currentPage] of context.pages().entries()) {
          const screenshot = `${prefix}-tab-${index + 1}.png`;
          const item = { url: safeUrl(currentPage.url()) };
          item.text = await currentPage.locator("body").innerText({ timeout: 5000 })
            .catch((e) => `[Could not read page: ${e.message}]`);
          await currentPage.screenshot({ path: screenshot, fullPage: true, timeout: 10000 })
            .then(() => { item.screenshot = screenshot; })
            .catch((e) => { item.screenshotError = e.message; });
          pages.push(item);
          console.error(`DOWNLOAD CHECK TAB ${index + 1}:`, JSON.stringify(item, null, 2));
        }
        const reportPath = `${prefix}.json`;
        await writeFile(reportPath, JSON.stringify({
          captured_at_utc: new Date().toISOString(),
          error: error.message, report_end_date: reportEndDate,
          pages, networkEvents
        }, null, 2) + "\n");
        console.error("DOWNLOAD CHECK NETWORK:", JSON.stringify(networkEvents, null, 2));
        console.error("DOWNLOAD CHECK SAVED:", reportPath);
      } catch (diagnosticError) {
        console.error("Could not save download diagnostics:", diagnosticError.message);
      }
      throw error;
    } finally {
      context.off("request", onRequest);
      context.off("response", onResponse);
      context.off("requestfailed", onFailed);
    }

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