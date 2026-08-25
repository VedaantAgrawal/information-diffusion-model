"""
04_event_study.py — Phase 1: the event study engine.

WHAT THIS SCRIPT IS FOR (read this before the code — see also
README_phase1.md for the full-length version):
This is NOT trying to predict which direction a stock will move after an
earnings surprise. That would be speculative, and single-event prediction
isn't what a market-model event study can honestly deliver. Instead, this
script calibrates a HISTORICAL relationship: "when past earnings surprises
were of magnitude X, the resulting price reaction was typically of
magnitude Y." Think actuarial calibration, not forecasting — we are
characterizing how the market HAS reacted to surprises of a given size,
so that a later phase (once options-chain data is flowing — see Phase 0.5)
can compare this historically-calibrated reaction against what the options
market is CURRENTLY pricing in. The gap between those two numbers, not
this script's output on its own, is where a tradeable signal would live.

WHAT IT DOES, IN ORDER (Parts 1-6 of the handoff spec):
  1. Read events.csv (ticker, event_date, actual/expected EPS or a direct
     surprise_pct).
  2. For each event, estimate alpha/beta via a market-model OLS regression
     of the stock's returns on NIFTY 50's returns over a pre-event window.
  3. Compute daily Abnormal Returns and Cumulative Abnormal Returns (CAR)
     over a SHORT window (immediate reaction) and a LONG window (tests for
     Post-Earnings-Announcement Drift, PEAD).
  4. Measure "days to stabilize" — how many trading days after the event
     until CAR settles down — as a diffusion-speed metric.
  5. Write a per-event chart + text summary, and a combined results CSV.
  6. Bucket all events by surprise magnitude and fit a regression of
     long-window CAR against surprise_pct: the actual calibration
     deliverable.

USAGE:
    python 04_event_study.py                          # default events.csv -> reports/
    python 04_event_study.py --events my_events.csv    # use a different event list
    python 04_event_study.py --output-dir out/         # write reports elsewhere
"""

import argparse
import math
import os

import matplotlib
matplotlib.use("Agg")  # write PNGs directly, no GUI backend needed
import matplotlib.pyplot as plt
import pandas as pd

import config
import db_utils
import event_study_utils as esu


# ---------------------------------------------------------------------------
# Part 5 — Per-event report (chart + templated text summary)
# ---------------------------------------------------------------------------

