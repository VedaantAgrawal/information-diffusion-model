"""
PHASE 0.5: Index + Options Data Layer
=======================================
WHY THIS PHASE EXISTS:
Phase 0 built a stock-by-stock equity price pipeline. Since the end goal
is OPTIONS trading, not equity, we need two new things equity data alone
doesn't give us:

  1. INDEX price history — most liquid Indian options are on NIFTY and
     BANKNIFTY (index options), not single stocks. Single-stock options
     exist but are much thinner outside the top names.

  2. OPTIONS CHAIN data — strikes, expiries, open interest (OI), implied
     volatility (IV), and volume. This is the actual tradeable instrument
     data, and it doesn't exist in the equity pipeline at all.

IMPORTANT LIMITATION — READ THIS BEFORE RELYING ON THIS DATA:
NSE's public option chain endpoint only gives you a LIVE/CURRENT snapshot
of the option chain — there is no free source for the FULL HISTORICAL
option chain (past OI/IV at past strikes on past dates). This script sets
up a SNAPSHOT COLLECTOR: every time you run it, it saves what the option
chain looks like right now, with a timestamp. If you run this daily (or
via a scheduled task / cron job) for weeks/months, you'll build your own
historical archive going forward.

If you need option chain history from BEFORE you started collecting
(e.g. to backtest against 2020-2024), you'll need a paid data source:
  - Sensibull, Opstra Definedge — retail-friendly, have historical IV/OI
  - Broker APIs (Zerodha Kite Connect, Upstox) — mainly live/recent data
  - NSE's own historical bhavcopy archives — has some historical F&O
    data (OI, volume, settlement price) but not full intraday option
    chain granularity

A SECOND LIMITATION, CONFIRMED BY TESTING THIS SCRIPT:
NSE's website is protected by a bot-detection layer that can block
plain scripted HTTP requests outright — returning an HTTP 403 or a
"challenge" page instead of JSON — even when the request has a
realistic browser User-Agent header. This is different from ordinary
rate-limiting (too many requests too fast); it can block the very
FIRST request of a session, especially from servers/cloud machines,
and is intermittent even from a normal home connection. There is no
guaranteed fix for a free scraper. If this script reports 0 rows for
every run:
  1. Make sure you're running it from a normal home/office internet
     connection during NSE market hours (9:15 AM - 3:30 PM IST) —
     both make you look less like a bot and more likely to succeed.
  2. Try `pip install --upgrade nsepython` — this library is updated
     periodically to work around NSE's latest blocking behavior.
  3. Simply retry later. The block is not always persistent.
  4. If it never works for you, this free approach may not be reliable
     enough for daily automated collection, and you may want a paid/
     authenticated source instead (broker API, Sensibull, etc.) for
     Phase 1+.

SETUP:
    pip install -r requirements.txt

RUNNING:
    python 02_phase0_5_options_data.py
    (Run this daily while markets are open to start building your own
    historical option chain archive. See README.md for scheduling it
    automatically with Windows Task Scheduler / cron.)
"""

import argparse
import os
import time
from datetime import datetime

import pandas as pd
import yfinance as yf

try:
    from nsepython import nse_optionchain_scrapper
except ImportError:
    nse_optionchain_scrapper = None


# -----------------------------------------------------------------------
# 1. CONFIGURATION
# -----------------------------------------------------------------------

# Index price tickers (Yahoo Finance symbols)
INDEX_TICKERS = {
    "^NSEI": "NIFTY_50",
    "^NSEBANK": "BANKNIFTY",
    # FINNIFTY is not reliably available on Yahoo Finance; if you need it,
    # source it from NSE's index history downloads directly.
}

# Symbols to pull live option chains for (NSE symbol names, not Yahoo tickers)
OPTION_CHAIN_SYMBOLS = ["NIFTY", "BANKNIFTY"]

START_DATE = "2019-01-01"
END_DATE = None
INDEX_OUTPUT_DIR = "data/index_prices"
OPTIONS_OUTPUT_DIR = "data/option_chain_snapshots"

