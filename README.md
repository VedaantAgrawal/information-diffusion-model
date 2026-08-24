# Indian Market Information Diffusion Project

## What this is
A research project to study how fast information (earnings calls, RBI
policy, global cues, FII/DII flows, etc.) gets absorbed into Indian
markets, with the eventual goal of building an **options trading**
strategy. Phase 0 built the equity price data pipeline; Phase 0.5 adds
the index and options data layers that options strategies actually need.

## Setup (one-time)
1. Install Python 3.9 or newer: https://www.python.org/downloads/
   (On Windows, tick "Add Python to PATH" during install.)
2. Open a terminal / command prompt in this folder.
3. (Recommended) create a virtual environment so these packages don't
   clash with anything else on your machine:
   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   source .venv/bin/activate     # macOS/Linux
   ```
4. Install the packages both scripts need:
   ```
   pip install -r requirements.txt
   ```

## Phase 0 — Equity price pipeline

### Running
```
python 01_data_pipeline.py
```
This creates a `data/` folder containing:
- `nifty50_index.csv` — the benchmark (Nifty 50 index daily prices)
- One CSV per stock (e.g. `RELIANCE_NS.csv`) with daily Date/Open/High/Low/Close/Volume
- `_download_summary.csv` — a sanity-check table showing status, row
  count, and date coverage for every ticker (including any that failed)

By default it pulls 2019-01-01 through today for the 15 stocks listed in
`NIFTY_SUBSET` inside the script. You can override the date range without
editing the file:
```
python 01_data_pipeline.py --start 2015-01-01 --end 2024-12-31
```

### Sanity checks before moving on
- Open a couple of the CSVs. Do the dates look continuous (no giant gaps
  beyond weekends/holidays)?
- Does `_download_summary.csv` show ~1,400+ rows per ticker for the
  default 2019-onward range (roughly 5 years of NSE trading days)?
- Check the `status` column in `_download_summary.csv` — everything
  should say `OK`. If a ticker shows `FAILED (...)`, the script already
  retried it 3 times automatically; just re-run the script and it will
  redownload only what's missing (existing good CSVs get overwritten
  with the same data, which is harmless).
- If a ticker fails consistently, double-check the Yahoo Finance symbol
  (Indian NSE tickers need the `.NS` suffix, BSE tickers use `.BO`).

### Known limitations
- Daily data only. Real "how many minutes" questions need intraday data —
  Yahoo Finance's Indian intraday history is too short/patchy for this
  (roughly the last 60 days only, and unreliable for illiquid names).
- Only 15 stocks. Easy to expand — just add tickers to `NIFTY_SUBSET` in
  the script.
- No earnings dates yet — that's a later phase. For now you can pick a
  few known past earnings dates by hand (e.g. from moneycontrol.com or
  the company's investor relations page) to test the event study once
  it's built.
- Yahoo Finance is a free, unofficial-for-this-use data source and
  occasionally rate-limits or blocks bursts of requests. The script
  retries transient failures automatically, but if you see repeated
  failures across many tickers at once, wait a few minutes and re-run.

## Phase 0.5 — Index + options data layer

Since the end goal is **options trading**, not equity, Phase 0.5 adds two
things Phase 0 doesn't cover:
1. **Index price history** (NIFTY 50, BANKNIFTY) — the underlyings for
   India's most liquid index options.
2. **Live option chain snapshots** — strikes, expiries, open interest,
   change in OI, volume, implied volatility, and bid/ask/last price. This
   is the actual tradeable instrument data.

### Running
```
python 02_phase0_5_options_data.py
```
This creates:
- `data/index_prices/` — one CSV per index (daily OHLCV, same pattern as Phase 0)
- `data/option_chain_snapshots/` — one CSV per symbol per run, timestamped
  (e.g. `NIFTY_20260824_093015.csv`)

Useful flags:
```
python 02_phase0_5_options_data.py --skip-options        # index history only, e.g. to test outside market hours
python 02_phase0_5_options_data.py --start 2015-01-01     # override index history start date
```

### Critical limitation: no free historical option chain
NSE's free option chain endpoint only gives the **current live snapshot**
— there's no free source for historical option chains (past OI/IV at past
strikes on past dates). This script is a **snapshot collector**, not a
backfill tool: run it regularly (ideally daily, via a scheduled task —
see below) and you'll build your own historical archive going forward.
It cannot retroactively give you option chain data from before you
started running it.

If you need historical option data further back for backtesting, you'll
need a paid source: **Sensibull** or **Opstra Definedge** (retail-friendly,
historical IV/OI), or a broker API with F&O history (**Zerodha Kite
Connect**, **Upstox**).

### Second limitation: NSE actively blocks scripted requests
This isn't just rate-limiting. NSE's site sits behind a bot-detection
layer that can reject a plain scripted HTTP request outright — a 403 or
a "challenge" response instead of JSON — even with a realistic browser
User-Agent header, and even on the very first request of a run. It's
intermittent, and running from a server/cloud machine tends to make it
worse than running from a normal home connection. There's no guaranteed
fix for a free scraper. The script retries each symbol up to 3 times
with a 10s backoff, and never crashes — a blocked symbol is recorded as
`FAILED` in the run summary instead of stopping the whole script.

If option chain collection keeps returning 0 rows:
1. Run during NSE market hours (9:15 AM – 3:30 PM IST), from a normal
   home/office connection — both reduce the odds of being blocked.
2. Try `pip install --upgrade nsepython` — the library gets updated
   periodically to work around NSE's latest blocking behavior.
3. Just retry later; the block often isn't persistent.
4. If it never works reliably for you, this free approach may not be
   solid enough for unattended daily collection — a paid/authenticated
   source (broker API, Sensibull) may be worth it once you're past the
   prototyping stage.

### Automating daily collection
Since there's no historical backfill, the value of this collector comes
from running it every trading day so the archive accumulates. Two easy
options:
- **Windows Task Scheduler**: create a daily trigger (e.g. 3:35 PM, just
  after market close) that runs
  `<path to .venv>\Scripts\python.exe 02_phase0_5_options_data.py`
  with "Start in" set to this project folder.
- **cron** (macOS/Linux/WSL): `35 15 * * 1-5 cd /path/to/project && .venv/bin/python 02_phase0_5_options_data.py`
  (weekdays only; NSE isn't open on weekends).

## What's next
- **Phase 1 — Event study engine**: for a given date (e.g. an earnings call),
  compute the stock's return minus the Nifty's return (the "abnormal return"),
  and track how many days it takes to stop drifting — this is your first
  real "diffusion speed" measurement. Note: the Nifty benchmark and the
  individual stock CSVs from Phase 0 don't always have identical row
  counts (a handful of date mismatches are normal), so align on `Date`
  with an inner join before computing abnormal returns.
- **Phase 2 — Event calendar**: automate pulling earnings dates, RBI policy
  dates, and FII/DII flow data so Phase 1 can run across hundreds of events
  instead of ones you look up by hand.
- **Phase 3 — Pattern mining**: compare diffusion speed across sectors,
  market cap, FII ownership %, and — once enough option chain snapshots
  have accumulated from Phase 0.5 — whether options positioning (OI, IV)
  reacts faster or slower than the underlying price.
- **Phase 4 — Predictive/strategy layer**: only after 1–3 give you real,
  tested patterns — with strict train/test separation so results aren't
  just overfitting to history.
