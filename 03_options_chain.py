"""
03_options_chain.py — Part 3: options chain snapshot collector.

*** READ THIS BEFORE YOU RUN IT ***

NSE's free option chain endpoint (which the `nsepython` library scrapes)
only ever returns the CURRENT, LIVE option chain — there is no free API
or download that gives you *historical* option chains for a past date.
This is a hard limitation of the free data source, not a bug in this
script.

That means this script CANNOT backfill the past. If you run it for the
first time today, your options history starts today — there is no way
to get, say, last year's option chain for NIFTY for free. If your
project needs real historical option chain data (e.g. to backtest a
strategy over past years), the two realistic options are:

  1. A paid historical options data vendor — e.g. Sensibull, Opstra
     Definedge.
  2. A broker API with historical data entitlements — e.g. Zerodha Kite
     Connect or Upstox, which typically require an active trading
     account with that broker.

Given that constraint, this script is deliberately designed to be run
FREQUENTLY and repeatedly (every ~30 minutes during market hours — see
.github/workflows/collect_options_data.yml) so that, going forward, we
build up our OWN intraday-resolution history snapshot by snapshot. This
is a much higher frequency than the once-a-day equity/index pipelines
(01_equity_prices.py / 02_index_prices.py) because the whole point of
this project is measuring how FAST information gets absorbed — you
can't measure "fast" with once-a-day data. The options side is where
that fine-grained timing actually lives, so it gets the fine-grained
collection schedule.

Every run appends new rows to option_chain_snapshots — it never
overwrites or deletes previous snapshots (see db_utils.py for why this
table has no dedup/UNIQUE constraint, unlike the daily price tables).

USAGE:
    python 03_options_chain.py
"""

import time
from datetime import datetime, timezone

import pandas as pd
from nsepython import nse_optionchain_scrapper

import config
import db_utils


def fetch_raw_chain(symbol: str) -> dict:
    """
    Pull the live option chain JSON for one symbol (e.g. "NIFTY") from
    NSE via nsepython. Raises on failure — the caller decides how to
    handle that (log + skip, so one symbol's failure doesn't kill the
    whole run).
    """
    return nse_optionchain_scrapper(symbol)


def flatten_chain(symbol: str, raw: dict, pulled_at_utc: str) -> pd.DataFrame:
    """
    Flatten NSE's nested option chain JSON into one row per
    (expiry, strike, option_type).

    NSE's response shape (as of writing) is roughly:
        {
          "records": {
            "timestamp": "25-Aug-2025 15:30:01",
            "underlyingValue": 24500.0,
            "data": [
              {"strikePrice": 24000, "expiryDate": "28-Aug-2025",
               "CE": {...fields...}, "PE": {...fields...}},
              ...
            ]
          }
        }

    Each strike/expiry entry can have a "CE" (call) sub-dict, a "PE"
    (put) sub-dict, or both — we emit a separate row for whichever of
    the two are present. We use dict.get(...) everywhere rather than
    direct indexing because NSE's fields are not 100% consistent across
    every strike (e.g. deep out-of-the-money strikes sometimes lack an
    implied volatility figure), and a single missing field should not
    crash the entire snapshot.
    """
    records = raw.get("records", {})
    snapshot_time = records.get("timestamp")
    underlying_value = records.get("underlyingValue")
    rows = []

    for entry in records.get("data", []):
        strike = entry.get("strikePrice")
        expiry = entry.get("expiryDate")

        for option_type in ("CE", "PE"):
            leg = entry.get(option_type)
            if leg is None:
                continue  # this strike/expiry has no call (or no put) quoted

            rows.append({
                "symbol": symbol,
                "snapshot_time": snapshot_time,
                "pulled_at_utc": pulled_at_utc,
                "underlying_value": underlying_value,
                "expiry": expiry,
                "strike": strike,
                "option_type": option_type,
                "open_interest": leg.get("openInterest"),
                "change_in_oi": leg.get("changeinOpenInterest"),
                "volume": leg.get("totalTradedVolume"),
                "implied_volatility": leg.get("impliedVolatility"),
                "last_price": leg.get("lastPrice"),
                # NSE's own JSON is inconsistent about capitalization:
                # bid price comes back as "bidprice" (lowercase p) while
                # ask price comes back as "askPrice" (uppercase P). This
                # is NSE's inconsistency, not a typo here.
                "bid_price": leg.get("bidprice"),
                "ask_price": leg.get("askPrice"),
            })

    return pd.DataFrame(rows)


def store_snapshot(conn, df: pd.DataFrame) -> int:
    """Append snapshot rows to option_chain_snapshots. Returns rows written."""
    if df.empty:
        return 0
    df.to_sql("option_chain_snapshots", conn, if_exists="append", index=False)
    return len(df)


def run() -> None:
    conn = db_utils.get_connection()

    total_written = 0
    for symbol in config.OPTIONS_SYMBOLS:
        pulled_at_utc = datetime.now(timezone.utc).isoformat()
        print(f"Fetching live option chain for {symbol} ...")

        try:
            raw = fetch_raw_chain(symbol)
        except Exception as exc:
            # This is the expected failure path outside market hours, or
            # when NSE rate-limits/blocks the request. We log clearly and
            # move on to the next symbol WITHOUT touching the database —
            # existing snapshots are never at risk from a failed fetch.
            print(f"  [WARN] {symbol}: fetch failed ({exc}). Skipping this symbol for this run.")
            continue

        df = flatten_chain(symbol, raw, pulled_at_utc)
        if df.empty:
            print(f"  [WARN] {symbol}: fetch succeeded but produced no rows (empty/unexpected response). Skipping.")
            continue

        n_written = store_snapshot(conn, df)
        total_written += n_written
        snapshot_time = df["snapshot_time"].iloc[0]
        print(f"  {symbol}: stored {n_written} rows for snapshot_time={snapshot_time}")

        # Small delay between symbols to avoid hammering NSE back-to-back.
        time.sleep(2)

    conn.close()
    print(f"\nDone. {total_written} total rows written this run.")


if __name__ == "__main__":
    run()
