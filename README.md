# Indian Market Information Diffusion Project — Phase 0

## What this is
The foundation for a project that measures how fast information (earnings,
RBI policy, global cues, etc.) gets absorbed into Indian stock prices, with
the eventual goal of finding tradeable patterns.

## Setup (one-time)
1. Install Python 3.9 or newer: https://www.python.org/downloads/
   (On Windows, tick "Add Python to PATH" during install.)
2. Open a terminal / command prompt in this folder.
3. Install the two packages this script needs:
   ```
   pip install yfinance pandas
   ```

## Running Phase 0
```
python 01_data_pipeline.py
```
This creates a `data/` folder containing:
- `nifty50_index.csv` — the benchmark (Nifty 50 index daily prices)
- One CSV per stock (e.g. `RELIANCE_NS.csv`) with daily Open/High/Low/Close/Volume
- `_download_summary.csv` — a sanity-check table showing how much data was pulled for each ticker

## Sanity checks before moving on
- Open a couple of the CSVs. Do the dates look continuous (no giant gaps)?
- Does `_download_summary.csv` show ~1,400+ rows per ticker (5 years of trading days)?
- If a ticker shows a WARNING with no data, double check the Yahoo Finance
  symbol (Indian NSE tickers need the `.NS` suffix, BSE tickers use `.BO`).

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

## Known limitations of this starter version
- Daily data only. Real "how many minutes" questions need intraday data —
  Yahoo Finance's Indian intraday history is too short/patchy for this.
  Phase 2 will bring in a proper intraday source (e.g. Zerodha Kite Connect
  API, which needs a Zerodha trading account, or NSE's own bhavcopy archives).
- Only 15 stocks. Easy to expand — just add tickers to `NIFTY_SUBSET` in
  the script.
- No earnings dates yet — that's Phase 2. For now you can pick a few known
  past earnings dates by hand (e.g. from moneycontrol.com or the company's
  investor relations page) to test Phase 1 once it's built.
