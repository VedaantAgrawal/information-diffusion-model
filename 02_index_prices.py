"""
02_index_prices.py — Part 2: index price pipeline (the actual underlyings for options).

WHY THIS IS THE MOST IMPORTANT DAILY DATA IN THE PROJECT:
NIFTY 50 and BANKNIFTY are the underlyings for India's two most liquid
index option chains — the instruments this whole project is ultimately
trying to build a trading strategy around (see 03_options_chain.py).
Single-stock options exist (see 01_equity_prices.py) but outside the top
handful of names their volume and open interest are thin and spreads are
wide, which makes them a poor fit for a strategy built on precisely
timing information absorption. Index options are where the liquidity —
and therefore the tradeable edge — actually is.

The daily OHLCV history captured here is what Phase 1's event studies
will measure "diffusion speed" against: e.g. "after an RBI policy
announcement, how many minutes/hours/days did it take for BANKNIFTY's
price to fully reflect the news?".

USAGE:
    python 02_index_prices.py                    # full history, both indices
    python 02_index_prices.py --start 2020-01-01
    python 02_index_prices.py --end 2024-01-01
    python 02_index_prices.py --incremental       # only fetch new days since last stored date per index
"""

import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import config
import db_utils


def fetch_index_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    Download daily OHLCV history for one index from Yahoo Finance.
    Mirrors fetch_equity_history in 01_equity_prices.py — kept as a
    separate function (rather than a shared import) because indices and
    stocks are conceptually different data sources even though the
    Yahoo Finance call looks the same today.
    """
    try:
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
    except Exception as exc:
        print(f"  [WARN] {ticker}: download failed with error: {exc}")
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.reset_index()
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    return df[["date", "open", "high", "low", "close", "volume"]]


def store_index_prices(conn, index_name: str, df: pd.DataFrame) -> int:
    """Insert rows into index_prices, skipping dates we already have."""
    if df.empty:
        return 0

    cur = conn.cursor()
    rows = [
        (index_name, r.date, r.open, r.high, r.low, r.close, int(r.volume) if pd.notna(r.volume) else None)
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        """
        INSERT OR IGNORE INTO index_prices (index_name, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def run(start: str, end: str, incremental: bool) -> pd.DataFrame:
    conn = db_utils.get_connection()

    summary_rows = []

    for entry in config.INDEX_TICKERS:
        ticker, index_name = entry["ticker"], entry["index_name"]

        fetch_start = start
        if incremental:
            latest = db_utils.get_latest_date(conn, "index_prices", "date", "index_name", index_name)
            if latest is not None:
                next_day = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
                fetch_start = next_day.strftime("%Y-%m-%d")
                if fetch_start >= end:
                    print(f"  {index_name}: already up to date (latest stored = {latest}), skipping.")
                    summary_rows.append(_summary_row(conn, index_name))
                    continue

        print(f"Fetching {index_name} ({ticker}) from {fetch_start} to {end} ...")
        df = fetch_index_history(ticker, fetch_start, end)

        if df.empty:
            print(f"  [WARN] {index_name}: no data returned for this range.")
        else:
            n_new = store_index_prices(conn, index_name, df)
            print(f"  {index_name}: {n_new} new row(s) stored.")

        summary_rows.append(_summary_row(conn, index_name))

        time.sleep(1.5)

    conn.close()
    return pd.DataFrame(summary_rows)


def _summary_row(conn, index_name: str) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM index_prices WHERE index_name = ?",
        (index_name,),
    )
    count, first_date, last_date = cur.fetchone()
    return {
        "index_name": index_name,
        "row_count": count,
        "first_date": first_date,
        "last_date": last_date,
    }


def main():
    parser = argparse.ArgumentParser(description="Download NIFTY50/BANKNIFTY OHLCV history into market_data.db")
    parser.add_argument("--start", default=config.DEFAULT_START_DATE, help="Start date YYYY-MM-DD (default: %(default)s)")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"), help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--incremental", action="store_true", help="Only fetch days after the latest date already stored per index")
    args = parser.parse_args()

    summary_df = run(args.start, args.end, args.incremental)

    print("\n=== Index price summary (sanity check) ===")
    print(summary_df.to_string(index=False))

    import os
    summary_path = os.path.join(config.DATA_DIR, "index_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