INDEX_MAX_RETRIES = 3
INDEX_RETRY_DELAY_SECONDS = 5
INDEX_DOWNLOAD_DELAY_SECONDS = 1.5

OPTION_CHAIN_MAX_ATTEMPTS = 3
OPTION_CHAIN_RETRY_DELAY_SECONDS = 10  # NSE's block often needs more than a few seconds
OPTION_CHAIN_DELAY_SECONDS = 3         # pause between NIFTY and BANKNIFTY requests


# -----------------------------------------------------------------------
# 2. INDEX PRICE HISTORY (same retry pattern as Phase 0, applied to indices)
# -----------------------------------------------------------------------

def download_one_index(ticker: str, start: str, end):
    """Download daily OHLCV for one index, with retries. Mirrors 01_data_pipeline.py."""
    last_error = None
    for attempt in range(1, INDEX_MAX_RETRIES + 1):
        try:
            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        except Exception as exc:
            last_error = str(exc)
            print(f"  ERROR on attempt {attempt}/{INDEX_MAX_RETRIES} for {ticker}: {exc}")
            if attempt < INDEX_MAX_RETRIES:
                time.sleep(INDEX_RETRY_DELAY_SECONDS)
            continue

        if df.empty:
            last_error = "empty response"
            if attempt < INDEX_MAX_RETRIES:
                print(f"  No data on attempt {attempt}/{INDEX_MAX_RETRIES} for {ticker}, retrying...")
                time.sleep(INDEX_RETRY_DELAY_SECONDS)
            continue

        # Newer yfinance versions can return MultiIndex columns even for a
        # single ticker — flatten so the CSV has plain column names.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.reset_index(), "OK"

    print(f"  WARNING: no data returned for {ticker} after {INDEX_MAX_RETRIES} attempts")
    return None, f"FAILED ({last_error})"


def download_index_history(output_dir: str, start: str, end):
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for ticker, name in INDEX_TICKERS.items():
        print(f"Downloading index history: {name} ({ticker}) ...")
        df, status = download_one_index(ticker, start, end)
        if df is not None:
            path = os.path.join(output_dir, f"{name}.csv")
            df.to_csv(path, index=False)
            print(f"  Saved {len(df)} rows -> {path}")
        results.append({"index": name, "ticker": ticker, "status": status})
        time.sleep(INDEX_DOWNLOAD_DELAY_SECONDS)
    return results


# -----------------------------------------------------------------------
# 3. LIVE OPTION CHAIN SNAPSHOT
# -----------------------------------------------------------------------

def flatten_option_chain(raw_json, symbol: str) -> pd.DataFrame:
    """
    Convert NSE's raw option chain JSON into a tidy DataFrame with one row
    per (expiry, strike, option type). Keeps the fields that matter most
    for an options-diffusion analysis: OI, change in OI, volume, and IV.

    Defensive by design: if NSE's bot-blocking kicks in (see module
    docstring), `raw_json` can come back as `{}` or with a different shape
    than expected. Every lookup below uses .get() with a default so a
    blocked/malformed response produces an empty DataFrame instead of a
    crash — the caller decides what to report to the user.
    """
    if not isinstance(raw_json, dict):
        return pd.DataFrame()

    rows = []
    records = raw_json.get("records", {}) or {}
    snapshot_time = records.get("timestamp", datetime.now().isoformat())
    underlying_value = records.get("underlyingValue")

    for item in records.get("data", []) or []:
        expiry = item.get("expiryDate")
        strike = item.get("strikePrice")
        for opt_type in ("CE", "PE"):
            leg = item.get(opt_type)
            if not leg:
                continue
            rows.append({
                "symbol": symbol,
                "snapshot_time": snapshot_time,
                "underlying_value": underlying_value,
                "expiry": expiry,
                "strike": strike,
                "option_type": opt_type,
                "open_interest": leg.get("openInterest"),
                "change_in_oi": leg.get("changeinOpenInterest"),
                "volume": leg.get("totalTradedVolume"),
                "implied_volatility": leg.get("impliedVolatility"),
                "last_price": leg.get("lastPrice"),
                "bid_price": leg.get("bidprice"),
                "ask_price": leg.get("askPrice"),
            })
    return pd.DataFrame(rows)


