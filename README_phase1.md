# Phase 1 — Event Study Engine

## What this phase is (and isn't)

Phase 0 built the data pipes (`equity_prices`, `index_prices`, and — once
the NSE access issue from Phase 0.5 is fixed — `option_chain_snapshots`).
Phase 1 is the first thing that actually *uses* that data.

**This is NOT trying to predict what a stock will do next.** Predicting
the direction of a future price move from an earnings surprise is
speculative, and a market-model event study can't honestly deliver that
kind of forecast anyway. What this phase actually does is closer to
**actuarial calibration**: it looks at a set of past earnings surprises,
measures how the market historically reacted to each one, and asks "when
past surprises were of magnitude X, how big was the resulting price
reaction Y, on average?" That's a backward-looking, descriptive question
— not a forward-looking, predictive one. Keep this distinction in mind
reading every number this phase produces, and notice that the code and
reports deliberately avoid the words "predict" / "forecast" in favor of
"historical reaction" / "calibration".

**Why this matters for later phases:** once Phase 0.5's option chain data
is flowing, a *future* phase (not this one) will compare this
historically-calibrated "typical reaction to a surprise this size"
against what the options market's implied volatility is **currently**
pricing in for an upcoming, similar event. The gap between those two
numbers — not this phase's output by itself — is where an actual
tradeable signal would come from.

---

## The method, and why it's built this way

### Market model (not a naive raw-return comparison)

For each event, we estimate how the stock normally moves relative to the
market (NIFTY 50) using a simple OLS regression:

```
stock_return(t) = alpha + beta * nifty_return(t) + error(t)
```

This is the **market model**, the standard technique used in most
published event studies since Brown & Warner's classic simulation
studies found it (and simpler models) perform well for short windows.
`alpha`/`beta` are estimated over a **250-trading-day window** (~1
trading year — the conventional length), ending **30 trading days before
the event** (a buffer, so pre-event anticipatory drift doesn't
contaminate the beta estimate itself).

**R² is always reported alongside beta.** A low R² means the stock's
daily moves aren't well explained by the market's moves over the
estimation window — which means the "abnormal" return computed from that
beta is less trustworthy too. Every per-event report flags this
explicitly rather than burying it in a number nobody reads.

### Abnormal Return (AR) and Cumulative Abnormal Return (CAR)

Once we have `alpha`/`beta`, the **Abnormal Return** on any day is:

```
AR(t) = actual_stock_return(t) - (alpha + beta * nifty_return(t))
```

i.e. "how much did this stock move beyond what its normal relationship
with the market would predict for that day". **Cumulative Abnormal
Return (CAR)** is just the running sum of AR from the start of a window
to each day in it — it's the metric that actually shows the shape of the
reaction (a sharp jump vs. a slow bleed) rather than just a single
before/after number.

### Two windows, not one — because of PEAD

Indian-market research has repeatedly found evidence of
**Post-Earnings-Announcement Drift (PEAD)**: prices do NOT fully adjust
to a surprise immediately — they keep drifting in the direction of the
surprise for weeks afterward. A study that only looks at a short window
around the event would systematically miss this. So this phase computes
CAR over **two** windows (both centered on event day 0, sizes configurable
in `config.py`):

- **SHORT window** `(-2, +5)` trading days — the immediate reaction.
- **LONG window** `(-2, +40)` trading days — tests for continued drift
  beyond the immediate reaction.

Every per-event report shows both, plus the **gap between them**
(`long_window_car - short_window_car`) — how much of the total reaction
happened *after* the first few days. A large gap is itself evidence of
PEAD for that stock/event.

### "Days to stabilize" — a diffusion-speed metric

This is the closest thing this phase has to directly answering "how fast
did the market absorb this information": the number of trading days
**after** the event until CAR settles down and stays within
`STABILIZE_PCT_THRESHOLD` (default 10%) of its *final* value in the long
window, for at least `STABILIZE_CONSECUTIVE_DAYS` (default 3) consecutive
days. Both thresholds are configurable constants in `config.py`, not
hardcoded magic numbers.

If CAR **never** stabilizes within the 40-day long window, that's
reported explicitly (as `None` / "did not stabilize") rather than as a
missing value — it's itself a meaningful finding, consistent with
extended PEAD for that event.

### Event day convention

"Day 0" for an event is the **first trading day on or after
`event_date`**, not necessarily an exact date match. Indian earnings are
routinely announced after market close (or around a weekend board
meeting), so treating the next available trading day as day 0 captures
the first day the market could plausibly have reacted — the quantity we
actually care about.

---

## Researching and filling in `events.csv`

`events.csv` (project root) has one row per event:

| column | meaning |
|---|---|
| `ticker` | must match a ticker already in Phase 0's `equity_prices` (see `config.EQUITY_TICKERS`) |
| `event_date` | `YYYY-MM-DD`, the announced/expected event date |
| `event_type` | e.g. `earnings` (this phase is written with earnings surprises in mind) |
| `actual_eps`, `expected_eps` | leave blank if you only have a rough surprise % (see below) |
| `surprise_pct` | `(actual_eps - expected_eps) / abs(expected_eps) * 100`, computed automatically if left blank and both EPS values are given |
| `notes` | free text — cite where the number came from |

