"""
PHASE 0: Data Pipeline
=======================
Goal: Download historical daily price data for a Nifty 50 subset + the
Nifty 50 index itself (as the market benchmark), and save it locally as
clean CSV files that later phases (event study, pattern mining) will use.

WHY THE BENCHMARK MATTERS:
If Reliance goes up 2% on the day of its earnings call, was that BECAUSE
of the earnings, or did the whole market go up 2% that day for unrelated
reasons (e.g. good global cues, RBI news)? We can't tell unless we compare
the stock's move to the market's move on the same day. That's why we
always download the Nifty 50 index alongside every stock — it's the
"control group" for every event study we do later. In Phase 1 we'll
compute "abnormal return" = stock's daily return - index's daily return,
which isolates the part of the move that's specific to that company.

HOW TO RUN THIS ON YOUR OWN COMPUTER:
1. Install Python 3.9+ if you don't have it: https://www.python.org/downloads/
2. Open a terminal and install the required packages:
       pip install -r requirements.txt
3. Run this script:
       python 01_data_pipeline.py
4. It will create a folder called "data/" with one CSV per stock, plus
   nifty50_index.csv for the benchmark.

Optional: override the date range without editing the file, e.g.
       python 01_data_pipeline.py --start 2015-01-01 --end 2024-12-31

NOTE ON DATA SOURCE (yfinance / Yahoo Finance):
- Free, no API key needed, good for DAILY data going back years.
- NSE-listed stocks use the ".NS" suffix on Yahoo (e.g. "RELIANCE.NS").
- Limitation: Yahoo's intraday data for Indian stocks is patchy and only
  covers the last ~60 days. For the "how fast in minutes" question later,
  we'll need a different source (Phase 2 will cover NSE bhavcopy / broker
  APIs like Zerodha Kite Connect, which give proper intraday history).
  Daily data is enough to get started and prove out the whole pipeline.
- Yahoo occasionally rate-limits or transiently fails requests. This
  script retries each ticker a few times with a short backoff before
  giving up on it, and always records what happened in the summary table
  below — so you never have to guess whether a missing ticker was a
  network blip or a real problem.
"""

import argparse
import os
import time

import pandas as pd
import yfinance as yf

# -----------------------------------------------------------------------
# 1. CONFIGURATION — edit this dict to change which stocks you track
# -----------------------------------------------------------------------

# A starter basket of 15 liquid Nifty 50 large caps across sectors.
# Sector variety matters later when you ask "do banks react faster than
# FMCG companies?" etc.
NIFTY_SUBSET = {
    "RELIANCE.NS": "Energy/Conglomerate",
    "TCS.NS": "IT",
    "INFY.NS": "IT",
    "HDFCBANK.NS": "Banking",
    "ICICIBANK.NS": "Banking",
    "KOTAKBANK.NS": "Banking",
    "AXISBANK.NS": "Banking",
    "SBIN.NS": "Banking",
    "HINDUNILVR.NS": "FMCG",
    "ITC.NS": "FMCG",
    "BHARTIARTL.NS": "Telecom",
    "LT.NS": "Infrastructure",
    "ASIANPAINT.NS": "Consumer Durables",
    "MARUTI.NS": "Auto",
    "TITAN.NS": "Consumer Durables",
}

BENCHMARK_TICKER = "^NSEI"   # Nifty 50 index on Yahoo Finance
BENCHMARK_NAME = "nifty50_index"
START_DATE = "2019-01-01"    # ~5+ years of history, covers multiple market regimes (COVID crash, 2022 rate hikes, etc.)
END_DATE = None              # None = up to today
OUTPUT_DIR = "data"

DOWNLOAD_DELAY_SECONDS = 1.5  # pause between tickers, be polite to Yahoo's API
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5       # backoff before retrying a failed/empty download


# -----------------------------------------------------------------------
# 2. DOWNLOAD FUNCTIONS
# -----------------------------------------------------------------------

def download_one_ticker(ticker: str, start: str, end):
    """
    Download daily OHLCV data for a single ticker.

    Retries a few times on transient errors or empty responses (Yahoo
    sometimes drops a request under load). Returns (dataframe, status)
    where dataframe is None if every attempt failed, and status is a
    short string recorded in the summary table so failures are visible
    rather than silently skipped.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        except Exception as exc:  # network errors, rate limiting, etc.
            last_error = str(exc)
            print(f"  ERROR on attempt {attempt}/{MAX_RETRIES} for {ticker}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        if df.empty:
            last_error = "empty response"
            if attempt < MAX_RETRIES:
                print(f"  No data on attempt {attempt}/{MAX_RETRIES} for {ticker}, retrying...")
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        # Newer yfinance versions sometimes return MultiIndex columns
        # (e.g. ("Close", "RELIANCE.NS")) even for a single ticker.
        # Flatten to plain column names so downstream code is simple.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df["Ticker"] = ticker
        return df, "OK"

    print(f"  WARNING: no data returned for {ticker} after {MAX_RETRIES} attempts")
    return None, f"FAILED ({last_error})"


def save_and_record(df, status, name: str, sector: str, summary: list):
    """Save a downloaded dataframe to CSV and append its outcome to the summary list."""
    if df is not None:
        path = os.path.join(OUTPUT_DIR, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"  Saved {len(df)} rows -> {path}")
        summary.append({
            "ticker": name,
            "sector": sector,
            "status": status,
            "rows": len(df),
            "first_date": df["Date"].min(),
            "last_date": df["Date"].max(),
        })
    else:
        summary.append({
            "ticker": name,
            "sector": sector,
            "status": status,
            "rows": 0,
            "first_date": None,
            "last_date": None,
        })


def parse_args():
    parser = argparse.ArgumentParser(description="Phase 0: download Nifty 50 subset + benchmark OHLCV data.")
    parser.add_argument("--start", default=START_DATE, help=f"Start date YYYY-MM-DD (default: {START_DATE})")
    parser.add_argument("--end", default=END_DATE, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help=f"Output folder (default: {OUTPUT_DIR})")
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    global OUTPUT_DIR
    OUTPUT_DIR = output_dir

    summary = []

    # --- Download the benchmark index first ---
    print(f"Downloading benchmark: {BENCHMARK_TICKER} ...")
    bench_df, bench_status = download_one_ticker(BENCHMARK_TICKER, args.start, args.end)
    save_and_record(bench_df, bench_status, BENCHMARK_NAME, "Benchmark (Nifty 50 Index)", summary)
    time.sleep(DOWNLOAD_DELAY_SECONDS)

    # --- Download each stock ---
    for ticker, sector in NIFTY_SUBSET.items():
        print(f"Downloading {ticker} ({sector}) ...")
        df, status = download_one_ticker(ticker, args.start, args.end)
        save_and_record(df, status, ticker.replace(".", "_"), sector, summary)
        time.sleep(DOWNLOAD_DELAY_SECONDS)

    # --- Print and save a summary table so you can sanity-check the download ---
    summary_df = pd.DataFrame(summary)
    print("\n=== DOWNLOAD SUMMARY ===")
    print(summary_df.to_string(index=False))
    summary_df.to_csv(os.path.join(OUTPUT_DIR, "_download_summary.csv"), index=False)

    failed = summary_df[summary_df["status"] != "OK"]
    if not failed.empty:
        print(f"\n{len(failed)} ticker(s) failed — see status column above and retry if needed:")
        print(failed[["ticker", "status"]].to_string(index=False))
    else:
        print("\nAll tickers downloaded successfully.")


if __name__ == "__main__":
    main()
