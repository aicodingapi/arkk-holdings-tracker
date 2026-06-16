#!/usr/bin/env python3
"""
Download the latest ARKK Fund Holdings CSV from ark-funds.com using Playwright.

The script:
1. Opens the ARK "Download Fund Materials" page with Playwright
2. Handles the region/consent gate ("I Agree")
3. Locates the direct download link for ARKK "Fund Holdings CSV"
4. Downloads the CSV (using Playwright's request context)
5. Saves it to data_arkk/arkk_yyyymmdd.csv based on the data date

Skip logic:
- Before launching any browser, the script checks whether a file named
  arkk_YYYYMMDD.csv already exists in data_arkk/ where YYYYMMDD is today's
  system date (from datetime.now()).
- If the file for today already exists, the program exits immediately
  without accessing the webpage or performing any downloads.
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


ARK_DOWNLOAD_PAGE = "https://www.ark-funds.com/download-fund-materials#docsListing"
OUTPUT_DIR = Path("data_arkk")


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def find_arkk_csv_url(page) -> str | None:
    """
    Find the direct download URL for the ARKK daily holdings CSV.
    Strictly prefers links ending in .csv (not .pdf).
    """
    html = page.content()

    # Strategy 1: Direct exact match on the known ARKK CSV filename (most reliable)
    match = re.search(
        r'https?://assets\.ark-funds\.com[^"\'<>\s)]*ARK_INNOVATION_ETF_ARKK_HOLDINGS\.csv',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(0)

    # Strategy 2: Look for any <a> whose href contains the ARKK CSV (case-insensitive)
    for a in page.locator("a").all():
        try:
            href = a.get_attribute("href") or ""
            if re.search(r"ARK_INNOVATION_ETF_ARKK_HOLDINGS\.csv", href, re.IGNORECASE):
                if href.startswith("http"):
                    return href
                if href.startswith("/"):
                    # The assets are on assets.ark-funds.com in practice
                    return "https://assets.ark-funds.com" + href
                return href
        except Exception:
            continue

    # Strategy 3: Fallback - any link that has both "ARKK" and ".csv" near "holdings"
    for a in page.locator("a").all():
        try:
            href = a.get_attribute("href") or ""
            text = (a.inner_text() or "").lower()
            low_href = href.lower()
            if ".csv" in low_href and "arkk" in (low_href + text) and "holdings" in (low_href + text):
                if href.startswith("http"):
                    return href
                if href.startswith("/"):
                    return "https://assets.ark-funds.com" + href
                return href
        except Exception:
            continue

    return None


def download_csv(page, url: str) -> bytes:
    """Download the CSV content using Playwright's API request context."""
    print(f"Downloading from:\n  {url}")
    request_context = page.request
    response = request_context.get(url, timeout=30000)
    if response.status != 200:
        raise RuntimeError(f"Failed to download: HTTP {response.status}")

    content = response.body()

    # Basic validation: make sure we actually got CSV, not PDF/HTML
    head = content[:200].decode("utf-8", errors="replace").lower()
    if "pdf" in head or head.startswith("%pdf") or "<html" in head:
        raise RuntimeError("Downloaded content does not look like CSV (got PDF or HTML instead)")

    if not content.lstrip().startswith(b"date,fund,company"):
        # Still try, but warn — the header might have slight variations
        print("Warning: CSV header did not match expected 'date,fund,company...'")

    return content


def _parse_ark_date(raw: str) -> str | None:
    """Try to parse a raw date string from the ARKK CSV into YYYYMMDD format."""
    if not raw:
        return None
    s = raw.strip().strip('"').strip("'")
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def save_holdings_csv(content: bytes) -> Path:
    """
    Save the CSV bytes.
    Reads the CSV properly, takes the first value of the first data row as the date,
    and uses it for the filename arkk_yyyymmdd.csv.
    Falls back to today's date only if the date cannot be read from the file.
    """
    ensure_output_dir()

    text = content.decode("utf-8", errors="replace")

    date_str = None
    try:
        reader = csv.reader(io.StringIO(text))
        # Skip header row
        next(reader, None)
        # Get first data row and take its first column (the date)
        first_row = next(reader, None)
        if first_row and len(first_row) > 0:
            raw_date = first_row[0]
            date_str = _parse_ark_date(raw_date)
            if date_str:
                print(f"Using date from downloaded file: {raw_date} → {date_str}")
    except Exception as e:
        print(f"Warning: Error while reading date from CSV ({e})")

    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")
        print(f"Warning: Could not get date from CSV data. Falling back to today's date: {date_str}")

    filename = f"arkk_{date_str}.csv"
    out_path = OUTPUT_DIR / filename

    try:
        out_path.write_bytes(content)
        print(f"Saved: {out_path} ({len(content)} bytes)")
    except PermissionError:
        print(f"\n⚠️  Permission denied when writing {out_path}")
        print("    The file may be open in Excel, a text editor, or another program.")
        print("    Close any application that has the CSV open and re-run the script,")
        print("    or manually save/delete the old file.")
        print(f"    (Date was correctly read from the downloaded data as {date_str})")
    except Exception as e:
        print(f"Error writing file {out_path}: {e}")

    return out_path


def main(headless: bool = True) -> None:
    print("=== ARKK Holdings Downloader (Playwright) ===")
    ensure_output_dir()

    # --- Early skip check using today's system date ---
    # The user wants to avoid unnecessary browser launches and downloads
    # if we already have a file for the current calendar day.
    today_str = datetime.now().strftime("%Y%m%d")
    today_file = OUTPUT_DIR / f"arkk_{today_str}.csv"

    if today_file.exists():
        print(f"File for today already exists: {today_file}")
        print("No need to download again. Skipping (browser will NOT be launched).")
        return
    else:
        print(f"No file for today ({today_str}) found. Proceeding to download...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            accept_downloads=True,
        )
        page = context.new_page()

        print(f"Loading page: {ARK_DOWNLOAD_PAGE}")
        page.goto(ARK_DOWNLOAD_PAGE, wait_until="domcontentloaded", timeout=60000)

        # Handle consent gate ("You Are Entering ark-funds.com" + "I Agree")
        try:
            for _ in range(3):
                for label in ("I Agree", "PROCEED", "Agree"):
                    try:
                        btn = page.get_by_text(label, exact=False).first
                        if btn.is_visible(timeout=1200):
                            print(f"  Dismissing gate: clicking '{label}'...")
                            btn.click(timeout=2000)
                            page.wait_for_timeout(1500)
                            break
                    except PlaywrightTimeoutError:
                        continue
        except Exception:
            pass

        # Wait for the documents list to render
        print("Waiting for document list to load...")
        page.wait_for_timeout(6000)

        # Extract the ARKK CSV download URL
        csv_url = find_arkk_csv_url(page)
        if not csv_url:
            # Fallback to the known stable direct URL
            csv_url = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv"
            print("Could not locate link on page — using known direct URL as fallback.")

        print(f"\nFound ARKK Fund Holdings CSV URL:\n  {csv_url}\n")

        # Download
        try:
            content = download_csv(page, csv_url)
        except Exception as e:
            print(f"Download via Playwright failed ({e}), trying direct requests fallback...")
            # Simple direct download fallback (no browser cookies needed for the assets CDN)
            import requests

            r = requests.get(csv_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            content = r.content

        saved_path = save_holdings_csv(content)

        print("\n=== Done ===")
        print(f"Latest ARKK holdings saved to: {saved_path}")

        browser.close()


if __name__ == "__main__":
    # Set headless=False if you want to watch it run
    main(headless=True)
