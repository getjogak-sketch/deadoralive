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


# ---------------------------------------------------------------------------
# Korean edition data source (this task's requirement 1) — Upbit's public candle REST API, no
# key required. Additive only: nothing above this line (the Bitstamp/English-edition path) is
# modified.
#
# GET https://api.upbit.com/v1/candles/days?market=KRW-BTC&count=200&to=<ISO8601 UTC>
# GET https://api.upbit.com/v1/candles/minutes/240?market=KRW-BTC&count=200&to=<ISO8601 UTC>
#
# Upbit returns candles NEWEST-FIRST, up to `count` per page (200 max). We page BACKWARDS: each
# page's oldest candle's own candle_date_time_utc becomes the next page's `to` cursor, until we
# pass config.DATA_START_BY_ASSET[market] (2017-10-01 for both Upbit assets). The still-forming
# (not yet closed) candle is dropped from the final assembled series.
#
# Upbit's real endpoint is network-blocked in this dev environment exactly like Bitstamp's, so
# this path is written but exercised here only via its pure parsing helpers (_parse_upbit_page,
# _drop_still_forming_upbit), unit-tested in tests.py against a hand-written fake payload.
# ---------------------------------------------------------------------------

UPBIT_CANDLE_URL = {
    "1d": "https://api.upbit.com/v1/candles/days",
    "4h": "https://api.upbit.com/v1/candles/minutes/240",
}
UPBIT_COUNT = 200
UPBIT_SLEEP_SECONDS = 0.15  # rate limit between requests, per this task's instruction
UPBIT_STEP = {"1d": pd.Timedelta(days=1), "4h": pd.Timedelta(hours=4)}


def _parse_upbit_page(candles: list) -> pd.DataFrame:
    """Pure parser for one page of Upbit's candle response (a list of dicts, Upbit's native
    newest-first order). Maps Upbit's field names to the v1 normalized format
    (date,open,high,low,close,volume) and returns them ASCENDING (oldest first). No I/O — this is
    the function tests.py unit-tests against a hand-written fake payload."""
    rows = [{
        "date": pd.Timestamp(c["candle_date_time_utc"]),
        "open": float(c["opening_price"]),
        "high": float(c["high_price"]),
        "low": float(c["low_price"]),
        "close": float(c["trade_price"]),
        "volume": float(c["candle_acc_trade_volume"]),
    } for c in candles]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return df.sort_values("date").reset_index(drop=True)


