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


# ===========================================================================
# spec_v3 §D "Popular combos" extension — additive only, nothing above this line is modified.
#
# Every function below returns a single bool "state" Series with EXACTLY the same semantics as
# ma_cross_target_state at the top of this file (True=long/False=flat, decided using data up to
# and including bar t) — spec_v3 §D itself specifies "same execution rules as spec_v2 (state at
# close t -> fill at open t+1)", so all seven plug directly into engine.simulate_ma_cross
# unmodified, exactly like every "state"-type registry strategy above. No new engine code is
# needed for this entire extension.
# ===========================================================================

def ema200_macd_target_state(df: pd.DataFrame, n_ema: int = 200) -> pd.Series:
    """POPULAR_COMBOS `ema200_macd`: long when close > EMA(n_ema) AND MACD(12,26,9) line >
    signal; flat when MACD line < signal OR close < EMA(n_ema). The flat condition is exactly the
    De Morgan negation of the long condition (ignoring the knife-edge tie case, which both
    conditions already treat as flat via strict '>'), so — unlike the RSI/Bollinger "hold zone"
    strategies elsewhere in this file — state[t] IS the long condition itself, evaluated fresh
    every bar; no persistence state machine is needed."""
    close = df["close"]
    ema_n = ind.ema(close, n_ema)
    macd_line, signal_line, _hist = ind.macd(close, 12, 26, 9)
    long_cond = (close > ema_n) & (macd_line > signal_line) & ema_n.notna() & macd_line.notna()
    return long_cond.astype(bool)


def rsi_uptrend_target_state(df: pd.DataFrame, n_sma: int = 200) -> pd.Series:
    """POPULAR_COMBOS `rsi_uptrend`: RSI dip in an uptrend. Long when RSI(14) < 30 AND
    close > SMA(n_sma); flat when RSI(14) > 70 OR close < SMA(n_sma). Unlike ema200_macd above,
    the flat trigger is NOT the negation of the long trigger (there is a hold zone — e.g. RSI
    sitting between 30 and 70 while still above the SMA is neither an entry nor an exit signal) —
    genuine hysteresis, via the same _stateful_target helper the registry's own `rsi_mr` uses."""
    close = df["close"]
    rsi14 = ind.rsi_wilder(close, 14)
    sma_n = ind.sma(close, n_sma)
    entry = (rsi14 < 30) & (close > sma_n) & sma_n.notna()
    exit_ = (rsi14 > 70) | ((close < sma_n) & sma_n.notna())
    return _stateful_target(entry, exit_)


def bb_squeeze_target_state(df: pd.DataFrame, n_lookback: int = 120, n_confirm: int = 5) -> pd.Series:
    """POPULAR_COMBOS `bb_squeeze`: bandwidth = (upper-lower)/middle of Bollinger(20,2).
    "squeeze" at bar t means bandwidth[t] is at or below the lowest bandwidth seen over the
    n_lookback bars STRICTLY BEFORE t (shift(1) before the rolling window, so today's own
    bandwidth never contributes to what counts as "tight"). Long when a squeeze was true on any
    of the n_confirm bars STRICTLY BEFORE t (shift(1) again) AND close[t] > upper band[t]; flat
    when close[t] < middle band[t]. Hold zone between breakout and the middle band ->
    _stateful_target."""
    close = df["close"]
    mid, upper, lower = ind.bollinger_bands(close, 20, 2.0)
    bandwidth = (upper - lower) / mid
    prior_min = bandwidth.shift(1).rolling(n_lookback, min_periods=n_lookback).min()
    squeeze = ((bandwidth <= prior_min) & prior_min.notna()).astype(float)
    squeeze_recent = (squeeze.shift(1).rolling(n_confirm, min_periods=1).max().fillna(0.0) > 0.0)
    entry = squeeze_recent & (close > upper) & upper.notna()
    exit_ = (close < mid) & mid.notna()
    return _stateful_target(entry, exit_)


def ichimoku_cloud_target_state(df: pd.DataFrame) -> pd.Series:
    """POPULAR_COMBOS `ichimoku_cloud`: long when close > max(senkou A, senkou B); flat when
    close < min(senkou A, senkou B); hold state while price sits inside the cloud. Both senkou
    spans (indicators.ichimoku_cloud_lines) are already forward-shifted so the cloud value used
    here at bar t was computed entirely from data at positions <= t-26 — see that function's own
    docstring for why the shift itself is the no-lookahead guarantee. Hold zone inside the cloud
    -> _stateful_target."""
    senkou_a, senkou_b = ind.ichimoku_cloud_lines(df)
    close = df["close"]
    # NaN-safe elementwise max/min (senkou_a matures earlier than senkou_b since n_tenkan/n_kijun
    # < n_senkou_b, so there is a stretch where one span is valid and the other still NaN;
    # DataFrame.max/min(axis=1, skipna=True) treats that correctly regardless of which side is
    # NaN, unlike Series.combine(..., max)/(..., min), whose result for a (valid, NaN) pair
    # depends on argument ORDER because Python's builtin max/min don't handle NaN symmetrically).
    both = pd.concat([senkou_a, senkou_b], axis=1)
    cloud_top = both.max(axis=1, skipna=True)
    cloud_bottom = both.min(axis=1, skipna=True)
    entry = (close > cloud_top) & cloud_top.notna()
    exit_ = (close < cloud_bottom) & cloud_bottom.notna()
    return _stateful_target(entry, exit_)


def heikin_ashi_trend_target_state(df: pd.DataFrame, n_confirm: int = 2) -> pd.Series:
    """POPULAR_COMBOS `heikin_ashi_trend`: long after n_confirm CONSECUTIVE Heikin-Ashi bars with
    HA close > HA open; flat on the first HA bar with HA close < HA open. A run of only
    n_confirm-1 bullish HA bars is not yet a signal (hold zone) -> _stateful_target.
    indicators.heikin_ashi is the one recursive (not merely rolling-window) computation behind
    this strategy — its own no-lookahead property is what tests.py checks directly, since a bug
    there could otherwise hide behind this function's own causal-looking boolean algebra."""
    ha_open, ha_close = ind.heikin_ashi(df)
    bullish = ha_close > ha_open
    bearish = ha_close < ha_open
    entry = bullish.copy()
    for i in range(1, n_confirm):
        entry = entry & bullish.shift(i).fillna(False)
    return _stateful_target(entry, bearish)


def supertrend_ema200_target_state(df: pd.DataFrame, n_ema: int = 200) -> pd.Series:
    """POPULAR_COMBOS `supertrend_ema200`: long when Supertrend(10,3) direction is up AND
    close > EMA(n_ema); flat SPECIFICALLY on the bar Supertrend flips from up to down — not on
    every bar the direction merely reads "down" already, and not when price alone dips below the
    EMA while Supertrend is still up (spec_v3 §D's own wording is asymmetric between the entry
    and exit conditions; followed literally here). Hysteresis -> _stateful_target."""
    direction = ind.supertrend(df, 10, 3.0)
    close = df["close"]
    ema_n = ind.ema(close, n_ema)
    up = (direction == "up")
    entry = up & (close > ema_n) & ema_n.notna()
    flipped_down = (direction == "down") & (direction.shift(1) == "up")
    return _stateful_target(entry, flipped_down)