def plot_event_car(ticker: str, event_date: str, long_car_df: pd.DataFrame, out_path: str) -> None:
    """
    Line chart of the CAR curve across the long window, with the event
    date (offset 0) and the short-window boundary marked as reference
    lines, so a reader can see at a glance how much of the total reaction
    happened in the first few days vs. later drift.
    """
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(long_car_df["offset"], long_car_df["car"] * 100, marker="o", markersize=3, linewidth=1.5)
    ax.axvline(0, color="black", linestyle="--", linewidth=1, label="Event day (day 0)")
    ax.axvline(config.SHORT_WINDOW[1], color="gray", linestyle=":", linewidth=1,
               label=f"Short-window boundary (day {config.SHORT_WINDOW[1]})")
    ax.axhline(0, color="lightgray", linewidth=0.8)

    ax.set_title(f"{ticker} — Cumulative Abnormal Return around {event_date}\n(historical reaction, not a prediction)")
    ax.set_xlabel("Trading days relative to event")
    ax.set_ylabel("CAR (%)")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def write_event_summary(ticker: str, event_date: str, surprise_pct: float, beta_info: dict,
                         short_car: float, long_car: float, stabilize_day, out_path: str) -> None:
    """Templated plain-text summary for one event — every number comes from computed results, nothing hardcoded."""
    if stabilize_day is None:
        stabilize_text = (
            f"CAR did NOT stabilize within the {config.LONG_WINDOW[1]}-trading-day long window "
            f"(within {config.STABILIZE_PCT_THRESHOLD:.0%} of its final value for "
            f"{config.STABILIZE_CONSECUTIVE_DAYS}+ consecutive days). This itself is a meaningful "
            f"finding — it suggests the price reaction was still drifting when the window ended, "
            f"consistent with extended Post-Earnings-Announcement Drift (PEAD) rather than a quick, "
            f"one-shot adjustment."
        )
    else:
        stabilize_text = (
            f"CAR stabilized {stabilize_day} trading day(s) after the event (stayed within "
            f"{config.STABILIZE_PCT_THRESHOLD:.0%} of its final value for "
            f"{config.STABILIZE_CONSECUTIVE_DAYS}+ consecutive days)."
        )

    drift_gap = long_car - short_car
    r2 = beta_info["r_squared"]
    r2_caveat = (
        " NOTE: this R² is low, meaning the beta estimate (and every abnormal return computed "
        "from it) explains relatively little of this stock's daily variation against the market — "
        "treat the numbers below with extra caution for this event."
        if r2 < 0.3 else ""
    )

    text = f"""Event study summary — {ticker}, event date {event_date}
{'=' * 60}

FRAMING: this is a HISTORICAL REACTION summary, not a prediction of what
this stock will do next. It characterizes how this one stock reacted to
this one surprise, using the market model.

Earnings surprise: {surprise_pct:+.2f}%

Market model (beta estimation window: {config.ESTIMATION_WINDOW_DAYS} trading days,
ending {config.ESTIMATION_BUFFER_DAYS} trading days before the event):
  alpha (daily)  = {beta_info['alpha']:+.5f}
  beta           = {beta_info['beta']:+.3f}
  R-squared      = {r2:.3f}{r2_caveat}
  observations   = {beta_info['n_obs']}

Cumulative Abnormal Return (CAR):
  Short window {config.SHORT_WINDOW} : {short_car:+.2%}
  Long window  {config.LONG_WINDOW}  : {long_car:+.2%}
  Drift after short window ends     : {drift_gap:+.2%}
  ({'Most of the reaction happened early; little further drift.' if abs(drift_gap) < abs(short_car) * 0.25 or abs(short_car) < 1e-6 else 'A meaningful share of the total reaction accrued AFTER the short window — evidence consistent with PEAD.'})

Diffusion speed:
  {stabilize_text}

Interpretation:
  Historically, a {surprise_pct:+.2f}% earnings surprise for {ticker} was followed by a
  {long_car:+.2%} cumulative abnormal return over the following {config.LONG_WINDOW[1]} trading
  days. This describes what happened after this one past event, under the market-model
  assumptions above — it is not a forecast for any future surprise of similar size. See
  the combined calibration report (Part 6) for how this fits alongside other events of
  similar surprise magnitude.
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Part 6 — Combined calibration report (chart + text)
# ---------------------------------------------------------------------------

def plot_calibration_scatter(results_df: pd.DataFrame, bucket_df: pd.DataFrame, regression: dict, out_path: str) -> None:
    """Scatter of surprise_pct vs. long-window CAR, one point per event, with the regression line and bucket averages annotated."""
    fig, ax = plt.subplots(figsize=(9, 6))

    ax.scatter(results_df["surprise_pct"], results_df["long_window_car"] * 100,
               alpha=0.7, edgecolor="black", linewidth=0.5, label="Individual events", zorder=3)

    x_line = [results_df["surprise_pct"].min(), results_df["surprise_pct"].max()]
    y_line = [regression["slope"] * x + regression["intercept"] for x in x_line]
    ax.plot(x_line, [y * 100 for y in y_line], color="firebrick", linewidth=1.5,
            label=f"Regression fit (slope={regression['slope']:.3f}, R²={regression['r_squared']:.2f})")

    bucket_midpoints = _bucket_midpoints()
    valid = bucket_df["n_events"] > 0
    ax.scatter(
        [bucket_midpoints[i] for i in bucket_df.index[valid]],
        bucket_df.loc[valid, "avg_long_window_car"] * 100,
        marker="D", s=90, color="darkorange", edgecolor="black", zorder=4,
        label="Bucket average (annotated with n)",
    )
    for i in bucket_df.index[valid]:
        ax.annotate(
            f"n={int(bucket_df.loc[i, 'n_events'])}",
            (bucket_midpoints[i], bucket_df.loc[i, "avg_long_window_car"] * 100),
            textcoords="offset points", xytext=(6, 6), fontsize=8,
        )

    ax.axhline(0, color="lightgray", linewidth=0.8)
    ax.axvline(0, color="lightgray", linewidth=0.8)
    ax.set_title("Surprise magnitude vs. historical long-window CAR\n(calibration, not a predictive model — small-sample proof of concept)")
    ax.set_xlabel("Earnings surprise (%)")
    ax.set_ylabel(f"Long-window CAR (%), {config.LONG_WINDOW[1]} trading days out")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _bucket_midpoints() -> dict:
    """
    Rough numeric x-position for each fixed surprise-bucket band (keyed by
    row position 0..n-1, which is how bucket_df's rows come out of
    groupby(..., observed=False) — pandas preserves the categorical's
    definition order, i.e. the order SURPRISE_BUCKET_LABELS was written in).
    Used only to place bucket-average markers on the raw scatter plot.
    Open-ended bands (< -10%, > +10%) get a placeholder position just
    outside the bounded bands rather than +/-inf, purely for display.
    """
    edges = config.SURPRISE_BUCKET_EDGES
    midpoints = {}
    for i in range(len(config.SURPRISE_BUCKET_LABELS)):
        lo, hi = edges[i], edges[i + 1]
        if lo == float("-inf"):
            midpoints[i] = hi - 5
        elif hi == float("inf"):
            midpoints[i] = lo + 5
        else:
            midpoints[i] = (lo + hi) / 2
    return midpoints


def write_calibration_report(results_df: pd.DataFrame, bucket_df: pd.DataFrame, regression: dict, out_path: str) -> None:
    n = len(results_df)
    bucket_table_str = bucket_df.to_string(index=False)

    text = f"""Surprise-to-reaction calibration report (Part 6)
{'=' * 60}

