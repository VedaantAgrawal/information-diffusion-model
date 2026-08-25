"""
event_study_utils.py — pure computation for Phase 1 (the event study engine).

WHY THIS IS SEPARATE FROM 04_event_study.py:
Same split as config.py/db_utils.py vs. the numbered pipeline scripts:
this file holds the math (loading price series, estimating beta, computing
abnormal returns, bucketing) with no printing, plotting, or file I/O, and
04_event_study.py is the thin orchestration layer that calls these
functions and writes the reports/charts. Keeping the math side-effect-free
makes it possible to test and reason about independently of what the
report output looks like.

FRAMING REMINDER (see PHASE_1_HANDOFF_PROMPT.md and README_phase1.md for
the full version): everything here calibrates a HISTORICAL relationship
between earnings-surprise magnitude and price reaction magnitude. None of
it predicts what a specific future stock move will be.
"""

import numpy as np
import pandas as pd
from scipy import stats

import config


# ---------------------------------------------------------------------------
# Part 1 — Loading events and price series
# ---------------------------------------------------------------------------

def load_events(csv_path: str) -> pd.DataFrame:
    """
    Read events.csv and fill in surprise_pct wherever it's missing but
    actual_eps/expected_eps are present.

    surprise_pct = (actual_eps - expected_eps) / abs(expected_eps) * 100

    Rows may instead supply surprise_pct directly (with actual_eps/
    expected_eps left blank) when only a rough "beat by ~X%" figure is
    known from news coverage rather than clean EPS numbers — both paths
    are valid per-row, so we only compute surprise_pct where it's absent.
    """
    df = pd.read_csv(csv_path)

    required_cols = {"ticker", "event_date", "event_type", "actual_eps",
                      "expected_eps", "surprise_pct", "notes"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"events.csv is missing required column(s): {sorted(missing_cols)}")

    computed_mask = df["surprise_pct"].isna() & df["actual_eps"].notna() & df["expected_eps"].notna()
    df.loc[computed_mask, "surprise_pct"] = (
        (df.loc[computed_mask, "actual_eps"] - df.loc[computed_mask, "expected_eps"])
        / df.loc[computed_mask, "expected_eps"].abs()
        * 100
    )

    return df


def load_price_series(conn, ticker: str) -> pd.DataFrame:
    """Return this ticker's equity_prices rows as a date-sorted DataFrame of (date, close)."""
    return pd.read_sql_query(
        "SELECT date, close FROM equity_prices WHERE ticker = ? ORDER BY date ASC",
        conn,
        params=(ticker,),
    )


def load_index_series(conn, index_name: str = None) -> pd.DataFrame:
    """Return the market index's index_prices rows as a date-sorted DataFrame of (date, close)."""
    index_name = index_name or config.MARKET_INDEX_NAME
    return pd.read_sql_query(
        "SELECT date, close FROM index_prices WHERE index_name = ? ORDER BY date ASC",
        conn,
        params=(index_name,),
    )


