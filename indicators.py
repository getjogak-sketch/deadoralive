"""
indicators.py — pure indicator functions, all operating on the FULL price series.

Hard rule (spec_v2 §3 / §6): every value at position t must depend only on data at positions
<= t (a "no-lookahead" property, verified by tests.py for every strategy in registry.py that
uses one of these). None of these functions ever look at df.iloc[t+1:] to produce value[t] —
each is either a fixed backward-looking rolling window, a recursive/exponential smoother seeded
from the start of the series, or an explicit .shift(1).

All formulas below are the standard textbook/industry definitions — nothing here is a novel or
tuned variant.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average: mean of the trailing n values (including t). NaN until n values
    are available (min_periods=n)."""
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """
    Exponential moving average, pandas' ewm(span=n, adjust=False) convention:
      alpha = 2 / (n + 1)
      EMA[0] = series[0]
      EMA[t] = alpha * series[t] + (1 - alpha) * EMA[t-1]   for t > 0
    This is a pure recursive (causal) filter: EMA[t] depends only on series[0..t]. adjust=False
    is used deliberately (vs. the default adjust=True) because it matches the recursive
    definition used by every popular charting platform (TradingView, etc.), not the
    weighted-average-of-all-history-with-no-decay-correction variant adjust=True computes for
    the early part of the series.
    """
    return series.ewm(span=n, adjust=False).mean()


def rsi_wilder(close: pd.Series, n: int) -> pd.Series:
    """
    RSI using Wilder's original smoothing (the textbook/classic definition — NOT the "SMA of
    gains/losses" variant some tutorials substitute).

    delta[t]  = close[t] - close[t-1]
    gain[t]   = max(delta[t], 0);  loss[t] = max(-delta[t], 0)
    Seed (t = n):      avg_gain[n] = mean(gain[1..n]);  avg_loss[n] = mean(loss[1..n])
    Recursive (t > n): avg_gain[t] = (avg_gain[t-1] * (n-1) + gain[t]) / n
                        avg_loss[t] = (avg_loss[t-1] * (n-1) + loss[t]) / n
    RS[t]  = avg_gain[t] / avg_loss[t]
    RSI[t] = 100 - 100 / (1 + RS[t])

    Edge cases: avg_loss[t] == 0 and avg_gain[t] > 0 -> RSI = 100 (pure uptrend, no losses to
    smooth). avg_gain[t] == 0 and avg_loss[t] == 0 (perfectly flat close) -> RSI = 50 by
    convention (no directional information at all).

    Implemented as an explicit forward loop (not pandas .ewm) because Wilder's seed — a simple
    mean of the first n gain/loss values — differs from ewm(alpha=1/n, adjust=False)'s seed
    (which starts smoothing from the very first observation); using the wrong seed would silently
    produce a slightly different number from every published RSI reference table / spot check.
    """
    n = int(n)
    delta = close.diff()
    gain = delta.clip(lower=0.0).to_numpy(dtype=float)
    loss = (-delta.clip(upper=0.0)).to_numpy(dtype=float)
    m = len(close)
    avg_gain = np.full(m, np.nan)
    avg_loss = np.full(m, np.nan)

    if m > n:
        avg_gain[n] = np.nanmean(gain[1:n + 1])
        avg_loss[n] = np.nanmean(loss[1:n + 1])
        for t in range(n + 1, m):
            avg_gain[t] = (avg_gain[t - 1] * (n - 1) + gain[t]) / n
            avg_loss[t] = (avg_loss[t - 1] * (n - 1) + loss[t]) / n

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100.0 - 100.0 / (1.0 + rs)

    rsi = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, rsi)
    rsi = np.where((avg_loss == 0) & (avg_gain == 0), 50.0, rsi)
    return pd.Series(rsi, index=close.index)


def bollinger_bands(close: pd.Series, n: int = 20, k: float = 2.0):
    """
    Bollinger Bands: n-period SMA as the middle band, +/- k * (rolling POPULATION standard
    deviation, ddof=0 — i.e. pandas .std(ddof=0), NOT the sample std (ddof=1) pandas' .rolling
    .std() defaults to). Population std is used here because Bollinger's own original definition
    divides by n, not n-1; we call this out explicitly since it is the single most common
    source of off-by-a-few-percent discrepancies between Bollinger Band implementations.

    Returns (middle, upper, lower), each a pd.Series aligned to close's index. All three are NaN
    until n values are available.
    """
    middle = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper = middle + k * std
    lower = middle - k * std
    return middle, upper, lower


