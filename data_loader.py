"""
data_loader.py — CSV load, partial-bar drop, period split.

Per spec §1/§2:
- Drop the last row of the BTC files (2026-09-06, partial bar). SPY file already ends 2026-03-20
  (a full closed day per README), so nothing is dropped there.
- Indicators/signals must be computed on the FULL series (warm-up uses data before the IS start);
  only trade/metric aggregation is sliced to IS / OOS windows. This module only loads and defines
  the windows — it does not truncate the price series to a period.
"""
from __future__ import annotations
import pandas as pd

import os
DATA_DIR = "/home/claude/data"   # v1 dev-box location (Bitstamp/SPY files)

# Production fallback (GitHub Actions): the repo's own data/ dir, filled by fetch_data.py.
_REPO_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PROD_FILES = {
    ("btc", "1d"): "BTCUSD_1d.csv",
    ("btc", "4h"): "BTCUSD_4h.csv",
}


def resolve_path(asset: str, timeframe: str):
    """Return the first existing CSV for (asset, tf): v1 dev path, else repo data/ (prod). None if neither."""
    p = f"{DATA_DIR}/{FILES[(asset, timeframe)]}"
    if os.path.exists(p):
        return p
    fname = _PROD_FILES.get((asset, timeframe))
    if fname:
        p2 = os.path.join(_REPO_DATA_DIR, fname)
        if os.path.exists(p2):
            return p2
    return None

# (asset, timeframe) -> csv filename
FILES = {
    ("btc", "1d"): "btc_1d.csv",
    ("btc", "4h"): "btc_4h.csv",
    ("spy", "1d"): "spy_1d.csv",
}

# Cost per side (fraction), per spec §3
COST = {
    "btc": 0.0010,
    "spy": 0.0002,
}

# bars_per_year, per spec §5
BARS_PER_YEAR = {
    ("btc", "1d"): 365,
    ("btc", "4h"): 365 * 6,
    ("spy", "1d"): 252,
}

# IS / OOS windows, per spec §2. Inclusive date strings.
PERIODS = {
    "btc": {
        "IS": ("2017-01-01", "2024-09-05"),
        "OOS": ("2024-09-06", "2026-09-05"),
    },
    "spy": {
        "IS": ("2000-01-03", "2024-03-20"),
        "OOS": ("2024-03-21", "2026-03-20"),
    },
}


def load_raw(asset: str, timeframe: str) -> pd.DataFrame:
    """Load a CSV, parse dates, sort ascending, drop the BTC partial last bar."""
    path = resolve_path(asset, timeframe)
    if path is None:
        raise FileNotFoundError(f"no data file for {asset} {timeframe}")
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if asset == "btc":
        # Drop the final partial bar (2026-09-06, per spec §1 and README).
        last_date = df["date"].iloc[-1]
        if last_date.normalize() == pd.Timestamp("2026-09-06"):
            df = df.iloc[:-1].reset_index(drop=True)
        # Production files (fetch_data.py) never contain a partial bar, so nothing to drop there.

    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    return df


def period_mask(df: pd.DataFrame, asset: str, period: str) -> pd.Series:
    """Boolean mask selecting rows of df whose date falls within the given IS/OOS window."""
    start, end = PERIODS[asset][period]
    start_ts = pd.Timestamp(start)
    # end is inclusive of the whole calendar day (covers 4h bars within that day too)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return (df["date"] >= start_ts) & (df["date"] <= end_ts)


def load_all():
    """Convenience: load all three (asset, timeframe) series into a dict."""
    out = {}
    for (asset, tf) in FILES:
        out[(asset, tf)] = load_raw(asset, tf)
    return out


# ---------------------------------------------------------------------------
# netcheck (spec_v2) extension — additive only, nothing above this line is modified.
#
# v1's load_raw/FILES/COST/PERIODS hard-code the three (asset, timeframe) combos this repo's
# original spec covered, at a fixed data directory. netcheck needs to load arbitrary
# <SYMBOL>_<TF>.csv files from its own data/ directory (BTCUSD, ETHUSD, ...), so we add a
# generic loader alongside the v1 one rather than touching it.
# ---------------------------------------------------------------------------

def load_generic(path: str, drop_last_if_partial: bool = False,
                  expected_partial_date: str | None = None,
                  min_date: str | None = None) -> pd.DataFrame:
    """
    Load a v1-format CSV (date,open,high,low,close,volume; any order of columns present) from an
    arbitrary path, sort ascending, and optionally drop the final row.

    drop_last_if_partial: if True, drop the last row (the "current, not-yet-closed bar" convention
    used throughout both specs).
    expected_partial_date: if given, only drop the last row when its date normalizes to this date
    (mirrors v1 load_raw's defensive assertion pattern); if the last row's date does not match,
    the row is left in place rather than silently dropped. If None, the last row is dropped
    unconditionally whenever drop_last_if_partial is True.
    min_date: if given, rows strictly before this date are dropped BEFORE the caller computes any
    indicator/signal off the returned frame (run_weekly.py passes config.DATA_START here, so the
    offline Bitstamp stand-in file's thin-liquidity 2012+ history doesn't distort IS metrics the
    way production Binance data — which starts at listing, 2017-08-17 — never would).
    """
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if drop_last_if_partial and len(df):
        if expected_partial_date is not None:
            last_date = df["date"].iloc[-1]
            if last_date.normalize() == pd.Timestamp(expected_partial_date):
                df = df.iloc[:-1].reset_index(drop=True)
        else:
            df = df.iloc[:-1].reset_index(drop=True)

    if min_date is not None:
        df = df[df["date"] >= pd.Timestamp(min_date)].reset_index(drop=True)

    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    return df


def rolling_is_oos_window(df: pd.DataFrame, oos_days: int):
    """
    netcheck's rolling IS/OOS split (spec_v2 §2), as opposed to v1's fixed calendar windows:
      as_of = date of the last (already-closed) bar in df
      OOS   = [as_of - oos_days days, as_of]
      IS    = [series start, day before OOS start]
    Returns (as_of: pd.Timestamp, is_mask: pd.Series[bool], oos_mask: pd.Series[bool]).
    Indicator warm-up still uses the full series (this function only defines the aggregation
    windows, exactly like v1's period_mask/PERIODS do for the fixed-window case).
    """
    as_of = df["date"].iloc[-1].normalize()
    oos_start = as_of - pd.Timedelta(days=oos_days)
    dates_norm = df["date"].dt.normalize()
    oos_mask = (dates_norm >= oos_start) & (dates_norm <= as_of)
    is_mask = dates_norm < oos_start
    return as_of, is_mask, oos_mask


if __name__ == "__main__":
    for key in FILES:
        d = load_raw(*key)
        print(key, d.shape, d["date"].min(), d["date"].max())
