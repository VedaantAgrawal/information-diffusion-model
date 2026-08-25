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

# ---------------------------------------------------------------------------
# Phase 1 — Event study engine (market-model event study + PEAD calibration)
# ---------------------------------------------------------------------------
# See PHASE_1_HANDOFF_PROMPT.md and README_phase1.md for the full framing:
# this is NOT a predictive model. It calibrates "when past earnings
# surprises were of magnitude X, how big was the historical price
# reaction Y" — an actuarial-style historical average, not a forecast.

# Where the hand-researched event list lives, and the default output
# directory for every report (per-event charts/summaries, the combined
# results CSV, and the Part 6 calibration report) — both overridable on
# the command line via --events / --output-dir (see 04_event_study.py).
# The events/ subfolder and file names within output_dir are fixed by
# 04_event_study.py itself, since they're relative to whatever
# --output-dir is passed, not to a hardcoded absolute path.
EVENTS_CSV_PATH = os.path.join(PROJECT_ROOT, "events.csv")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")

# Which row of index_prices to regress each stock's returns against when
# estimating beta (the market model needs ONE benchmark; NIFTY 50 is the
# broad-market proxy — BANKNIFTY is a sector index, not a market proxy,
# so it isn't used here even though we collect it in Phase 0).
MARKET_INDEX_NAME = "NIFTY50"

# --- Part 2: beta estimation window --------------------------------------
# ~250 trading days (~1 trading year) is the conventional estimation
# window length in the event-study literature (Brown & Warner and most
# published studies that followed them). The buffer keeps the window from
# running right up to the event: if a stock starts drifting in ANTICIPATION
# of an earnings beat/miss a few weeks early (which happens), including
# those days in the beta estimate would contaminate alpha/beta with the
# very effect we're trying to measure. 30 trading days (~6 weeks) of
# buffer is a common, if somewhat arbitrary, choice — feel free to tune it.
ESTIMATION_WINDOW_DAYS = 250
ESTIMATION_BUFFER_DAYS = 30

# --- Part 3: event windows -------------------------------------------------
# Both are (start_offset, end_offset) in TRADING days relative to the
# event day (day 0 = the first trading day on/after event_date — see
# event_study_utils.locate_event_index for why "on/after" rather than
# an exact match).
#
# SHORT_WINDOW captures the immediate reaction most classic event studies
# stop at. LONG_WINDOW exists because Indian-market PEAD research finds
# that stock prices keep drifting in the direction of the surprise for
# weeks after the immediate reaction — a short window alone would miss
# that drift entirely. Comparing CAR at the end of each window (Part 4)
# is how we measure how much of the total reaction happened AFTER the
# first few days.
SHORT_WINDOW = (-2, 5)
LONG_WINDOW = (-2, 40)

# --- Part 4: "days to stabilize" diffusion-speed metric --------------------
# CAR is considered "stabilized" once it stays within STABILIZE_PCT_THRESHOLD
# (as a fraction of the window's final CAR value) for at least
# STABILIZE_CONSECUTIVE_DAYS consecutive trading days. Both are
# deliberately configurable constants, not hardcoded magic numbers, since
# "how close is close enough" is a judgment call worth being able to tune
# without touching the computation code.
STABILIZE_PCT_THRESHOLD = 0.10
STABILIZE_CONSECUTIVE_DAYS = 3

# --- Part 6: surprise-magnitude buckets ------------------------------------
# Fixed bands rather than quintiles. With a small, hand-entered event
# count (this phase realistically starts with single-digit-to-low-dozens
# of events), quintiles would put ~1-2 events per bucket and the
# boundaries would shift every time an event is added, making buckets
# hard to compare across runs. Fixed, interpretable bands are more robust
# at small sample sizes; once the event list grows into the hundreds,
# switching to quintiles (the more academically standard approach) would
# make sense — see the note in event_study_utils.bucket_events.
SURPRISE_BUCKET_EDGES = [-float("inf"), -10, -3, 3, 10, float("inf")]
SURPRISE_BUCKET_LABELS = ["< -10%", "-10% to -3%", "-3% to +3%", "+3% to +10%", "> +10%"]
