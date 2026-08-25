"""
db_utils.py — shared SQLite schema and connection helpers.

WHY THIS EXISTS:
All three data pipelines (equity, index, options) write into the SAME
database file (data/market_data.db), each into its own table. Rather
than have each script define its own CREATE TABLE statement (and risk
them drifting apart, or one script accidentally creating the table with
slightly different column names), we define the whole schema in one
place and have every script call init_db() before it does anything else.

`CREATE TABLE IF NOT EXISTS` makes this safe to call every single run:
on a fresh checkout of the repo (no data/market_data.db yet) it creates
the tables from scratch; on every later run it's a harmless no-op.
"""

import os
import sqlite3

import config


def get_connection() -> sqlite3.Connection:
    """
    Open a connection to data/market_data.db, creating the data/ folder
    and the database file itself if they don't exist yet, and making
    sure the schema (all three tables) is in place before handing back
    the connection.

    Every script should get its connection through this function rather
    than calling sqlite3.connect() directly, so that "the schema exists"
    is guaranteed everywhere.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Create all three Phase 0 tables if they don't already exist.

    Design notes on the dedup strategy:

    - equity_prices and index_prices are DAILY data pulled from
      Yahoo Finance. Every time the daily-update workflow runs, we
      re-fetch a small overlapping window (see get_latest_date below)
      and rely on a UNIQUE constraint (ticker, date) / (index_name, date)
      plus `INSERT OR IGNORE` to silently skip rows we already have.
      This means it is always safe to re-run these scripts — you cannot
      accidentally create duplicate rows for a day you already stored.

    - option_chain_snapshots is different: it's a time series of
      *snapshots*, not a single row per day. Every run appends a new
      batch of rows stamped with that run's snapshot_time. There is no
      natural "primary key" to dedup on (running the collector twice in
      the same minute should, in theory, just give you two very similar
      but independent data points) — so this table is deliberately
      append-only with no UNIQUE constraint.
    """
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS equity_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sector TEXT,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE (ticker, date)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS index_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_name TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE (index_name, date)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            snapshot_time TEXT,
            pulled_at_utc TEXT NOT NULL,
            underlying_value REAL,
            expiry TEXT,
            strike REAL,
            option_type TEXT,
            open_interest REAL,
            change_in_oi REAL,
            volume REAL,
            implied_volatility REAL,
            last_price REAL,
            bid_price REAL,
            ask_price REAL
        )
        """
    )

    # Indexes speed up the queries later phases will run constantly:
    # "give me everything for this ticker/index/symbol, ordered by
    # date/time". Without these, SQLite has to scan the whole table.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_equity_ticker_date ON equity_prices (ticker, date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_index_name_date ON index_prices (index_name, date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_options_symbol_time ON option_chain_snapshots (symbol, snapshot_time)")

    conn.commit()


def get_latest_date(conn: sqlite3.Connection, table: str, date_col: str, filter_col: str, filter_val: str):
    """
    Return the most recent date (as a 'YYYY-MM-DD' string) already
    stored for a given ticker/index, or None if we have no rows yet.

    This is the core of the "incremental update" logic used by the daily
    price workflow (Part 4b): instead of re-downloading 5+ years of
    history every single day, we ask the database "what's the last date
    you have for RELIANCE.NS?" and only fetch from the day after that
    forward. On a brand-new database (first-ever run), this returns
    None, which the calling script treats as "do a full historical pull".

    table/date_col/filter_col are plain string interpolation here rather
    than query parameters because they're column/table NAMES (SQLite
    parameter binding only works for VALUES, not identifiers). This is
    safe because these arguments are always hardcoded by us in
    equity_prices.py / index_prices.py, never user input.
    """
    cur = conn.cursor()
    cur.execute(
        f"SELECT MAX({date_col}) FROM {table} WHERE {filter_col} = ?",
        (filter_val,),
    )
    result = cur.fetchone()[0]
    return result  # None if no rows for this filter_val yet
