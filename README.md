# Information Diffusion Model — Phase 0: Data Foundation

## What this project is

The end goal is an **options trading strategy** on Indian index options
(NIFTY 50 / BANKNIFTY), built around a specific idea: markets don't
absorb new information (earnings, RBI policy decisions, global cues,
FII/DII flows, etc.) instantly — there's a lag, and that lag is
measurable and potentially tradeable. Options are a natural vehicle for
that idea because implied volatility and open interest react to *how
fast and how much* the market expects a price to move, not just the
price itself.

**Phase 0** (this repo, right now) is not the strategy — it's the data
foundation everything else will be built on. Before you can measure "how
fast did the market absorb X", you need years of clean price history and
an ongoing stream of options data to measure that absorption against.
Phase 1 (next) will be an **event-study engine**: given a known event
date (an earnings release, an RBI policy announcement), measure how
quickly price (for equity/index) and implied volatility / open-interest
change (for options) move toward their new "informed" level. This repo
is what makes that possible.

If you're new to data engineering: think of this phase as "build the
pipes and fill the tank", not "build the machine that uses the water".
Nothing in this repo trades anything or makes any prediction — it only
collects and stores data, safely and repeatably.

---

## Project structure

```
.
├── config.py                          # single source of truth: ticker basket, indices, date range, DB path
├── db_utils.py                        # shared SQLite schema + connection helper (used by all three pipelines)
├── 01_equity_prices.py                # Part 1: daily OHLCV for 15 Nifty 50 stocks
├── 02_index_prices.py                 # Part 2: daily OHLCV for NIFTY 50 / BANKNIFTY
├── 03_options_chain.py                # Part 3: live option chain snapshot collector
├── requirements.txt
├── data/
│   └── market_data.db                 # the single SQLite database — this is the actual deliverable of Phase 0
└── .github/workflows/
    ├── update_daily_prices.yml        # runs Parts 1+2 once/day after market close
    └── collect_options_data.yml       # runs Part 3 every ~30 min during market hours
```

Everything reads and writes **one SQLite file**: `data/market_data.db`.
That was a deliberate choice over "CSVs for equity/index + a separate DB
for options": one file means one thing to `sqlite3.connect(...)` to in
Phase 1, and it means you can trivially `JOIN` an options snapshot to
"what was NIFTY's close that day" without stitching files together
yourself.

---

## One-time setup

