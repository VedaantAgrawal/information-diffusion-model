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
"control group" for every event study we do later.

HOW TO RUN THIS ON YOUR OWN COMPUTER:
1. Install Python 3.9+ if you don't have it: https://www.python.org/downloads/
2. Open a terminal and install the required packages:
       pip install yfinance pandas
3. Run this script:
       python 01_data_pipeline.py
4. It will create a folder called "data/" with one CSV per stock, plus
   nifty50_index.csv for the benchmark.

NOTE ON DATA SOURCE (yfinance / Yahoo Finance):
- Free, no API key needed, good for DAILY data going back years.
- NSE-listed stocks use the ".NS" suffix on Yahoo (e.g. "RELIANCE.NS").
- Limitation: Yahoo's intraday data for Indian stocks is patchy and only
  covers the last ~60 days. For the "how fast in minutes" question later,
  we'll need a different source (Phase 2 will cover NSE bhavcopy / broker
  APIs like Zerodha Kite Connect, which give proper intraday history).
  Daily data is enough to get started and prove out the whole pipeline.
"""

import os
import time
import pandas as pd
import yfinance as yf

# -----------------------------------------------------------------------
# 1. CONFIGURATION — edit this list to change which stocks you track
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
START_DATE = "2019-01-01"    # ~5+ years of history, covers multiple market regimes (COVID crash, 2022 rate hikes, etc.)
END_DATE = None              # None = up to today
OUTPUT_DIR = "data"


# -----------------------------------------------------------------------
# 2. DOWNLOAD FUNCTIONS
# -----------------------------------------------------------------------

def download_one_ticker(ticker: str, start: str, end):
    """Download daily OHLCV data for a single ticker and return a DataFrame."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        print(f"  WARNING: no data returned for {ticker}")
        return None
    df = df.reset_index()
    df["Ticker"] = ticker
    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- Download the benchmark index first ---
    print(f"Downloading benchmark: {BENCHMARK_TICKER} ...")
    bench_df = download_one_ticker(BENCHMARK_TICKER, START_DATE, END_DATE)
    if bench_df is not None:
        bench_path = os.path.join(OUTPUT_DIR, "nifty50_index.csv")
        bench_df.to_csv(bench_path, index=False)
        print(f"  Saved {len(bench_df)} rows -> {bench_path}")
    time.sleep(1)  # be polite to the API, avoid rate limiting

    # --- Download each stock ---
    summary = []
    for ticker, sector in NIFTY_SUBSET.items():
        print(f"Downloading {ticker} ({sector}) ...")
        df = download_one_ticker(ticker, START_DATE, END_DATE)
        if df is not None:
            path = os.path.join(OUTPUT_DIR, f"{ticker.replace('.', '_')}.csv")
            df.to_csv(path, index=False)
            summary.append({"ticker": ticker, "sector": sector, "rows": len(df),
                             "first_date": df["Date"].min(), "last_date": df["Date"].max()})
            print(f"  Saved {len(df)} rows -> {path}")
        time.sleep(1)  # avoid hammering the API

    # --- Print a summary table so you can sanity-check the download ---
    if summary:
        summary_df = pd.DataFrame(summary)
        print("\n=== DOWNLOAD SUMMARY ===")
        print(summary_df.to_string(index=False))
        summary_df.to_csv(os.path.join(OUTPUT_DIR, "_download_summary.csv"), index=False)
    else:
        print("No data was downloaded. Check your internet connection or ticker symbols.")


if __name__ == "__main__":
    main()