def fetch_option_chain_with_retries(symbol: str):
    """
    Try fetching + parsing the option chain up to OPTION_CHAIN_MAX_ATTEMPTS
    times. Returns (dataframe_or_None, status_string). A "successful"
    HTTP call that comes back blocked/empty is retried just like an
    exception, since that's the more common failure mode in practice
    (see module docstring).
    """
    for attempt in range(1, OPTION_CHAIN_MAX_ATTEMPTS + 1):
        try:
            raw = nse_optionchain_scrapper(symbol)
        except Exception as exc:
            print(f"  ERROR fetching {symbol} (attempt {attempt}/{OPTION_CHAIN_MAX_ATTEMPTS}): {exc}")
            if attempt < OPTION_CHAIN_MAX_ATTEMPTS:
                time.sleep(OPTION_CHAIN_RETRY_DELAY_SECONDS)
            continue

        df = flatten_option_chain(raw, symbol)
        if not df.empty:
            return df, "OK"

        print(f"  No option chain data on attempt {attempt}/{OPTION_CHAIN_MAX_ATTEMPTS} for {symbol} "
              f"(likely NSE bot-blocking - see module docstring for troubleshooting)")
        if attempt < OPTION_CHAIN_MAX_ATTEMPTS:
            time.sleep(OPTION_CHAIN_RETRY_DELAY_SECONDS)

    return None, "FAILED (no data after retries - see module docstring troubleshooting section)"


def collect_option_chain_snapshots(output_dir: str):
    if nse_optionchain_scrapper is None:
        print("nsepython is not installed. Run: pip install nsepython")
        print("Skipping option chain collection for now.")
        return []

    os.makedirs(output_dir, exist_ok=True)
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    results = []
    for symbol in OPTION_CHAIN_SYMBOLS:
        print(f"Fetching live option chain: {symbol} ...")
        df, status = fetch_option_chain_with_retries(symbol)

        if df is not None:
            path = os.path.join(output_dir, f"{symbol}_{run_timestamp}.csv")
            df.to_csv(path, index=False)
            print(f"  Saved {len(df)} rows ({df['expiry'].nunique()} expiries, "
                  f"{df['strike'].nunique()} strikes) -> {path}")
        else:
            print(f"  WARNING: giving up on {symbol} for this run - {status}")

        results.append({"symbol": symbol, "status": status})
        time.sleep(OPTION_CHAIN_DELAY_SECONDS)  # NSE rate-limits aggressively; be conservative

    return results


# -----------------------------------------------------------------------
# 4. MAIN
# -----------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Phase 0.5: index history + live option chain snapshots.")
    parser.add_argument("--start", default=START_DATE, help=f"Index history start date YYYY-MM-DD (default: {START_DATE})")
    parser.add_argument("--end", default=END_DATE, help="Index history end date YYYY-MM-DD (default: today)")
    parser.add_argument("--index-output-dir", default=INDEX_OUTPUT_DIR, help=f"Index CSV output folder (default: {INDEX_OUTPUT_DIR})")
    parser.add_argument("--options-output-dir", default=OPTIONS_OUTPUT_DIR, help=f"Option chain snapshot output folder (default: {OPTIONS_OUTPUT_DIR})")
    parser.add_argument("--skip-options", action="store_true", help="Only run the index history download (useful for testing outside market hours).")
    return parser.parse_args()


def main():
    args = parse_args()
    print("=== Phase 0.5: Index + Options Data Layer ===\n")

    index_results = download_index_history(args.index_output_dir, args.start, args.end)

    print()
    if args.skip_options:
        print("--skip-options set: skipping option chain collection.")
        option_results = []
    else:
        option_results = collect_option_chain_snapshots(args.options_output_dir)

    print("\n=== RUN SUMMARY ===")
    for r in index_results:
        print(f"  [index]  {r['index']:<12} {r['status']}")
    for r in option_results:
        print(f"  [chain]  {r['symbol']:<12} {r['status']}")

    print("\nDone. Run this script again on future trading days to keep "
          "building your own historical option chain archive.")


if __name__ == "__main__":
    main()
