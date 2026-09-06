"""
fetch_data.py — spec_v2 §1 data fetch: Bitstamp public OHLC API, incremental, per (symbol, tf)
in config.ASSETS x config.TIMEFRAMES. (Binance was the original plan but returns HTTP 451 from
GitHub's US-based runners; Bitstamp has no geo-block, no API key, and supports 4h/1d steps.)
In THIS dev environment every exchange API is network-blocked, so locally it is only ever
exercised via `--offline` (see below and README.md).

Online mode (default, `python fetch_data.py`):
  For each (symbol, step) pair, page GET https://www.bitstamp.net/api/v2/ohlc/<pair>/ with
  limit=1000 and exclude_current_candle=true, starting from config.LISTING_DATE[symbol] (or the
  bar after the last one already on disk, for an incremental update), until the API returns
  fewer than 1000 rows. Bitstamp timestamps are candle OPEN times in UTC; steps 14400 (4h) and
  86400 (1d) are aligned to 00:00 UTC, matching the offline files. Writes/updates
  data/<symbol>_<tf>.csv in the v1 normalized format (date,open,high,low,close,volume).

Offline mode (`python fetch_data.py --offline`):
  Copies config.OFFLINE_SOURCES[(symbol, tf)] in as data/<symbol>_<tf>.csv, dropping the last row
  if it matches config.OFFLINE_PARTIAL_BAR_DATE (the same drop-the-partial-bar rule, applied to
  the local Bitstamp BTCUSD stand-in files instead of a live Binance response). Any
  (symbol, timeframe) with no offline source configured (ETHUSD, in this environment) is
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

BITSTAMP_OHLC_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"
BITSTAMP_LIMIT = 1000
# Bitstamp `step` (candle length in seconds), keyed by our timeframe strings.
BITSTAMP_STEP = {"1d": 86400, "4h": 14400}


def fetch_ohlc_bitstamp(symbol: str, tf: str, start_ts: int) -> pd.DataFrame:
    """Page through Bitstamp's public OHLC endpoint from start_ts (unix seconds, UTC) forward,
    BITSTAMP_LIMIT candles per page, until a short page. Returns normalized ascending rows.
    exclude_current_candle=true makes Bitstamp omit the still-forming bar, so nothing partial is
    ever written."""
    pair = config.BITSTAMP_SYMBOL[symbol]
    step = BITSTAMP_STEP[tf]
    frames = []
    cursor = int(start_ts)
    while True:
        params = {"step": step, "limit": BITSTAMP_LIMIT, "start": cursor,
                  "exclude_current_candle": "true"}
        resp = requests.get(BITSTAMP_OHLC_URL.format(pair=pair), params=params, timeout=30,
                            headers={"User-Agent": "deadoralive/0.1 (+github.com/getjogak-sketch/deadoralive)"})
        resp.raise_for_status()
        ohlc = resp.json().get("data", {}).get("ohlc", [])
        if not ohlc:
            break
        rows = [{
            "date": pd.to_datetime(int(k["timestamp"]), unit="s", utc=True).tz_localize(None),
            "open": float(k["open"]), "high": float(k["high"]), "low": float(k["low"]),
            "close": float(k["close"]), "volume": float(k["volume"]),
        } for k in ohlc]
        frames.append(pd.DataFrame(rows))
        last_ts = int(ohlc[-1]["timestamp"])
        if len(ohlc) < BITSTAMP_LIMIT or last_ts + step <= cursor:
            break
        cursor = last_ts + step
        time.sleep(0.3)
    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    # Belt and braces: never keep a bar whose close time is still in the future.
    now = pd.Timestamp.utcnow().tz_localize(None)
    df = df[df["date"] + pd.Timedelta(seconds=step) <= now].reset_index(drop=True)
    return df


def _out_path(symbol: str, tf: str) -> str:
    return os.path.join(config.DATA_DIR, f"{symbol}_{tf}.csv")


def update_symbol_online(symbol: str, tf: str) -> bool:
    """Incremental online update for one (symbol, timeframe): appends only bars after the last
    one already on disk (or starts from config.LISTING_DATE[symbol] if no file exists yet).
    Returns True if the file was written, False if skipped (no new bars)."""
    out_path = _out_path(symbol, tf)
    step = BITSTAMP_STEP[tf]

    if os.path.exists(out_path):
        existing = load_generic(out_path)
        start_ts = int(existing["date"].iloc[-1].timestamp()) + step
    else:
        existing = None
        start_dt = datetime.strptime(config.LISTING_DATE[symbol], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ts = int(start_dt.timestamp())

    fetched = fetch_ohlc_bitstamp(symbol, tf, start_ts)
    if fetched.empty:
        print(f"[fetch_data] {symbol} {tf}: no new bars.")
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
                         help="Use local files (config.OFFLINE_SOURCES) instead of the Bitstamp "
                              "API — required in this dev environment, where exchange APIs are "
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
                    print("[fetch_data] (expected in this dev environment — exchange APIs are "
                          "network-blocked; re-run with --offline)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