**Two ways to fill in a row**, both supported:
1. You found clean `actual_eps` / `expected_eps` (consensus estimate)
   numbers → put both in, leave `surprise_pct` blank, and the script
   computes it.
2. You only found a "beat/missed estimates by ~X%" statement in coverage,
   with no clean EPS pair → put your best estimate directly into
   `surprise_pct`, leave `actual_eps`/`expected_eps` blank.

**Where to find this, for this phase (no scraping — by hand):** Indian
financial news (Moneycontrol, Economic Times, Business Standard) routinely
states "X beat/missed Street estimates by Y%" directly in earnings
coverage — that sentence is usually enough for path 2 above. For path 1,
the same outlets often report the analyst consensus EPS estimate
alongside the actual reported EPS.

The shipped `events.csv` contains **placeholder rows** (clearly marked
`PLACEHOLDER` in the `notes` column) using real historical result dates
for a few stocks already in the Phase 0 basket, but made-up EPS/surprise
figures — they exist so the pipeline is runnable out of the box. **Replace
them with real researched numbers before treating any output as
meaningful.**

A row is silently skipped (with a `[WARN]` line, not a crash) if:
- there's no `surprise_pct` and no `actual_eps`/`expected_eps` pair to
  compute one from,
- the ticker has no rows in `equity_prices`,
- the event date is after the most recent price data available,
- there isn't enough surrounding price history for the full estimation
  window + long window (e.g. an event too close to the start of Phase
  0's price history, or too close to today for the 40-day-out long
  window to be complete yet).

---

## Running it

```bash
# default: reads events.csv, writes to reports/
python 04_event_study.py

# use a different event list
python 04_event_study.py --events my_events.csv

# write reports elsewhere
python 04_event_study.py --output-dir out/
```

### Outputs

```
reports/
├── events/
│   ├── <TICKER>_<EVENT_DATE>.png     # CAR curve chart, one per event
│   └── <TICKER>_<EVENT_DATE>.txt     # templated plain-text summary, one per event
├── event_results.csv                 # one row per event: the combined table Part 6 is built from
├── calibration_buckets.csv           # Part 6 bucket table
├── calibration_scatter.png           # Part 6 scatter + regression line + bucket markers
└── calibration_report.txt            # Part 6 written report
```

**Per-event chart:** the CAR curve across the full long window, with the
event day and the short-window boundary marked as reference lines — read
it as "how much of the curve's total movement happened before the dotted
line vs. after it".

**Per-event text summary:** beta, R² (with a caveat auto-inserted if it's
low), surprise_pct, both windows' CAR, the drift gap, days-to-stabilize
(or "did not stabilize"), and a plain-language interpretation — every
number is computed, nothing in the template is hardcoded.

### Reading the Part 6 calibration report

This is the actual deliverable: "if EPS moved +/-5%, the stock
historically moved +/-8%", built entirely from `event_results.csv` (no
new data pull). It has two views of the same underlying relationship,
meant to check each other:

- **Bucket table**: events grouped into fixed surprise-magnitude bands
  (not quintiles — see the comment in `config.py` for why fixed bands are
  more robust at small sample sizes). Each bucket reports `n_events`, the
  average short/long-window CAR, and the spread (std/min/max) so you can
  see how noisy each bucket's average actually is.
- **Regression fit**: a simple linear regression of long-window CAR
  against `surprise_pct` across *all* events — slope, intercept, and R².
  "Each 1% of surprise has historically corresponded to roughly `slope`%
  of long-window CAR."

If a bucket average and the regression line disagree sharply at that
bucket's surprise level, check that bucket's `long_window_car_min`/`max`
first — it likely just means a small `n` and/or an outlier event, not
that one method is "wrong".

### ⚠️ Small-sample caveat — read this before trusting any number here

With the shipped placeholder `events.csv` (8 rows, and even once
replaced with real researched events, likely still a low double-digit
count for a while), **both the bucket averages and the regression slope
are a proof of concept, not a statistically reliable result.** A single
outlier event can swing a bucket average heavily, and a low regression R²
(expected at this sample size) means the linear fit explains only a small
share of the event-to-event variation. This caveat is also printed at the
top of every generated `calibration_report.txt` — it's meant to travel
with the output, not just live in this README.

**Do not use this calibration to inform any real trading decision until
significantly more events have been researched and added.**

---

## What's next

- **Phase 2** (not built yet): automate collecting event dates and
  consensus estimates instead of hand-entering `events.csv` — the biggest
  lever for making this phase's calibration statistically meaningful is
  simply more events, and that's currently the manual bottleneck.
- **Future phase, once Phase 0.5's option chain data is flowing**:
  compare this phase's historically-calibrated "typical reaction to a
  surprise this size" against what the options market's implied
  volatility is *currently* pricing in ahead of a similar upcoming event.
  The gap between the two — historical calibration vs. current market
  pricing — is the actual tradeable signal this whole project is working
  toward. This phase deliberately keeps its per-event output (one row per
  event, consistent columns) clean specifically so that comparison is
  easy to bolt on later, along with the cross-stock comparison one Indian
  study found (stocks with higher options volume absorb information
  faster, i.e. less drift) — also not built yet, but the same reason for
  keeping output structured and consistent applies.