FRAMING: this report characterizes how this basket of stocks has
HISTORICALLY reacted to earnings surprises of different sizes. It is a
calibration, not a prediction — it says nothing about what any specific
future surprise will produce, only what past surprises of similar
magnitude have produced on average.

*** SMALL-SAMPLE CAVEAT ***
This calibration is built from {n} hand-researched event(s). With this few
events, both the bucket averages and the regression slope below should be
treated as a PROOF OF CONCEPT, not a statistically reliable result. Single
outlier events can swing a bucket average heavily, and the regression R²
below should be read in that light. More events need to be researched and
added to events.csv before these numbers should inform any real decision.

Bucket table (fixed surprise-magnitude bands):
{bucket_table_str}

Regression fit — long-window CAR ~ surprise_pct, across all {n} events:
  slope     = {regression['slope']:+.4f}   (each 1% of surprise -> ~{regression['slope']*100:+.3f}% of long-window CAR, historically)
  intercept = {regression['intercept']:+.4f}
  R-squared = {regression['r_squared']:.3f}
  n         = {regression['n_obs']}

How to read the bucket table alongside the regression:
  The bucket table and the regression are two views of the same data, and
  are meant to check each other. If a bucket's average is wildly out of
  line with what the regression line would predict at that bucket's
  surprise level, that bucket likely has a small n and/or an outlier event
  — look at long_window_car_min/max for that bucket before trusting either
  number.