def macd(close: pd.Series, n_fast: int = 12, n_slow: int = 26, n_signal: int = 9):
    """
    MACD, standard definition:
      macd_line[t]   = EMA(close, n_fast)[t] - EMA(close, n_slow)[t]
      signal_line[t] = EMA(macd_line, n_signal)[t]
      histogram[t]   = macd_line[t] - signal_line[t]
    All EMAs use ema() above (ewm(adjust=False)), so every value is causal.
    Returns (macd_line, signal_line, histogram).
    """
    macd_line = ema(close, n_fast) - ema(close, n_slow)
    signal_line = ema(macd_line, n_signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr_wilder(df: pd.DataFrame, n: int = 10) -> pd.Series:
    """
    Average True Range, Wilder's smoothing (same recursive scheme as rsi_wilder's avg_gain/loss).
      TR[t] = max(high[t]-low[t], |high[t]-close[t-1]|, |low[t]-close[t-1]|)   (TR[0] = high[0]-low[0])
      Seed (t = n):      ATR[n] = mean(TR[1..n])
      Recursive (t > n): ATR[t] = (ATR[t-1] * (n-1) + TR[t]) / n
    """
    n = int(n)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    m = len(df)

    tr = np.empty(m)
    tr[0] = high[0] - low[0]
    prev_close = close[:-1]
    tr[1:] = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])

    atr = np.full(m, np.nan)
    if m > n:
        atr[n] = np.mean(tr[1:n + 1])
        for t in range(n + 1, m):
            atr[t] = (atr[t - 1] * (n - 1) + tr[t]) / n
    return pd.Series(atr, index=df.index)


def supertrend(df: pd.DataFrame, n: int = 10, multiplier: float = 3.0):
    """
    Supertrend, the standard ATR-band-flip definition (as popularized on TradingView / most
    retail charting platforms):
      mid[t]            = (high[t] + low[t]) / 2
      basic_upper[t]     = mid[t] + multiplier * ATR(n)[t]
      basic_lower[t]     = mid[t] - multiplier * ATR(n)[t]
      final_upper[t]     = basic_upper[t]  if basic_upper[t] < final_upper[t-1] or close[t-1] > final_upper[t-1]
                            else final_upper[t-1]
      final_lower[t]     = basic_lower[t]  if basic_lower[t] > final_lower[t-1] or close[t-1] < final_lower[t-1]
                            else final_lower[t-1]
      direction[t] = "up"   if close[t] > final_upper[t-1]
                     "down" if close[t] < final_lower[t-1]
                     else direction[t-1]   (no flip -> carry previous direction forward)
    direction[t] depends only on final_upper/final_lower up to t (themselves depending only on
    ATR/high/low/close up to t) and close[t] itself — causal throughout.

    Returns a pd.Series of dtype object with values in {"up", "down", None} (None during ATR
    warm-up, before the bands are defined).
    """
    n = int(n)
    atr = atr_wilder(df, n).to_numpy()
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    m = len(df)
    mid = (high + low) / 2.0
    basic_upper = mid + multiplier * atr
    basic_lower = mid - multiplier * atr

    final_upper = np.full(m, np.nan)
    final_lower = np.full(m, np.nan)
    direction = np.array([None] * m, dtype=object)

    first_valid = np.flatnonzero(~np.isnan(atr))
    if len(first_valid) == 0:
        return pd.Series(direction, index=df.index)
    start = int(first_valid[0])

    final_upper[start] = basic_upper[start]
    final_lower[start] = basic_lower[start]
    # Seed direction by comparing the first available bar to its own band (no prior direction
    # to carry forward yet); this only affects a single warm-up bar.
    direction[start] = "down" if close[start] <= final_upper[start] else "up"

    for t in range(start + 1, m):
        if basic_upper[t] < final_upper[t - 1] or close[t - 1] > final_upper[t - 1]:
            final_upper[t] = basic_upper[t]
        else:
            final_upper[t] = final_upper[t - 1]

        if basic_lower[t] > final_lower[t - 1] or close[t - 1] < final_lower[t - 1]:
            final_lower[t] = basic_lower[t]
        else:
            final_lower[t] = final_lower[t - 1]

        if close[t] > final_upper[t - 1]:
            direction[t] = "up"
        elif close[t] < final_lower[t - 1]:
            direction[t] = "down"
        else:
            direction[t] = direction[t - 1]

    return pd.Series(direction, index=df.index)


# ===========================================================================
# spec_v3 §D "Popular combos" extension — additive only, nothing above this line is modified.
# ===========================================================================

