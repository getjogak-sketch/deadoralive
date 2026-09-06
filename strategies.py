"""
strategies.py — signal computation only (pandas), pure functions of the FULL price series.

No execution/equity logic here (that's engine.py) and no lookahead: every value at position t
must depend only on data at positions <= t. This property is what tests.py verifies.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# Fixed parameter grids per spec §4 — do not add combinations.
MA_CROSS_PARAMS = [(10, 50), (20, 100), (50, 200)]
VOL_BREAKOUT_PARAMS = [0.5, 0.7]


def ma_cross_target_state(close: pd.Series, n_fast: int, n_slow: int) -> pd.Series:
    """
    Target state per spec §4A: state decided at close of bar t.
    fast[t] = SMA(close, n_fast) up to and including bar t.
    slow[t] = SMA(close, n_slow) up to and including bar t.
    target_state[t] = True (long) if fast[t] > slow[t], else False (flat).
    Warm-up (slow SMA is NaN) => False (flat) — NaN comparisons in pandas/numpy are already False,
    so this falls out naturally, but we make it explicit for clarity.
    """
    fast = close.rolling(n_fast, min_periods=n_fast).mean()
    slow = close.rolling(n_slow, min_periods=n_slow).mean()
    state = (fast > slow) & slow.notna() & fast.notna()
    return state.astype(bool)


def vol_breakout_targets(df: pd.DataFrame, k: float):
    """
    Per spec §4B, computed on the FULL series:
      range_prev[t] = high[t-1] - low[t-1]
      target[t]     = open[t] + k * range_prev[t]
      triggered[t]  = high[t] >= target[t]
    First bar has no t-1 => range_prev/target are NaN => triggered is False there.
    Returns (target, triggered) as pandas Series aligned to df's index.
    """
    range_prev = (df["high"] - df["low"]).shift(1)
    target = df["open"] + k * range_prev
    triggered = (df["high"] >= target).fillna(False)
    return target, triggered


# ===========================================================================
# netcheck (spec_v2 §3) extension — additive only, nothing above this line is modified.
#
# Every function below follows one of the two v1 "shapes" the spec_v2 registry maps onto:
#   - "state" strategies return a single bool Series `target_state` with EXACTLY the same
#     semantics as ma_cross_target_state above (True=long/False=flat, decided using data up to
#     and including bar t) — they plug directly into engine.simulate_ma_cross, unmodified.
#   - "one-bar" strategies return (target, triggered) with the same semantics as
#     vol_breakout_targets above — they plug directly into engine.simulate_vol_breakout.
#   - "hold-N-bar" and "reference" strategies (dip_pct, dca_weekly) need a new engine primitive,
#     added in engine.py, since neither v1 shape fits (see spec_v2 §3, "보유 N bar형").
#
# No new comparison thresholds/lookback windows are invented here beyond registry.py's table —
# every constant used below (30/70, 10, 200, 5, 20/2, 12/26/9, 10/3, -5%, ...) is copied from the
# spec_v2 §3 registry table.
# ===========================================================================

import indicators as ind


def _stateful_target(entry_trigger: pd.Series, exit_trigger: pd.Series) -> pd.Series:
    """
    Generic long/flat state machine for strategies whose entry and exit conditions are not
    complements of a single comparison (so the simple `(a > b)` boolean-Series trick
    ma_cross_target_state uses doesn't apply): the position PERSISTS until the opposite trigger
    fires.
        state[t] = True   if (state[t-1] and not exit_trigger[t]) or (not state[t-1] and entry_trigger[t])
                 = False  otherwise
    entry_trigger[t] and exit_trigger[t] must themselves already be causal (depend only on data
    at positions <= t); this function only adds persistence on top, so it inherits the
    no-lookahead property of its inputs bar-for-bar (state[t] is a pure function of
    state[t-1], entry_trigger[t], exit_trigger[t] — never of anything at t+1 or later).
    If both triggers are simultaneously True (not expected for any strategy below, since exit
    conditions are only evaluated while already long, and vice versa), exit takes priority.
    """
    entry = entry_trigger.fillna(False).to_numpy()
    exitt = exit_trigger.fillna(False).to_numpy()
    n = len(entry)
    state = np.zeros(n, dtype=bool)
    cur = False
    for t in range(n):
        cur = (not exitt[t]) if cur else bool(entry[t])
        state[t] = cur
    return pd.Series(state, index=entry_trigger.index)


# --- state-type strategies -------------------------------------------------

def ema_cross_target_state(close: pd.Series, n_fast: int, n_slow: int) -> pd.Series:
    """registry `ema_cross`: EMA(n_fast) > EMA(n_slow) -> long."""
    fast = ind.ema(close, n_fast)
    slow = ind.ema(close, n_slow)
    return ((fast > slow) & fast.notna() & slow.notna()).astype(bool)


def above_sma_target_state(close: pd.Series, n: int) -> pd.Series:
    """registry `above_sma`: close > SMA(n) -> long."""
    s = ind.sma(close, n)
    return ((close > s) & s.notna()).astype(bool)


def donchian_target_state(df: pd.DataFrame, n_high: int, n_low: int) -> pd.Series:
    """registry `donchian`: close breaks above the prior n_high-bar high -> long; close breaks
    below the prior n_low-bar low -> flat. Both bands already use shift(1) (indicators.donchian),
    so the entry/exit triggers themselves are causal before the state machine is even applied."""
    upper, lower = ind.donchian(df, n_high, n_low)
    entry = (df["close"] > upper) & upper.notna()
    exit_ = (df["close"] < lower) & lower.notna()
    return _stateful_target(entry, exit_)


def rsi_mr_target_state(close: pd.Series, exit_level: float, n: int = 14) -> pd.Series:
    """registry `rsi_mr`: RSI(14) < 30 -> long; RSI(14) > exit_level -> flat."""
    rsi = ind.rsi_wilder(close, n)
    entry = rsi < 30
    exit_ = rsi > exit_level
    return _stateful_target(entry, exit_)


def rsi2_connors_target_state(df: pd.DataFrame) -> pd.Series:
    """registry `rsi2_connors` (fixed params): RSI(2) < 10 AND close > SMA(200) -> long;
    close > SMA(5) -> flat."""
    close = df["close"]
    rsi2 = ind.rsi_wilder(close, 2)
    sma200 = ind.sma(close, 200)
    sma5 = ind.sma(close, 5)
    entry = (rsi2 < 10) & (close > sma200) & sma200.notna()
    exit_ = (close > sma5) & sma5.notna()
    return _stateful_target(entry, exit_)


def bb_mr_target_state(close: pd.Series) -> pd.Series:
    """registry `bb_mr` (fixed 20,2): close < lower band -> long; close > middle band -> flat."""
    mid, upper, lower = ind.bollinger_bands(close, 20, 2.0)
    entry = (close < lower) & lower.notna()
    exit_ = (close > mid) & mid.notna()
    return _stateful_target(entry, exit_)


def bb_breakout_target_state(close: pd.Series) -> pd.Series:
    """registry `bb_breakout` (fixed 20,2): close > upper band -> long; close < middle band -> flat."""
    mid, upper, lower = ind.bollinger_bands(close, 20, 2.0)
    entry = (close > upper) & upper.notna()
    exit_ = (close < mid) & mid.notna()
    return _stateful_target(entry, exit_)


def macd_target_state(close: pd.Series) -> pd.Series:
    """registry `macd` (fixed 12,26,9): MACD line > signal line -> long."""
    macd_line, signal_line, _hist = ind.macd(close, 12, 26, 9)
    return ((macd_line > signal_line) & macd_line.notna() & signal_line.notna()).astype(bool)


def supertrend_target_state(df: pd.DataFrame, n: int = 10, multiplier: float = 3.0) -> pd.Series:
    """registry `supertrend` (fixed 10,3): Supertrend direction == up -> long."""
    direction = ind.supertrend(df, n, multiplier)
    return (direction == "up")


def tsmom_target_state(close: pd.Series, n: int) -> pd.Series:
    """registry `tsmom`: close[t] > close[t-n] -> long (time-series momentum)."""
    ref = close.shift(n)
    return ((close > ref) & ref.notna()).astype(bool)


def dip_3down_target_state(close: pd.Series) -> pd.Series:
    """registry `dip_3down` (fixed): 3 consecutive down closes -> long; first up close -> flat."""
    diff = close.diff()
    down = diff < 0
    entry = down & down.shift(1).fillna(False) & down.shift(2).fillna(False)
    exit_ = diff > 0
    return _stateful_target(entry, exit_)


# --- one-bar-type strategies ------------------------------------------------

def vol_breakout_trend_targets(df: pd.DataFrame, k: float, sma_n: int = 20):
    """registry `vol_breakout_trend` (fixed k=0.5, sma_n=20): v1 vol_breakout trigger, additionally
    gated on open[t] > SMA(sma_n)[t-1] (trend filter uses only bars strictly before t, via
    .shift(1), so the filter itself adds no lookahead on top of vol_breakout_targets)."""
    target, triggered = vol_breakout_targets(df, k)
    sma_prev = ind.sma(df["close"], sma_n).shift(1)
    triggered_trend = triggered & (df["open"] > sma_prev) & sma_prev.notna()
    return target, triggered_trend


# --- hold-N-bar-type strategy ------------------------------------------------

def dip_pct_entry_trigger(close: pd.Series, threshold: float = -0.05) -> pd.Series:
    """registry `dip_pct`: bar close-to-close return <= threshold (-5%) -> entry signal at bar t
    (executed at bar t+1's open by the engine, same next-bar-open convention as every other
    strategy here)."""
    bar_return = close / close.shift(1) - 1.0
    return ((bar_return <= threshold) & bar_return.notna()).astype(bool)
