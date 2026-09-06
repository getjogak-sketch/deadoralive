"""
fetch_data.py — spec_v2 §1 data fetch: Binance public REST klines, incremental, per (symbol, tf)
in config.ASSETS x config.TIMEFRAMES. Written in full, but in THIS environment api.binance.com is
network-blocked, so it is only ever exercised via `--offline` (see below and README.md).

Online mode (default, `python fetch_data.py`):
  For each (symbol, interval) pair, page GET https://api.binance.com/api/v3/klines with
  limit=1000, starting from config.LISTING_DATE[symbol] (or the day after the last bar already
  on disk, for an incremental update), until the API returns fewer than 1000 rows (caught up to
  "now"). The final returned kline is Binance's still-forming current candle — its close time is
  in the future — and is dropped before writing, mirroring spec.md/spec_v2's "drop the partial
  last bar" rule. Writes/updates data/<symbol>_<tf>.csv in the v1 normalized format
  (date,open,high,low,close,volume; ascending, UTC).

Offline mode (`python fetch_data.py --offline`):
  Copies config.OFFLINE_SOURCES[(symbol, tf)] in as data/<symbol>_<tf>.csv, dropping the last row
  if it matches config.OFFLINE_PARTIAL_BAR_DATE (the same drop-the-partial-bar rule, applied to
  the local Bitstamp BTCUSD stand-in files instead of a live Binance response). Any
  (symbol, timeframe) with no offline source configured (ETHUSDT, in this environment) is
  SKIPPED WITH A WARNING, per spec_v2 §1 — not treated as a failure.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

import config
from data_loader import load_generic

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_LIMIT = 1000

# Binance kline interval strings, keyed by our (identical) timeframe strings.
BINANCE_INTERVAL = {"1d": "1d", "4h": "4h"}


def _out_path(symbol: str, tf: str) -> str:
    return os.path.join(config.DATA_DIR, f"{symbol}_{tf}.csv")


def _klines_to_df(klines: list) -> pd.DataFrame:
    """Binance kline row: [open_time_ms, open, high, low, close, volume, close_time_ms, ...]."""
    rows = []
    for k in klines:
        rows.append({
            "date": pd.to_datetime(int(k[0]), unit="ms", utc=True).tz_localize(None),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
            "close_time_ms": int(k[6]),
        })
    return pd.DataFrame(rows)


def fetch_klines_binance(symbol: str, interval: str, start_time_ms: int) -> pd.DataFrame:
    """
    Page through Binance's public klines endpoint (no API key required) from start_time_ms
    forward, BINANCE_LIMIT rows at a time, until a page comes back with fewer than BINANCE_LIMIT
    rows (meaning we've caught up to the present). A short sleep between pages is polite to the
    public rate limit. Returns a single concatenated DataFrame with a `close_time_ms` column
    still attached (used by the caller to detect the trailing still-forming candle).
    """
    frames = []
    cursor = start_time_ms
    while True:
        params = {
            "symbol": symbol, "interval": interval,
            "limit": BINANCE_LIMIT, "startTime": cursor,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        resp.raise_for_status()
        klines = resp.json()
        if not klines:
            break
        frames.append(_klines_to_df(klines))
        cursor = int(klines[-1][6]) + 1  # one ms after the last kline's close time
        if len(klines) < BINANCE_LIMIT:
            break
        time.sleep(0.2)

    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "close_time_ms"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def update_symbol_online(symbol: str, tf: str) -> bool:
    """
    Incremental online update for one (symbol, timeframe): appends only bars after the last date
    already on disk (or starts from config.LISTING_DATE[symbol] if no file exists yet). Drops the
    trailing still-forming candle (close_time in the future) before writing. Returns True if a
    file was written, False if skipped (e.g. no new data yet).
    """
    interval = BINANCE_INTERVAL[tf]
    out_path = _out_path(symbol, tf)

    if os.path.exists(out_path):
        existing = load_generic(out_path)
        start_time_ms = int(existing["date"].iloc[-1].timestamp() * 1000) + 1
    else:
        existing = None
        start_dt = datetime.strptime(config.LISTING_DATE[symbol], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_time_ms = int(start_dt.timestamp() * 1000)

    fetched = fetch_klines_binance(symbol, interval, start_time_ms)
    if fetched.empty:
        print(f"[fetch_data] {symbol} {tf}: no new bars.")
        return False

    now_ms = int(time.time() * 1000)
    fetched = fetched[fetched["close_time_ms"] <= now_ms].drop(columns=["close_time_ms"])
    if fetched.empty:
        print(f"[fetch_data] {symbol} {tf}: only the still-forming current bar was returned; nothing to add.")
        return False

    combined = fetched if existing is None else pd.concat([existing, fetched], ignore_index=True)
    combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    combined.to_csv(out_path, index=False)
    print(f"[fetch_data] {symbol} {tf}: wrote {len(combined)} rows ({combined['date'].min()} .. {combined['date'].max()}) -> {out_path}")
    return True


def update_symbol_offline(symbol: str, tf: str) -> bool:
    """Offline stand-in: copy config.OFFLINE_SOURCES[(symbol, tf)] in, dropping the configured
    partial bar date. Skips (returns False) with a warning if no offline source is configured for
    this (symbol, timeframe) — per spec_v2 §1, a missing asset is a warning, not a failure."""
    key = (symbol, tf)
    src = config.OFFLINE_SOURCES.get(key)
    if src is None or not os.path.exists(src):
        print(f"[fetch_data] WARNING: no offline source for {symbol} {tf} — skipping (not a failure).")
        return False

    df = load_generic(src, drop_last_if_partial=True,
                       expected_partial_date=config.OFFLINE_PARTIAL_BAR_DATE)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out_path = _out_path(symbol, tf)
    df.to_csv(out_path, index=False)
    print(f"[fetch_data] (offline) {symbol} {tf}: {len(df)} rows ({df['date'].min()} .. {df['date'].max()}) -> {out_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="netcheck data fetch (spec_v2 §1)")
    parser.add_argument("--offline", action="store_true",
                         help="Use local files (config.OFFLINE_SOURCES) instead of the Binance "
                              "API — required in this environment, where api.binance.com is "
                              "network-blocked.")
    args = parser.parse_args()

    any_written = False
    for symbol in config.ASSETS:
        for tf in config.TIMEFRAMES:
            try:
                if args.offline:
                    written = update_symbol_offline(symbol, tf)
                else:
                    written = update_symbol_online(symbol, tf)
                any_written = any_written or written
            except requests.exceptions.RequestException as e:
                print(f"[fetch_data] ERROR fetching {symbol} {tf}: {e}")
                if not args.offline:
                    print("[fetch_data] (expected in this dev environment — Binance is network-"
                          "blocked; re-run with --offline)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