def heikin_ashi(df: pd.DataFrame):
    """
    Heikin-Ashi open/close, the standard recursive definition:
      ha_close[t] = (open[t] + high[t] + low[t] + close[t]) / 4
      ha_open[0]  = (open[0] + close[0]) / 2                        (seed — no prior HA bar)
      ha_open[t]  = (ha_open[t-1] + ha_close[t-1]) / 2               for t > 0

    Same causal-recursive shape as ema()/rsi_wilder()/atr_wilder() above: ha_open[t] depends only
    on ha_open[t-1] and ha_close[t-1] (themselves built only from data at positions < t), plus
    bar t's own raw OHLC for ha_close[t] itself. Implemented as an explicit forward loop (not a
    vectorized pandas op) for the same reason rsi_wilder/atr_wilder are — it is a genuine
    recursion, not a rolling window. HA high/low are not returned: no strategy here uses them.

    Returns (ha_open, ha_close), each a pd.Series aligned to df's index.
    """
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    m = len(df)

    ha_close = (o + h + l + c) / 4.0
    ha_open = np.full(m, np.nan)
    if m:
        ha_open[0] = (o[0] + c[0]) / 2.0
        for t in range(1, m):
            ha_open[t] = (ha_open[t - 1] + ha_close[t - 1]) / 2.0

    return pd.Series(ha_open, index=df.index), pd.Series(ha_close, index=df.index)


def ichimoku_cloud_lines(df: pd.DataFrame, n_tenkan: int = 9, n_kijun: int = 26,
                          n_senkou_b: int = 52, cloud_shift: int = 26):
    """
    Ichimoku Kinko Hyo's two "cloud" (kumo) lines, senkou span A and B — ALREADY forward-shifted
    by `cloud_shift` bars so that the value returned at bar t is exactly what a strategy may
    causally compare bar t's own close against (spec_v3 §D: "cloud value at bar t was computed
    from bars <= t-26 -- no lookahead"). This is the one place in this indicator where the shift
    IS the no-lookahead guarantee, not an afterthought — a real Ichimoku chart plots the cloud 26
    bars ahead of where it was computed for exactly this reason (it's a forecast overlay), so
    "the cloud value at today's bar" on a live chart is, mechanically, a `cloud_shift`-bars-old
    computation; this function returns it already aligned that way rather than making every
    caller shift it themselves.

      tenkan_raw[t]   = (highest high, n_tenkan bars incl. t) + (lowest low, n_tenkan bars incl. t)) / 2
      kijun_raw[t]    = (highest high, n_kijun bars incl. t)  + (lowest low, n_kijun bars incl. t))  / 2
      senkou_a_raw[t] = (tenkan_raw[t] + kijun_raw[t]) / 2
      senkou_b_raw[t] = (highest high, n_senkou_b bars incl. t) + (lowest low, n_senkou_b bars incl. t)) / 2
      senkou_a[t] = senkou_a_raw[t - cloud_shift]     (via .shift(cloud_shift))
      senkou_b[t] = senkou_b_raw[t - cloud_shift]

    tenkan/kijun's own rolling windows include the current bar (unlike donchian() above, which
    deliberately excludes it) — this matches Ichimoku's own standard definition, and causality is
    unaffected either way (a window ending at t, inclusive, still only uses data <= t).

    Returns (senkou_a, senkou_b), each a pd.Series aligned to df's index, both already shifted.
    """
    high, low = df["high"], df["low"]

    def _mid_channel(n):
        return (high.rolling(n, min_periods=n).max() + low.rolling(n, min_periods=n).min()) / 2.0

    tenkan_raw = _mid_channel(n_tenkan)
    kijun_raw = _mid_channel(n_kijun)
    senkou_a_raw = (tenkan_raw + kijun_raw) / 2.0
    senkou_b_raw = _mid_channel(n_senkou_b)

    return senkou_a_raw.shift(cloud_shift), senkou_b_raw.shift(cloud_shift)


def donchian(df: pd.DataFrame, n_high: int, n_low: int):
    """
    Donchian channel, using the PRIOR n bars only (i.e. shift(1) before the rolling window, so
    the bar t value never includes bar t's own high/low — the standard "breakout above the prior
    N-bar range" definition, as opposed to a channel that includes the current bar):
      upper[t] = max(high[t-n_high .. t-1])
      lower[t] = min(low[t-n_low .. t-1])
    Returns (upper, lower).
    """
    upper = df["high"].shift(1).rolling(n_high, min_periods=n_high).max()
    lower = df["low"].shift(1).rolling(n_low, min_periods=n_low).min()
    return upper, lower
