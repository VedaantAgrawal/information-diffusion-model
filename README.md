# Indian Market Information Diffusion Project — Phase 0

## What this is
The foundation for a project that measures how fast information (earnings,
RBI policy, global cues, etc.) gets absorbed into Indian stock prices, with
the eventual goal of finding tradeable patterns.

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
4. Install the packages this script needs:
   ```
   pip install -r requirements.txt
   ```

## Running Phase 0
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

## Sanity checks before moving on
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

## Known limitations of this starter version
- Daily data only. Real "how many minutes" questions need intraday data —
  Yahoo Finance's Indian intraday history is too short/patchy for this
  (roughly the last 60 days only, and unreliable for illiquid names).
  Phase 2 will bring in a proper intraday source (e.g. Zerodha Kite Connect
  API, which needs a Zerodha trading account, or NSE's own bhavcopy archives).
- Only 15 stocks. Easy to expand — just add tickers to `NIFTY_SUBSET` in
  the script.
- No earnings dates yet — that's Phase 2. For now you can pick a few known
  past earnings dates by hand (e.g. from moneycontrol.com or the company's
  investor relations page) to test Phase 1 once it's built.
- Yahoo Finance is a free, unofficial-for-this-use data source and
  occasionally rate-limits or blocks bursts of requests. The script
  retries transient failures automatically, but if you see repeated
  failures across many tickers at once, wait a few minutes and re-run.

## What's next (once Phase 0 data looks good)
- **Phase 1 — Event study engine**: for a given date (e.g. an earnings call),
  compute the stock's return minus the Nifty's return (the "abnormal return"),
  and track how many days it takes to stop drifting — this is your first
  real "diffusion speed" measurement.
- **Phase 2 — Event calendar**: automate pulling earnings dates, RBI policy
  dates, and FII/DII flow data so Phase 1 can run across hundreds of events
  instead of ones you look up by hand.
- **Phase 3 — Pattern mining**: compare diffusion speed across sectors,
  market cap, FII ownership %, etc.
- **Phase 4 — Predictive/strategy layer**: only after 1–3 give you real,
  tested patterns — with strict train/test separation so results aren't
  just overfitting to history.
