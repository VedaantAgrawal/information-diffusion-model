"""
config.py — single source of truth for tickers, dates, and file paths.

WHY A SEPARATE CONFIG FILE:
Every script in this project (equity, index, options) needs to agree on
things like "where is the database file" and "which stocks are we
tracking". Instead of copy-pasting those constants into three different
scripts (and inevitably letting them drift out of sync), we define them
once here and `import config` everywhere else. If you want to add a
16th stock to the basket, or move the database, this is the only file
you should need to touch.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# os.path.dirname(__file__) = the folder this config.py file lives in, i.e.
# the project root. We build every other path off of that, rather than a
# hardcoded absolute path, so the project works no matter where it's
# checked out on disk (your laptop, a GitHub Actions runner, etc).
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# The single SQLite database that backs the entire Phase 0 data
# foundation. Equity prices, index prices, and option chain snapshots
# all live in here as separate tables. See db_utils.py for the schema.
DB_PATH = os.path.join(DATA_DIR, "market_data.db")

# ---------------------------------------------------------------------------
# Part 1 — Equity basket (context/control data, NOT the options target)
# ---------------------------------------------------------------------------
# ~15 Nifty 50 large caps, deliberately spread across sectors. The point
# of this diversity is that "how fast does information get absorbed"
# probably isn't the same answer for a banking stock reacting to an RBI
# rate decision as it is for an IT stock reacting to a US Fed signal or
# a global tech selloff. Having multiple sectors lets later phases
# compare diffusion speed *across* sectors, not just within one.
#
# Each entry is a dict of {ticker, sector} because both fields get
# written to the equity_prices table — the ticker to identify the row,
# the sector so later analysis can group/filter without needing a
# separate lookup table.
EQUITY_TICKERS = [
    {"ticker": "RELIANCE.NS", "sector": "Energy"},
    {"ticker": "ONGC.NS", "sector": "Energy"},
    {"ticker": "TCS.NS", "sector": "IT"},
    {"ticker": "INFY.NS", "sector": "IT"},
    {"ticker": "HDFCBANK.NS", "sector": "Banking"},
    {"ticker": "ICICIBANK.NS", "sector": "Banking"},
    {"ticker": "SBIN.NS", "sector": "Banking"},
    {"ticker": "HINDUNILVR.NS", "sector": "FMCG"},
    {"ticker": "ITC.NS", "sector": "FMCG"},
    {"ticker": "MARUTI.NS", "sector": "Auto"},
    # NOTE: TATAMOTORS.NS was originally in this basket, but Tata Motors'
    # 2025 demerger (into separate commercial-vehicle and passenger-
    # vehicle listings) means the old combined ticker now 404s on Yahoo
    # Finance. M&M.NS (Mahindra & Mahindra) is used instead. This is a
    # good example of why 01_equity_prices.py warns loudly instead of
    # silently skipping a ticker with no data — corporate actions
    # (demergers, delistings, ticker renames) happen, and you want to
    # notice, not silently lose a stock from your basket.
    {"ticker": "M&M.NS", "sector": "Auto"},
    {"ticker": "BHARTIARTL.NS", "sector": "Telecom"},
    {"ticker": "LT.NS", "sector": "Infrastructure"},
    {"ticker": "TITAN.NS", "sector": "Consumer Durables"},
    {"ticker": "ASIANPAINT.NS", "sector": "Consumer Durables"},
]

# ---------------------------------------------------------------------------
# Part 2 — Index basket (the actual options underlyings)
# ---------------------------------------------------------------------------
# NIFTY 50 and BANKNIFTY are the underlyings for India's two most liquid,
# most-traded index option chains. Single-stock options exist on NSE too,
# but outside the top ~20 names their volume/open-interest is thin,
# spreads are wide, and there often isn't a clean options chain to study
# diffusion speed with. Index options are where the real liquidity (and
# therefore the real trading opportunity) is — that's why they're the
# primary focus of Part 3, even though Part 1 tracks individual stocks
# too (as context/control data, see equity_prices.py for why).
#
# Yahoo Finance ticker symbols for these indices use a "^" prefix rather
# than the ".NS" suffix used for individual NSE stocks.
INDEX_TICKERS = [
    {"ticker": "^NSEI", "index_name": "NIFTY50"},
    {"ticker": "^NSEBANK", "index_name": "BANKNIFTY"},
]

# ---------------------------------------------------------------------------
# Part 3 — Options chain symbols (NSE's own naming, not Yahoo's)
# ---------------------------------------------------------------------------
# nsepython talks to NSE directly, which uses its own short symbol names
# for indices — "NIFTY" and "BANKNIFTY" — different from the Yahoo
# Finance tickers above ("^NSEI" / "^NSEBANK"). Keep these separate so
# it's obvious which library each list feeds.
OPTIONS_SYMBOLS = ["NIFTY", "BANKNIFTY"]

# ---------------------------------------------------------------------------
# Default date range for historical pulls (Parts 1 & 2)
# ---------------------------------------------------------------------------
# ~5+ years back. Chosen as a fixed calendar date (not "5 years before
# today") so that re-running a full historical pull is reproducible —
# it always starts from the same point unless you explicitly override it
# with --start on the command line.
DEFAULT_START_DATE = "2019-01-01"
