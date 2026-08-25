"""
01_equity_prices.py — Part 1: equity price pipeline (context/control data).

WHY WE BOTHER WITH INDIVIDUAL STOCKS AT ALL, WHEN THE TARGET IS OPTIONS:
The end goal of this project is an options trading strategy on index
options (NIFTY/BANKNIFTY — see 02_index_prices.py and 03_options_chain.py).
So why download 15 individual stocks?

  1. Single-stock options exist and trade on names like RELIANCE, TCS,
     HDFCBANK etc. An earnings surprise in one of these can move its own
     options chain sharply and fast — useful raw material for the
     "how fast does information get absorbed" question even outside the
     index-options focus.
  2. These same stocks are the heaviest-weighted constituents of NIFTY 50
     and BANKNIFTY. A big move in RELIANCE or HDFCBANK mechanically drags
     the index (and therefore index options) with it. Phase 1's event
     studies will want to distinguish "the index moved because of a
     macro event" from "the index moved because one heavyweight stock
     had an earnings surprise" — you need the stock-level data to make
     that distinction.
  3. It's a supporting/control layer: comparing how fast a single stock's
     price reacts to news versus how fast the broader index reacts is
     itself a useful diffusion-speed signal for later phases.

USAGE:
    python 01_equity_prices.py                        # full history, all configured tickers
    python 01_equity_prices.py --start 2020-01-01      # override start date
    python 01_equity_prices.py --end 2024-01-01        # override end date
    python 01_equity_prices.py --incremental           # only fetch new days since last stored date per ticker
    python 01_equity_prices.py --tickers RELIANCE.NS,TCS.NS   # limit to specific tickers (comma-separated)
"""

import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import config
import db_utils


def fetch_equity_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    Download daily OHLCV history for one ticker from Yahoo Finance.

    Returns an empty DataFrame (not an exception) if Yahoo has no data
    for this ticker/range — the caller is responsible for warning about
    that, so one bad ticker doesn't kill the whole run.
    """
    try:
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
    except Exception as exc:
        print(f"  [WARN] {ticker}: download failed with error: {exc}")
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.reset_index()
    # yfinance names the date column "Date" and gives it a timezone-aware
    # timestamp; we only care about the calendar date, stored as a plain
    # 'YYYY-MM-DD' string so it matches cleanly across tickers and is easy
    # to read straight out of the database without any parsing.
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    return df[["date", "open", "high", "low", "close", "volume"]]


def store_equity_prices(conn, ticker: str, sector: str, df: pd.DataFrame) -> int:
    """
    Insert rows into equity_prices, skipping any (ticker, date) pair we
    already have (see the UNIQUE constraint in db_utils.init_db).
    Returns the number of rows actually inserted (new rows only).
    """
    if df.empty:
        return 0

    cur = conn.cursor()
    rows = [
        (ticker, sector, r.date, r.open, r.high, r.low, r.close, int(r.volume) if pd.notna(r.volume) else None)
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        """
        INSERT OR IGNORE INTO equity_prices (ticker, sector, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def run(start: str, end: str, incremental: bool, tickers_filter=None) -> pd.DataFrame:
    conn = db_utils.get_connection()

    basket = config.EQUITY_TICKERS
    if tickers_filter:
        basket = [t for t in basket if t["ticker"] in tickers_filter]

    summary_rows = []

    for entry in basket:
        ticker, sector = entry["ticker"], entry["sector"]

        fetch_start = start
        if incremental:
            latest = db_utils.get_latest_date(conn, "equity_prices", "date", "ticker", ticker)
            if latest is not None:
                # Start the day AFTER the last date we already have.
                next_day = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
                fetch_start = next_day.strftime("%Y-%m-%d")
                if fetch_start >= end:
                    print(f"  {ticker}: already up to date (latest stored = {latest}), skipping.")
                    summary_rows.append(_summary_row(conn, ticker, sector))
                    continue
            # else: no rows yet for this ticker -> fall back to a full
            # historical pull starting at `start`.

        print(f"Fetching {ticker} ({sector}) from {fetch_start} to {end} ...")
        df = fetch_equity_history(ticker, fetch_start, end)

        if df.empty:
            print(f"  [WARN] {ticker}: no data returned for this range.")
        else:
            n_new = store_equity_prices(conn, ticker, sector, df)
            print(f"  {ticker}: {n_new} new row(s) stored.")

        summary_rows.append(_summary_row(conn, ticker, sector))

        # Be polite to Yahoo Finance's free endpoint — a short delay
        # between requests avoids tripping their rate limiting, which is
        # especially important on a shared CI runner IP (GitHub Actions).
        time.sleep(1.5)

    conn.close()

    summary_df = pd.DataFrame(summary_rows)
    return summary_df


def _summary_row(conn, ticker: str, sector: str) -> dict:
    """Build one row of the post-run sanity-check summary table."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM equity_prices WHERE ticker = ?",
        (ticker,),
    )
    count, first_date, last_date = cur.fetchone()
    return {
        "ticker": ticker,
        "sector": sector,
        "row_count": count,
        "first_date": first_date,
        "last_date": last_date,
    }


def main():
    parser = argparse.ArgumentParser(description="Download NSE equity OHLCV history into market_data.db")
    parser.add_argument("--start", default=config.DEFAULT_START_DATE, help="Start date YYYY-MM-DD (default: %(default)s)")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"), help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--incremental", action="store_true", help="Only fetch days after the latest date already stored per ticker")
    parser.add_argument("--tickers", default=None, help="Comma-separated list of tickers to limit the run to (default: full configured basket)")
    args = parser.parse_args()

    tickers_filter = None
    if args.tickers:
        tickers_filter = {t.strip() for t in args.tickers.split(",")}

    summary_df = run(args.start, args.end, args.incremental, tickers_filter)

    print("\n=== Equity price summary (sanity check) ===")
    print(summary_df.to_string(index=False))

    import os
    summary_path = os.path.join(config.DATA_DIR, "equity_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