def _drop_still_forming_upbit(df: pd.DataFrame, tf: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop the last row if its bar has not fully closed yet (its own close time is still in the
    future relative to `now`). `now` is injectable so this is unit-testable without a clock."""
    if df.empty:
        return df
    now = pd.Timestamp.utcnow().tz_localize(None) if now is None else now
    step = UPBIT_STEP[tf]
    if df["date"].iloc[-1] + step > now:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def fetch_ohlc_upbit(market: str, tf: str) -> pd.DataFrame:
    """Page Upbit's public candle endpoint backwards (via `to`) from now until we pass
    config.DATA_START_BY_ASSET[market]. Returns normalized ascending rows, still-forming last
    candle dropped. Always fetches the full history each call (Upbit's endpoint has no documented
    "since" bound, unlike Bitstamp's `start`), which is why the Korean edition's fetch is a full
    re-fetch rather than an incremental append like update_symbol_online above."""
    url = UPBIT_CANDLE_URL[tf]
    min_date = pd.Timestamp(config.DATA_START_BY_ASSET.get(market, config.DATA_START))
    frames = []
    to_cursor = None
    while True:
        params = {"market": market, "count": UPBIT_COUNT}
        if to_cursor is not None:
            params["to"] = to_cursor
        resp = requests.get(url, params=params, timeout=30,
                             headers={"User-Agent": "deadoralive/0.1 (+github.com/getjogak-sketch/deadoralive)"})
        resp.raise_for_status()
        candles = resp.json()
        if not candles:
            break
        page = _parse_upbit_page(candles)
        frames.append(page)
        oldest_date = page["date"].iloc[0]
        if oldest_date <= min_date:
            break
        # candles[-1] is the oldest raw candle in this (newest-first) page — its own
        # candle_date_time_utc is the correct backward-paging cursor for the next request.
        to_cursor = candles[-1]["candle_date_time_utc"]
        time.sleep(UPBIT_SLEEP_SECONDS)

    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= min_date].reset_index(drop=True)
    df = _drop_still_forming_upbit(df, tf)
    return df


def update_symbol_online_upbit(symbol: str, tf: str) -> bool:
    """Online update for one Upbit (market, timeframe): full re-fetch (see fetch_ohlc_upbit),
    written to data/<symbol>_<tf>.csv. Returns True if a file was written, False if the fetch
    came back empty (e.g. a 4xx from Upbit) — in either case the caller's try/except is what
    guarantees this never fails the overall run."""
    fetched = fetch_ohlc_upbit(symbol, tf)
    if fetched.empty:
        print(f"[fetch_data] WARNING: Upbit {symbol} {tf} returned no usable candles — skipping "
              f"(not a failure).")
        return False
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out_path = _out_path(symbol, tf)
    fetched.to_csv(out_path, index=False)
    print(f"[fetch_data] (Upbit) {symbol} {tf}: wrote {len(fetched)} rows "
          f"({fetched['date'].min()} .. {fetched['date'].max()}) -> {out_path}")
    return True


def update_symbol_offline_upbit(symbol: str, tf: str) -> bool:
    """Offline mode has no local Upbit stand-in file anywhere in this environment (unlike
    BTCUSD's Bitstamp stand-in) — skip with a warning, per this task's requirement 1. Not a
    failure: run_weekly.py's Korean edition renders a "no data this week" page in this case."""
    print(f"[fetch_data] WARNING: --offline has no local Upbit stand-in for {symbol} {tf} — "
          f"skipping (not a failure). Real Upbit fetch is network-blocked in this dev "
          f"environment too; run without --offline in production.")
    return False


# ---------------------------------------------------------------------------
# Stocks edition data source (spec_v3 §A) — Stooq daily CSV (no key), Yahoo chart JSON as a
# fallback if Stooq fails. Additive only: nothing above this line (Bitstamp/Upbit fetchers) is
# modified.
#
# Stooq: GET https://stooq.com/q/d/l/?s=<symbol>.us&i=d -> CSV text, header
#   "Date,Open,High,Low,Close,Volume". A dead/unknown symbol or a rate-limited request returns an
#   HTML error page or an empty body instead of that header, which _parse_stooq_csv treats as "no
#   data" (never raises) so the caller can fall back to Yahoo.
# Yahoo: GET https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=max&interval=1d ->
#   JSON with parallel arrays under chart.result[0].timestamp /
#   chart.result[0].indicators.quote[0].{open,high,low,close,volume}. Any bar with a null OHLC
#   value (Yahoo does this for holidays/gaps inside `range=max`) is skipped.
#
# Both real endpoints are network-blocked in this dev environment exactly like Bitstamp/Upbit's,
# so both fetch_ohlc_* functions are written but exercised here only through their pure,
# unit-tested parsers (_parse_stooq_csv, _parse_yahoo_chart_json) against hand-written fake
# payloads (tests.py: test_stooq_parser, test_yahoo_parser) — never against a live response.
# Per this task's instruction, --offline has NO local stand-in for SPY/QQQ (unlike BTCUSD) and
# always skips the stocks edition with a warning, not a failure.
# ---------------------------------------------------------------------------

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}.us&i=d"
STOOQ_SYMBOL = {"SPY": "spy", "QQQ": "qqq"}
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=max&interval=1d"

_NORM_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def _empty_ohlc_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_NORM_COLUMNS)


def _parse_stooq_csv(text: str) -> pd.DataFrame:
    """Pure parser for Stooq's daily-CSV response body. Returns an empty (but correctly-columned)
    frame — never raises — if `text` isn't a well-formed Stooq CSV (empty body, an HTML error
    page, or a header that doesn't match what Stooq documents), so the caller can treat that as
    "try the Yahoo fallback" per spec_v3 §A. No I/O here — this is what tests.py unit-tests
    against hand-written fake payloads."""
    import io

    if text is None:
        return _empty_ohlc_df()
    text = text.strip()
    if not text or text.lower().startswith("<"):
        return _empty_ohlc_df()
    first_line = text.splitlines()[0].strip()
    if first_line.replace(" ", "") != "Date,Open,High,Low,Close,Volume":
        return _empty_ohlc_df()
    try:
        raw = pd.read_csv(io.StringIO(text))
    except Exception:
        return _empty_ohlc_df()
    if raw.empty:
        return _empty_ohlc_df()
    raw = raw.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                               "Close": "close", "Volume": "volume"})
    missing = set(_NORM_COLUMNS) - set(raw.columns)
    if missing:
        return _empty_ohlc_df()
    raw["date"] = pd.to_datetime(raw["date"])
    for c in ("open", "high", "low", "close", "volume"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw.dropna(subset=["date", "open", "high", "low", "close"])
    raw = raw[_NORM_COLUMNS].sort_values("date").drop_duplicates(subset="date").reset_index(drop=True)
    return raw


def _parse_yahoo_chart_json(payload: dict) -> pd.DataFrame:
    """Pure parser for Yahoo's `/v8/finance/chart/<symbol>` response body (already json-decoded).
    Returns an empty frame — never raises — on any malformed/missing-data shape. Bars with a null
    OHLC value (Yahoo's own convention for a session with no trade data inside a `range=max`
    request) are dropped rather than kept as NaN rows. No I/O here — unit-tested in tests.py
    against a hand-written fake payload."""
    try:
        result = (payload or {}).get("chart", {}).get("result")
    except AttributeError:
        return _empty_ohlc_df()
    if not result:
        return _empty_ohlc_df()
    r = result[0] or {}
    timestamps = r.get("timestamp") or []
    quotes = ((r.get("indicators") or {}).get("quote") or [{}])
    q = quotes[0] if quotes else {}
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    volumes = q.get("volume") or []

    rows = []
    for i, ts in enumerate(timestamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        if o is None or h is None or l is None or c is None:
            continue
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
        rows.append({
            "date": pd.Timestamp(int(ts), unit="s", tz="UTC").tz_localize(None).normalize(),
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": float(v),
        })
    df = pd.DataFrame(rows, columns=_NORM_COLUMNS)
    return df.sort_values("date").drop_duplicates(subset="date").reset_index(drop=True)


def _drop_unclosed_stocks_bar(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Per spec_v3 §A: "Drop today's bar if the US market session is not over (bar date == today
    UTC)." A simple date-equality check against UTC "now", injectable for testing without a
    clock — deliberately conservative (it may also drop an already-fully-published prior-day bar
    on a day when `now` and the exchange's calendar disagree by a few hours, which self-corrects
    on the next run) rather than risk keeping a same-day bar that Stooq/Yahoo already served
    mid-session."""
    if df.empty:
        return df
    now = pd.Timestamp.utcnow().tz_localize(None) if now is None else now
    today = now.normalize()
    if df["date"].iloc[-1].normalize() == today:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def fetch_ohlc_stooq(symbol: str) -> pd.DataFrame:
    stooq_symbol = STOOQ_SYMBOL[symbol]
    resp = requests.get(STOOQ_URL.format(symbol=stooq_symbol), timeout=30,
                         headers={"User-Agent": "deadoralive/0.1 (+github.com/getjogak-sketch/deadoralive)"})
    resp.raise_for_status()
    return _parse_stooq_csv(resp.text)


def fetch_ohlc_yahoo(symbol: str) -> pd.DataFrame:
    resp = requests.get(YAHOO_CHART_URL.format(symbol=symbol), timeout=30,
                         headers={"User-Agent": "deadoralive/0.1 (+github.com/getjogak-sketch/deadoralive)"})
    resp.raise_for_status()
    return _parse_yahoo_chart_json(resp.json())


def update_symbol_online_stocks(symbol: str, tf: str = "1d") -> bool:
    """Online fetch for one stocks-edition symbol: Stooq first, Yahoo chart JSON if Stooq comes
    back empty (non-200 raises are caught by the caller, exactly like every other online fetcher
    here). Full history is re-fetched every run (spec_v3 §A: "Full history each run is fine
    (small)"). Never raises past a network error on the Yahoo leg either — that, too, becomes a
    skip-with-warning, since spec_v3 §A requires "if both fail, skip with a warning (never fail
    the run)"."""
    try:
        df = fetch_ohlc_stooq(symbol)
    except requests.exceptions.RequestException as e:
        print(f"[fetch_data] Stooq fetch failed for {symbol}: {e} — trying Yahoo fallback.")
        df = _empty_ohlc_df()

    if df.empty:
        try:
            df = fetch_ohlc_yahoo(symbol)
        except requests.exceptions.RequestException as e:
            print(f"[fetch_data] Yahoo fallback also failed for {symbol}: {e} — skipping "
                  f"(not a failure).")
            df = _empty_ohlc_df()

    if df.empty:
        print(f"[fetch_data] WARNING: both Stooq and Yahoo returned no usable data for "
              f"{symbol} — skipping (not a failure).")
        return False

    df = df[df["date"] >= pd.Timestamp(config.STOCKS_DATA_START)].reset_index(drop=True)
    df = _drop_unclosed_stocks_bar(df)
    if df.empty:
        print(f"[fetch_data] WARNING: {symbol} data was empty after filtering — skipping.")
        return False

    os.makedirs(config.DATA_DIR, exist_ok=True)
    out_path = _out_path(symbol, tf)
    df.to_csv(out_path, index=False)
    print(f"[fetch_data] (stocks) {symbol} {tf}: wrote {len(df)} rows "
          f"({df['date'].min()} .. {df['date'].max()}) -> {out_path}")
    return True


def update_symbol_offline_stocks(symbol: str, tf: str = "1d") -> bool:
    """Offline mode has no local Stooq/Yahoo stand-in for SPY/QQQ in this environment (per this
    task's instruction: "--offline (local BTC files only; other assets skipped with warnings)")
    — skip with a warning, exactly like the Korean edition's Upbit assets do offline. Not a
    failure: run_weekly.py's stocks edition renders a "no data this week" style skip when no
    stocks data file is present."""
    print(f"[fetch_data] WARNING: --offline has no local stand-in for {symbol} {tf} (stocks "
          f"edition) — skipping (not a failure). Stooq/Yahoo are network-blocked in this dev "
          f"environment too; run without --offline in production.")
    return False


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

    # Korean edition (Upbit KRW-BTC/KRW-ETH) — a second, independent data source. Any failure
    # here (network block, Upbit 4xx/5xx) is caught per-asset and only ever produces a warning:
    # it must never fail the whole run or affect the Bitstamp/English edition above.
    for symbol in config.UPBIT_ASSETS:
        for tf in config.TIMEFRAMES:
            try:
                if args.offline:
                    written = update_symbol_offline_upbit(symbol, tf)
                else:
                    written = update_symbol_online_upbit(symbol, tf)
                any_written = any_written or written
            except requests.exceptions.RequestException as e:
                print(f"[fetch_data] WARNING: Upbit fetch failed for {symbol} {tf}: {e} — "
                      f"skipping (not a failure; the English/Bitstamp edition is unaffected).")

    # Stocks edition (SPY/QQQ, daily) — a third, independent data source (Stooq, Yahoo fallback).
    # Any failure here is caught per-symbol and only ever produces a warning: it must never fail
    # the whole run or affect the crypto (en/ko) editions above.
    for symbol in config.STOCKS_ASSETS:
        for tf in config.STOCKS_TIMEFRAMES:
            try:
                if args.offline:
                    written = update_symbol_offline_stocks(symbol, tf)
                else:
                    written = update_symbol_online_stocks(symbol, tf)
                any_written = any_written or written
            except requests.exceptions.RequestException as e:
                print(f"[fetch_data] WARNING: stocks fetch failed for {symbol} {tf}: {e} — "
                      f"skipping (not a failure; the crypto editions are unaffected).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