def build_aligned_returns(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
    """
    Inner-join the stock and index price series on date, then compute daily
    simple returns for both.

    Inner join (not asof/outer) is deliberate: a market-model regression
    needs the stock's return and the index's return on the SAME trading
    day. An inner join naturally handles the (rare) case where one series
    has a trading day the other doesn't, by simply dropping that day from
    both, rather than requiring special-case calendar logic.

    The returned DataFrame's row position (0, 1, 2, ...) IS the trading-day
    index used everywhere else in this module — "day offset -2" means
    "2 rows before the event row", not "2 calendar days before".
    """
    merged = pd.merge(stock_df, index_df, on="date", suffixes=("_stock", "_index"))
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["stock_ret"] = merged["close_stock"].pct_change()
    merged["index_ret"] = merged["close_index"].pct_change()
    return merged


def locate_event_index(merged_df: pd.DataFrame, event_date: str):
    """
    Find the row position of "day 0" for this event: the first trading day
    on or after event_date.

    WHY "on or after" rather than an exact date match: Indian earnings are
    routinely announced after market close, or on a non-trading day
    (weekend board meeting). Treating the next available trading day as
    day 0 is a simplifying convention (documented in README_phase1.md) —
    it means day 0 is the first day the market could plausibly have
    reacted, which is the quantity we actually care about.

    Returns None if event_date is after the last date we have prices for.
    """
    candidates = merged_df.index[merged_df["date"] >= event_date]
    if len(candidates) == 0:
        return None
    return int(candidates[0])


def validate_data_sufficiency(merged_df: pd.DataFrame, day0_idx: int) -> tuple:
    """
    Check that there's enough aligned price history around day0_idx to
    both estimate beta (Part 2) and compute the long-window CAR (Part 3).

    Returns (True, "") if sufficient, or (False, reason) if not — callers
    are expected to skip the event with a warning on failure, never crash
    the whole run over one bad event (see PART 1 of the handoff prompt).
    """
    estimation_end = day0_idx - config.ESTIMATION_BUFFER_DAYS
    estimation_start = estimation_end - config.ESTIMATION_WINDOW_DAYS

    if estimation_start < 1:
        # Row 0's return is NaN (nothing to diff against), so the
        # estimation window must start at row 1 or later.
        return False, (
            f"not enough pre-event history for a {config.ESTIMATION_WINDOW_DAYS}-day "
            f"estimation window ending {config.ESTIMATION_BUFFER_DAYS} days before the event"
        )

    long_start = day0_idx + config.LONG_WINDOW[0]
    long_end = day0_idx + config.LONG_WINDOW[1]

    if long_start < 1:
        return False, "long window's start offset falls before the start of available price history"

    if long_end >= len(merged_df):
        return False, (
            f"not enough post-event history for the long window "
            f"(need data through day {config.LONG_WINDOW[1]} after the event)"
        )

    return True, ""


# ---------------------------------------------------------------------------
# Part 2 — Beta estimation (market model)
# ---------------------------------------------------------------------------

def estimate_beta(merged_df: pd.DataFrame, day0_idx: int) -> dict:
    """
    OLS-regress the stock's daily return on the index's daily return over
    the estimation window (ending ESTIMATION_BUFFER_DAYS trading days
    before the event, spanning ESTIMATION_WINDOW_DAYS days before that —
    see config.py for why).

    Returns alpha, beta, r_squared, and n_obs. R² is surfaced explicitly
    (not just computed and discarded) because a low R² means the beta
    estimate — and therefore every abnormal return computed from it — is
    less trustworthy. Downstream reports must not bury this number.
    """
    estimation_end = day0_idx - config.ESTIMATION_BUFFER_DAYS
    estimation_start = estimation_end - config.ESTIMATION_WINDOW_DAYS

    window = merged_df.iloc[estimation_start:estimation_end]
    x = window["index_ret"].to_numpy()
    y = window["stock_ret"].to_numpy()

    result = stats.linregress(x, y)

    return {
        "alpha": result.intercept,
        "beta": result.slope,
        "r_squared": result.rvalue ** 2,
        "n_obs": len(window),
    }


# ---------------------------------------------------------------------------
# Part 3 — Abnormal and cumulative abnormal returns
# ---------------------------------------------------------------------------

def compute_ar_car(merged_df: pd.DataFrame, day0_idx: int, alpha: float, beta: float, window: tuple) -> pd.DataFrame:
    """
    Compute daily Abnormal Return and running Cumulative Abnormal Return
    for every trading day in `window` (a (start_offset, end_offset) tuple
    of trading-day offsets relative to day0_idx, e.g. (-2, 40)).

    AR(t)  = actual stock return(t) - (alpha + beta * index return(t))
    CAR(t) = running sum of AR from the start of the window through t

    Returns a DataFrame with one row per trading day in the window:
    offset (trading days relative to the event), date, ar, car.
    """
    start_idx = day0_idx + window[0]
    end_idx = day0_idx + window[1]

    rows = merged_df.iloc[start_idx:end_idx + 1].copy()
    rows["offset"] = range(window[0], window[1] + 1)
    rows["ar"] = rows["stock_ret"] - (alpha + beta * rows["index_ret"])
    rows["car"] = rows["ar"].cumsum()

    return rows[["offset", "date", "ar", "car"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Part 4 — Diffusion speed metric
# ---------------------------------------------------------------------------

def days_to_stabilize(car_df: pd.DataFrame) -> "int | None":
    """
    Find the smallest offset t >= 0 (i.e. on/after the event day) such
    that CAR stays within STABILIZE_PCT_THRESHOLD of the window's FINAL
    CAR value for at least STABILIZE_CONSECUTIVE_DAYS consecutive trading
    days starting at t.

    Returns that offset (an int, "N trading days after the event"), or
    None if CAR never stabilizes within the window — itself a meaningful
    finding (evidence of extended drift), not a failure, so callers should
    report it explicitly rather than treating None as an error.

    `car_df` is expected to be the LONG-window CAR series (compute_ar_car
    with config.LONG_WINDOW) — "final value" means CAR at the end of that
    window.
    """
    final_car = car_df["car"].iloc[-1]
    car_values = car_df["car"].to_numpy()
    offsets = car_df["offset"].to_numpy()

    # A near-zero final CAR would make a *relative* (percent-of-final)
    # threshold either trivially satisfied or wildly unstable, since
    # dividing by ~0 blows up. Fall back to a small absolute threshold in
    # that edge case instead.
    if abs(final_car) < 1e-6:
        tolerance = 0.01  # 1 percentage point of CAR, in absolute terms
    else:
        tolerance = abs(final_car) * config.STABILIZE_PCT_THRESHOLD

    post_event_mask = offsets >= 0
    post_event_offsets = offsets[post_event_mask]
    post_event_car = car_values[post_event_mask]

    n = config.STABILIZE_CONSECUTIVE_DAYS
    for i in range(len(post_event_car) - n + 1):
        run = post_event_car[i:i + n]
        if np.all(np.abs(run - final_car) <= tolerance):
            return int(post_event_offsets[i])

    return None


# ---------------------------------------------------------------------------
# Part 6 — Surprise-to-reaction calibration
# ---------------------------------------------------------------------------

def bucket_events(results_df: pd.DataFrame) -> pd.DataFrame:
    """
    Group events into fixed surprise_pct bands (config.SURPRISE_BUCKET_EDGES/
    LABELS — see config.py for why fixed bands rather than quintiles at this
    sample size) and summarize each bucket's short/long-window CAR.

    NOTE ON SAMPLE SIZE: once the event list grows into the hundreds,
    switching SURPRISE_BUCKET_EDGES/LABELS to quintile cut points
    (pd.qcut on surprise_pct instead of pd.cut on fixed edges) would be
    the more academically standard approach — this is a one-line swap
    when that day comes, left as fixed bands for now because quintiles
    on a handful of events would put ~1-2 events per bucket and shuffle
    the boundaries every time a new event is added.
    """
    df = results_df.copy()
    df["bucket"] = pd.cut(
        df["surprise_pct"],
        bins=config.SURPRISE_BUCKET_EDGES,
        labels=config.SURPRISE_BUCKET_LABELS,
    )

    summary = df.groupby("bucket", observed=False).agg(
        n_events=("surprise_pct", "count"),
        avg_short_window_car=("short_window_car", "mean"),
        avg_long_window_car=("long_window_car", "mean"),
        long_window_car_std=("long_window_car", "std"),
        long_window_car_min=("long_window_car", "min"),
        long_window_car_max=("long_window_car", "max"),
    ).reset_index()

    return summary


def fit_calibration_regression(results_df: pd.DataFrame) -> dict:
    """
    Simple linear regression of long-window CAR against surprise_pct
    across all events: "each 1% of earnings surprise has historically
    corresponded to roughly `slope`% of cumulative abnormal return."

    Returns slope, intercept, r_squared, and n_obs. Like beta's R² in
    Part 2, r_squared here must be reported alongside the slope, not
    hidden — a low R² means the linear fit explains little of the
    event-to-event variation, which is expected and worth stating
    plainly at small sample sizes.
    """
    x = results_df["surprise_pct"].to_numpy()
    y = results_df["long_window_car"].to_numpy()

    result = stats.linregress(x, y)

    return {
        "slope": result.slope,
        "intercept": result.intercept,
        "r_squared": result.rvalue ** 2,
        "n_obs": len(results_df),
    }
