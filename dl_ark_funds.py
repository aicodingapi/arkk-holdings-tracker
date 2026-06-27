#!/usr/bin/env python3
"""
Download ARK ETF Fund Holdings CSV files directly from the ARK assets CDN.

Each fund has a stable direct URL under:
  https://assets.ark-funds.com/fund-documents/funds-etf-csv/

Skip logic (per fund):
- Before downloading, checks whether data_ark/<ticker>_YYYYMMDD.csv already exists.
- YYYYMMDD is today's UTC date on weekdays; on UTC Saturday/Sunday it uses the
  most recent prior Friday (ARK holdings CSVs only update Mon-Fri).
- If that file already exists, the fund is skipped.

Usage:
  python dl_ark_funds.py              # download ARKK (default)
  python dl_ark_funds.py ARKG        # download a specific fund
  python dl_ark_funds.py --all       # download all configured funds
  python dl_ark_funds.py --list      # list available funds
"""

from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


CDN_BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv"
OUTPUT_DIR = Path("data_ark")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class FundConfig:
    ticker: str
    filename: str

    @property
    def url(self) -> str:
        return f"{CDN_BASE}/{self.filename}"

    def output_path(self, date_str: str) -> Path:
        return OUTPUT_DIR / f"{self.ticker.lower()}_{date_str}.csv"

    def today_file(self, today_str: str) -> Path:
        return self.output_path(today_str)


FUNDS: dict[str, FundConfig] = {
    "ARKB": FundConfig("ARKB", "ARK_21SHARES_BITCOIN_ETF_ARKB_HOLDINGS.csv"),
    "ARKF": FundConfig("ARKF", "ARK_BLOCKCHAIN_&_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv"),
    "ARKG": FundConfig("ARKG", "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv"),
    "ARKK": FundConfig("ARKK", "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv"),
    "ARKQ": FundConfig("ARKQ", "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv"),
    "ARKW": FundConfig("ARKW", "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv"),
    "ARKX": FundConfig("ARKX", "ARK_SPACE_&_DEFENSE_INNOVATION_ETF_ARKX_HOLDINGS.csv"),
    "IZRL": FundConfig("IZRL", "ARK_ISRAEL_INNOVATIVE_TECHNOLOGY_ETF_IZRL_HOLDINGS.csv"),
    "PRNT": FundConfig("PRNT", "THE_3D_PRINTING_ETF_PRNT_HOLDINGS.csv"),
}


def utc_now() -> datetime:
    """Return the current UTC datetime (matches GitHub Actions runners)."""
    return datetime.now(timezone.utc)


def utc_today_str() -> str:
    """Return today's date as YYYYMMDD in UTC."""
    return utc_now().strftime("%Y%m%d")


def skip_check_date_str() -> str:
    """
    Return the UTC date to use for the skip-exists check.

    ARK holdings CSVs update on US market days (Mon-Fri). On UTC weekends,
    use the most recent prior Friday so we don't re-download Friday's data.
    """
    now = utc_now()
    weekday = now.weekday()  # Mon=0 ... Fri=4, Sat=5, Sun=6
    if weekday == 5:
        check_date = now - timedelta(days=1)
    elif weekday == 6:
        check_date = now - timedelta(days=2)
    else:
        check_date = now
    return check_date.strftime("%Y%m%d")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def download_csv(url: str) -> bytes:
    """Download the CSV content via HTTP GET."""
    print(f"Downloading from:\n  {url}")
    response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    content = response.content

    head = content[:200].decode("utf-8", errors="replace").lower()
    if "pdf" in head or head.startswith("%pdf") or "<html" in head:
        raise RuntimeError("Downloaded content does not look like CSV (got PDF or HTML instead)")

    if not content.lstrip().startswith(b"date,fund,company"):
        print("Warning: CSV header did not match expected 'date,fund,company...'")

    return content


def _parse_ark_date(raw: str) -> str | None:
    """Try to parse a raw date string from the ARK CSV into YYYYMMDD format."""
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


def save_holdings_csv(content: bytes, fund: FundConfig) -> Path:
    """
    Save the CSV bytes to data_ark/<ticker>_yyyymmdd.csv.
    Uses the date from the first data row; falls back to today's UTC date.
    """
    ensure_output_dir(OUTPUT_DIR)

    text = content.decode("utf-8", errors="replace")

    date_str = None
    try:
        reader = csv.reader(io.StringIO(text))
        next(reader, None)
        first_row = next(reader, None)
        if first_row and len(first_row) > 0:
            raw_date = first_row[0]
            date_str = _parse_ark_date(raw_date)
            if date_str:
                print(f"Using date from downloaded file: {raw_date} → {date_str}")
    except Exception as e:
        print(f"Warning: Error while reading date from CSV ({e})")

    if not date_str:
        date_str = utc_today_str()
        print(f"Warning: Could not get date from CSV data. Falling back to today's UTC date: {date_str}")

    out_path = fund.output_path(date_str)

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


def download_fund(fund: FundConfig, check_date_str: str) -> Path | None:
    """Download one fund if the skip-check date file is not already present."""
    check_file = fund.today_file(check_date_str)

    if check_file.exists():
        print(f"[{fund.ticker}] File already exists for check date: {check_file}")
        print(f"[{fund.ticker}] Skipping.")
        return None

    print(
        f"[{fund.ticker}] No file for check date ({check_date_str}) found. "
        "Proceeding to download..."
    )
    content = download_csv(fund.url)
    return save_holdings_csv(content, fund)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download ARK ETF holdings CSV files from the ARK assets CDN."
    )
    parser.add_argument(
        "fund",
        nargs="?",
        default="ARKK",
        help="Fund ticker to download (default: ARKK)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download all configured funds",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List configured funds and exit",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list:
        print("Configured ARK funds:")
        for ticker, fund in sorted(FUNDS.items()):
            print(f"  {ticker:5}  {fund.url}")
        return

    if args.all:
        targets = list(FUNDS.values())
    else:
        ticker = args.fund.upper()
        if ticker not in FUNDS:
            available = ", ".join(sorted(FUNDS))
            raise SystemExit(f"Unknown fund: {ticker}. Available: {available}")
        targets = [FUNDS[ticker]]

    print("=== ARK Fund Holdings Downloader ===")
    check_date_str = skip_check_date_str()
    if utc_now().weekday() >= 5:
        print(
            f"UTC weekend: using prior Friday ({check_date_str}) for skip check"
        )
    else:
        print(f"Skip check date (UTC): {check_date_str}")

    saved: list[Path] = []
    failed: list[str] = []
    for fund in targets:
        print(f"\n--- {fund.ticker} ---")
        try:
            path = download_fund(fund, check_date_str)
        except Exception as e:
            print(f"[{fund.ticker}] Download failed: {e}")
            failed.append(fund.ticker)
            continue
        if path is not None:
            saved.append(path)

    print("\n=== Done ===")
    if saved:
        for path in saved:
            print(f"  Saved: {path}")
    else:
        print("  No new files downloaded.")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()