WHAT'S NEXT (not built in this phase):
  Once option chain data is flowing again (see Phase 0.5's note about the
  pending NSE access fix), a future phase will compare this historically-
  calibrated expected reaction magnitude against what the options market's
  implied volatility is CURRENTLY pricing in for a similar upcoming event.
  The gap between "what IV is pricing in" and "what history says this size
  of surprise typically produces" — not this report by itself — would be
  the actual tradeable signal.
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(events_csv_path: str, output_dir: str) -> None:
    events_dir = os.path.join(output_dir, "events")
    os.makedirs(events_dir, exist_ok=True)

    events_df = esu.load_events(events_csv_path)
    conn = db_utils.get_connection()
    index_df = esu.load_index_series(conn)

    result_rows = []

    for _, event in events_df.iterrows():
        ticker = event["ticker"]
        event_date = event["event_date"]
        surprise_pct = event["surprise_pct"]

        # Guards both "genuinely missing" (NaN) and the pathological case
        # of an infinite surprise_pct (e.g. a would-be division by a zero
        # expected_eps) — either would otherwise flow into the Part 6
        # regression/bucket averages and silently poison them.
        if pd.isna(surprise_pct) or not math.isfinite(surprise_pct):
            print(f"  [WARN] {ticker} {event_date}: no usable surprise_pct (missing or non-finite) — skipping.")
            continue

        stock_df = esu.load_price_series(conn, ticker)
        if stock_df.empty:
            print(f"  [WARN] {ticker} {event_date}: no equity_prices rows for this ticker — skipping.")
            continue

        merged = esu.build_aligned_returns(stock_df, index_df)
        day0_idx = esu.locate_event_index(merged, event_date)
        if day0_idx is None:
            print(f"  [WARN] {ticker} {event_date}: event date is after the latest price data we have — skipping.")
            continue

        ok, reason = esu.validate_data_sufficiency(merged, day0_idx)
        if not ok:
            print(f"  [WARN] {ticker} {event_date}: {reason} — skipping.")
            continue

        beta_info = esu.estimate_beta(merged, day0_idx)
        short_car_df = esu.compute_ar_car(merged, day0_idx, beta_info["alpha"], beta_info["beta"], config.SHORT_WINDOW)
        long_car_df = esu.compute_ar_car(merged, day0_idx, beta_info["alpha"], beta_info["beta"], config.LONG_WINDOW)

        short_window_car = short_car_df["car"].iloc[-1]
        long_window_car = long_car_df["car"].iloc[-1]
        stabilize_day = esu.days_to_stabilize(long_car_df)

        file_stub = f"{ticker.replace('.', '_').replace('&', 'and')}_{event_date}"
        plot_event_car(ticker, event_date, long_car_df, os.path.join(events_dir, f"{file_stub}.png"))
        write_event_summary(
            ticker, event_date, surprise_pct, beta_info,
            short_window_car, long_window_car, stabilize_day,
            os.path.join(events_dir, f"{file_stub}.txt"),
        )

        print(f"  {ticker} {event_date}: beta={beta_info['beta']:.3f} R2={beta_info['r_squared']:.2f} "
              f"short_CAR={short_window_car:+.2%} long_CAR={long_window_car:+.2%} "
              f"stabilize={stabilize_day if stabilize_day is not None else 'never'}")

        result_rows.append({
            "ticker": ticker,
            "event_date": event_date,
            "surprise_pct": surprise_pct,
            "beta": beta_info["beta"],
            "r_squared": beta_info["r_squared"],
            "short_window_car": short_window_car,
            "long_window_car": long_window_car,
            "days_to_stabilize": stabilize_day,
        })

    conn.close()

    if not result_rows:
        print("\nNo events produced results — nothing to write for the combined calibration report.")
        return

    results_df = pd.DataFrame(result_rows)
    results_csv_path = os.path.join(output_dir, "event_results.csv")
    results_df.to_csv(results_csv_path, index=False)
    print(f"\nCombined per-event results written to {results_csv_path}")

    # Part 6: bucket + regression, built from results_df, not a new data pull.
    bucket_df = esu.bucket_events(results_df)
    regression = esu.fit_calibration_regression(results_df)

    bucket_csv_path = os.path.join(output_dir, "calibration_buckets.csv")
    bucket_df.to_csv(bucket_csv_path, index=False)

    plot_calibration_scatter(results_df, bucket_df, regression, os.path.join(output_dir, "calibration_scatter.png"))
    write_calibration_report(results_df, bucket_df, regression, os.path.join(output_dir, "calibration_report.txt"))

    print(f"Calibration bucket table written to {bucket_csv_path}")
    print(f"Calibration scatter chart written to {os.path.join(output_dir, 'calibration_scatter.png')}")
    print(f"Calibration report written to {os.path.join(output_dir, 'calibration_report.txt')}")


def main():
    parser = argparse.ArgumentParser(description="Phase 1: event study engine (market-model CAR + surprise-magnitude calibration)")
    parser.add_argument("--events", default=config.EVENTS_CSV_PATH, help="Path to events.csv (default: %(default)s)")
    parser.add_argument("--output-dir", default=config.REPORTS_DIR, help="Directory to write reports/charts to (default: %(default)s)")
    args = parser.parse_args()

    print(f"Reading events from {args.events} ...")
    run(args.events, args.output_dir)


if __name__ == "__main__":
    main()