1. **Install Python 3.10+** if you don't already have it.
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   (This installs `yfinance`, `pandas`, and `nsepython`.)
3. **Push this repo to GitHub** (if you haven't already) — the two
   workflows in `.github/workflows/` only start running once the repo
   lives on GitHub with those files on the default branch.
   **No secrets or API keys need to be configured.** Both workflows
   authenticate with the automatically-provided `GITHUB_TOKEN` to commit
   data back to the repo — nothing to set up in Settings → Secrets.

That's it — there's no database server to install, no accounts to
create. `data/market_data.db` is created automatically (via
`db_utils.get_connection()`) the first time any script runs.

---

## Database schema

### `equity_prices` (Part 1)
One row per (ticker, date). Deduped via a `UNIQUE(ticker, date)`
constraint + `INSERT OR IGNORE`, so re-running a historical pull never
creates duplicates.

| column | type | notes |
|---|---|---|
| ticker | TEXT | e.g. `"RELIANCE.NS"` |
| sector | TEXT | e.g. `"Energy"` |
| date | TEXT | `"YYYY-MM-DD"` |
| open, high, low, close | REAL | |
| volume | INTEGER | |

### `index_prices` (Part 2)
One row per (index_name, date). Same dedup strategy as above.

| column | type | notes |
|---|---|---|
| index_name | TEXT | `"NIFTY50"` or `"BANKNIFTY"` |
| date | TEXT | `"YYYY-MM-DD"` |
| open, high, low, close | REAL | |
| volume | INTEGER | |

### `option_chain_snapshots` (Part 3)
One row per (symbol, snapshot, expiry, strike, option_type). This table
is **append-only** — every collector run adds new rows, nothing is ever
deduped or overwritten, because each `snapshot_time` is a genuinely new
data point (see "The options data limitation" below).

| column | type | notes |
|---|---|---|
| symbol | TEXT | `"NIFTY"` or `"BANKNIFTY"` |
| snapshot_time | TEXT | NSE's own timestamp for this chain |
| pulled_at_utc | TEXT | when *our* script pulled it (ISO 8601 UTC) |
| underlying_value | REAL | spot price of the index at snapshot time |
| expiry | TEXT | option expiry date |
| strike | REAL | strike price |
| option_type | TEXT | `"CE"` (call) or `"PE"` (put) |
| open_interest | REAL | |
| change_in_oi | REAL | change in open interest since previous session |
| volume | REAL | traded volume |
| implied_volatility | REAL | |
| last_price | REAL | |
| bid_price, ask_price | REAL | best bid/ask on the chain |

---

## Running each part manually

```bash
# Part 1 — equity prices, full history, all 15 configured tickers
python 01_equity_prices.py

# Part 1 — override the date range
python 01_equity_prices.py --start 2020-01-01 --end 2024-01-01

# Part 1 — incremental (only fetch days after what's already stored)
python 01_equity_prices.py --incremental

# Part 1 — limit to specific tickers
python 01_equity_prices.py --tickers RELIANCE.NS,TCS.NS

# Part 2 — index prices (same flags as Part 1, minus --tickers)
python 02_index_prices.py
python 02_index_prices.py --incremental

# Part 3 — one live option chain snapshot for NIFTY + BANKNIFTY
python 03_options_chain.py
```

**What to sanity-check after running Parts 1/2:** both scripts print a
summary table (ticker/index, row count, first date, last date) and save
it to `data/equity_summary.csv` / `data/index_summary.csv`. Check that:
- every configured ticker/index actually has rows (a `row_count` of 0
  with a `[WARN] ... no data returned` message means something's wrong —
  a bad ticker symbol, a delisting, or a transient Yahoo Finance issue)
- `first_date` is close to your requested `--start`
- `last_date` is close to `--end` (a recent trading day)

**What to sanity-check after running Part 3:** the printed line
`"{symbol}: stored N rows for snapshot_time=..."` — N should be roughly
`(number of strikes) × (number of expiries) × 2` (calls + puts). If you
see `[WARN] ... fetch failed` or `produced no rows`, see the limitation
section below before assuming something's broken.

---

## The options data limitation (read this before Phase 1)

NSE's free option chain endpoint (what `nsepython` scrapes) only ever
returns the **current, live** chain. There is no free source for
**historical** option chains — this script cannot backfill last month's,
or even yesterday's, option chain. That's a hard limitation of the data
source, not a bug.

Because of that, `03_options_chain.py` is deliberately built to be run
**frequently and repeatedly** (every ~30 minutes during market hours,
via `collect_options_data.yml`) so that, starting from whenever you
first turn this on, you build up your own intraday-resolution history
snapshot by snapshot. This is why Part 3 runs on a much tighter schedule
than Parts 1/2 — the whole point of this project is measuring how *fast*
information gets absorbed, and daily bars can't show you that; only
intraday snapshots can.

**If you need real historical option chain data** (e.g. to backtest a
strategy over past years rather than only data collected from today
forward), the realistic paid options are:
- **Sensibull** or **Opstra Definedge** (retail-focused historical
  options data vendors)
- A broker API with historical entitlements, e.g. **Zerodha Kite
  Connect** or **Upstox** (requires an active trading account with that
  broker)

**A related, separate risk worth knowing about:** NSE's website actively
blocks a lot of non-browser / datacenter traffic (bot detection). In
testing this repo, requests from a cloud/sandboxed environment were
blocked outright (an HTTP 403 on the homepage, and a fake "Resource not
found" response on the actual API call) even during NSE market hours.
**GitHub Actions runners are themselves cloud/datacenter IPs**, so it is
possible `collect_options_data.yml` will sometimes (or consistently) get
blocked the same way, in addition to the expected "run happened outside
market hours" case. Both scripts already treat this the same way as any
other fetch failure — log a warning, skip that symbol, don't touch the
database — so a blocked run degrades gracefully rather than corrupting
anything. If you find the workflow is *never* successfully collecting
data, try running `python 03_options_chain.py` manually from your own
machine during market hours first to confirm it works at all from a
residential/non-cloud IP before debugging the workflow itself.

---

## GitHub Actions automation

### `update_daily_prices.yml` (Parts 1 + 2)
- **Schedule:** `30 12 * * 1-5` → 12:30 UTC, Mon–Fri → **6:00 PM IST**
  (IST = UTC+5:30). NSE closes at 3:30 PM IST; this runs 2.5 hours later
  to safely be past close and past when Yahoo Finance has finalized the
  day's closing bar.
- **Incremental by design:** it calls `01_equity_prices.py --incremental`
  and `02_index_prices.py --incremental`, which query the DB for the
  latest stored date per ticker/index and only fetch from the day after
  that forward — not a full multi-year re-download every day. On a
  brand-new/empty database, `get_latest_date()` returns `None` and the
  scripts automatically fall back to a full historical pull.
- **Market holidays are expected to produce "no new data":** NSE is
  closed on holidays (Diwali, Republic Day, etc.). On those days, Yahoo
  Finance has nothing new to report either, so the run finds 0 new rows
  and skips the commit cleanly. This is normal, not a failure — you
  don't need to investigate every holiday run.
- **Manual trigger:** go to the **Actions** tab → **Update Daily Equity
  & Index Prices** → **Run workflow**.

### `collect_options_data.yml` (Part 3)
- **Schedule:** `*/30 3-10 * * 1-5` → every 30 minutes, 3:00–10:59 UTC,
  Mon–Fri. NSE market hours are 9:15 AM–3:30 PM IST = roughly 3:45–10:00
  UTC; the window here is intentionally a bit wider than that to absorb
  scheduling jitter (see caveat below) rather than clipping the
  open/close.
- **Manual trigger:** go to the **Actions** tab → **Collect Options
  Chain Snapshots** → **Run workflow**. Useful for testing outside the
  scheduled window (though it will still only succeed if it happens to
  run during actual NSE market hours and isn't blocked — see the
  limitation section above).

### Adjusting either schedule
Cron schedules live at the top of each `.yml` file under `on: schedule:
- cron: '...'`. Cron fields are `minute hour day month weekday` in
**UTC**, and IST is always UTC+5:30 (no daylight saving in India, so
this offset never changes). To shift a schedule, convert your desired
IST time to UTC by subtracting 5 hours 30 minutes.

### Caveat: GitHub's free-tier scheduling isn't exact
GitHub explicitly documents that scheduled workflow runs on the free
tier can be delayed, especially during periods of high load on their
infrastructure — a `cron` entry is a *request* for a time, not a
guarantee. Don't be surprised if a run fires a few minutes (occasionally
longer) after its scheduled time. This is a known platform limitation,
not something to debug in this repo's code.

### Both workflows write to the same database file — commit conflict handling
Since `update_daily_prices.yml` and `collect_options_data.yml` both
ultimately commit changes to `data/market_data.db`, there's a small
chance of a git conflict if both happen to run and try to push around
the same time. Both workflows mitigate this by running `git pull
--rebase origin main` **twice** — once right after checkout (so they
start from the latest committed data) and once again right before the
final `git push` (so a commit from the other workflow that landed in
between doesn't cause a rejected push). This meaningfully shrinks the
conflict window but can't eliminate it entirely — SQLite database files
are binary, so if both workflows *do* manage to commit conflicting
changes in that narrow window, `git rebase` would hit a binary conflict
it can't auto-resolve, and that one job run would fail. If that ever
happens, the fix is simply to re-run the failed workflow (via
`workflow_dispatch`) — it will pull the latest committed state and
append its data on top; no manual data recovery is needed since neither
workflow ever deletes or overwrites existing rows, only appends/inserts.

---

## Querying the data with pandas

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("data/market_data.db")

# Equity: RELIANCE's full daily history
reliance = pd.read_sql(
    "SELECT * FROM equity_prices WHERE ticker = 'RELIANCE.NS' ORDER BY date",
    conn,
)

# Index: NIFTY 50 closes only, as a quick time series
nifty_close = pd.read_sql(
    "SELECT date, close FROM index_prices WHERE index_name = 'NIFTY50' ORDER BY date",
    conn,
)

# Options: every NIFTY snapshot collected so far, most recent first
nifty_chain = pd.read_sql(
    "SELECT * FROM option_chain_snapshots WHERE symbol = 'NIFTY' ORDER BY snapshot_time DESC",
    conn,
)

# Example join: NIFTY's option chain alongside that day's index close,
# by matching the snapshot's calendar date to index_prices.date
combined = pd.read_sql(
    """
    SELECT o.*, i.close AS nifty_close_that_day
    FROM option_chain_snapshots o
    JOIN index_prices i
      ON i.index_name = 'NIFTY50'
     AND date(o.snapshot_time) = i.date
    WHERE o.symbol = 'NIFTY'
    """,
    conn,
)

conn.close()
```

---

## What's next: Phase 1

Phase 1 will be an **event-study engine**: given a list of known event
dates (individual companies' earnings dates, RBI monetary policy
announcement dates), measure how quickly the market "absorbs" that
information —
- for equity/index: how price moves and how quickly it stabilizes
  around the event, relative to a normal (non-event) day
- for options: how quickly implied volatility and open interest shift
  around the event, which is the more direct signal of the market
  repricing *expected future movement*, not just past movement

Everything Phase 1 needs — years of clean daily price history, and a
growing intraday options snapshot history — is what this repo (Phase 0)
exists to build.

**Phase 1 is now built** — see [README_phase1.md](README_phase1.md) for
the event-study engine (`04_event_study.py`) that implements the first
half of this: a market-model event study of equity price reactions to
earnings surprises, calibrated (not predictive) by design.
