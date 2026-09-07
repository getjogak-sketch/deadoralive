"""
tests.py — §6 engine-honesty verification. Must all pass before run_all.py is trusted.

1. No-lookahead test (both strategies, >=3 truncation points each).
2. Two-engine cross-check (ma_cross only, vs backtesting.py) — see crosscheck_bt.py.
3. Sanity: zero-cost result is always >= cost-applied result (never printed in final results).
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pandas as pd

import config
from data_loader import load_raw, period_mask, COST, PERIODS, resolve_path
from strategies import ma_cross_target_state, vol_breakout_targets, MA_CROSS_PARAMS, VOL_BREAKOUT_PARAMS
from engine import simulate_ma_cross, simulate_vol_breakout

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append((name, detail))


# ---------------------------------------------------------------------------
# 1. No-lookahead
# ---------------------------------------------------------------------------

def test_no_lookahead_ma_cross():
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        Ts = sorted(set([n // 4, n // 2, (3 * n) // 4]))
        for n_fast, n_slow in MA_CROSS_PARAMS:
            full_state = ma_cross_target_state(df["close"], n_fast, n_slow)
            for T in Ts:
                if T < n_slow + 5:
                    continue
                truncated_close = df["close"].iloc[:T]
                trunc_state = ma_cross_target_state(truncated_close, n_fast, n_slow)
                same = (full_state.iloc[:T].to_numpy() == trunc_state.to_numpy()).all()
                check(
                    f"no-lookahead ma_cross {asset}/{tf} params=({n_fast},{n_slow}) T={T}",
                    same,
                    "target_state before T changed when future data was added",
                )


def test_no_lookahead_vol_breakout():
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        Ts = sorted(set([n // 4, n // 2, (3 * n) // 4]))
        for k in VOL_BREAKOUT_PARAMS:
            full_target, full_trig = vol_breakout_targets(df, k)
            for T in Ts:
                trunc_target, trunc_trig = vol_breakout_targets(df.iloc[:T].copy(), k)
                same_target = np.allclose(
                    full_target.iloc[:T].to_numpy(), trunc_target.to_numpy(), equal_nan=True
                )
                same_trig = (full_trig.iloc[:T].to_numpy() == trunc_trig.to_numpy()).all()
                check(
                    f"no-lookahead vol_breakout {asset}/{tf} k={k} T={T} (target)",
                    same_target,
                )
                check(
                    f"no-lookahead vol_breakout {asset}/{tf} k={k} T={T} (triggered)",
                    same_trig,
                )


# ---------------------------------------------------------------------------
# 3. Sanity: zero-cost >= cost-applied
# ---------------------------------------------------------------------------

def test_sanity_cost():
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        cost = COST[asset]
        for period in ["IS", "OOS"]:
            mask = period_mask(df, asset, period)

            for n_fast, n_slow in MA_CROSS_PARAMS:
                state = ma_cross_target_state(df["close"], n_fast, n_slow)
                trades_c, eq_c = simulate_ma_cross(df, state, mask, cost)
                trades_0, eq_0 = simulate_ma_cross(df, state, mask, 0.0)
                tr_c = eq_c["equity"].iloc[-1] - 1.0 if len(eq_c) else 0.0
                tr_0 = eq_0["equity"].iloc[-1] - 1.0 if len(eq_0) else 0.0
                check(
                    f"sanity ma_cross {asset}/{tf} params=({n_fast},{n_slow}) {period}: "
                    f"zero-cost({tr_0:.4f}) >= cost({tr_c:.4f})",
                    tr_0 >= tr_c - 1e-12,
                )

            for k in VOL_BREAKOUT_PARAMS:
                target, trig = vol_breakout_targets(df, k)
                trades_c, eq_c = simulate_vol_breakout(df, target, trig, mask, cost)
                trades_0, eq_0 = simulate_vol_breakout(df, target, trig, mask, 0.0)
                tr_c = eq_c["equity"].iloc[-1] - 1.0 if len(eq_c) else 0.0
                tr_0 = eq_0["equity"].iloc[-1] - 1.0 if len(eq_0) else 0.0
                check(
                    f"sanity vol_breakout {asset}/{tf} k={k} {period}: "
                    f"zero-cost({tr_0:.4f}) >= cost({tr_c:.4f})",
                    tr_0 >= tr_c - 1e-12,
                )


# ---------------------------------------------------------------------------
# netcheck (spec_v2 §6) extension — additive only, nothing above this line is modified.
#
# 1. No-lookahead test, generalized across EVERY strategy in registry.py (spec_v2 §3's hard
#    requirement: "v1 §6.1 no-lookahead 테스트가 모든 전략에 대해 돌아야 한다"). Run against v1's
#    own three (asset, timeframe) series — this needs no netcheck-specific data file (no
#    dependency on fetch_data.py having run yet), since the property under test ("does this pure
#    function of a price series ever look past row T") does not depend on which price series or
#    which directory it came from.
# 2. Indicator spot checks (RSI/EMA/MACD/Bollinger/Donchian) against hand-derivable known values.
# ---------------------------------------------------------------------------

import registry as _registry


MIN_WARMUP_T = 260  # safely past every registry variant's longest lookback (SMA/RSI200, n=90, etc.)


def test_no_lookahead_registry():
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        Ts = sorted(set([n // 4, n // 2, (3 * n) // 4]))
        for sid, sname, stype, variant in _registry.iter_variants():
            full_out = variant["signal_fn"](df)
            for T in Ts:
                if T < MIN_WARMUP_T:
                    continue
                df_trunc = df.iloc[:T].reset_index(drop=True)
                trunc_out = variant["signal_fn"](df_trunc)
                label = f"no-lookahead {sid} params={variant['params_str']} {asset}/{tf} T={T}"

                if stype in ("state", "holdN"):
                    full_arr = full_out.iloc[:T].to_numpy()
                    trunc_arr = trunc_out.to_numpy()
                    same = np.array_equal(full_arr, trunc_arr)
                    check(label, same, "signal before T changed when future data was added")
                elif stype == "onebar":
                    full_target, full_trig = full_out
                    trunc_target, trunc_trig = trunc_out
                    same_target = np.allclose(
                        full_target.iloc[:T].to_numpy(), trunc_target.to_numpy(), equal_nan=True
                    )
                    same_trig = np.array_equal(full_trig.iloc[:T].to_numpy(), trunc_trig.to_numpy())
                    check(label + " (target)", same_target)
                    check(label + " (triggered)", same_trig)
                else:
                    raise AssertionError(f"unknown registry strategy type: {stype}")


# ---------------------------------------------------------------------------
# spec_v3 §D "Popular combos" — additive extension, nothing above this line is modified.
#
# 1. The same generic no-lookahead truncation test as test_no_lookahead_registry, over
#    registry.iter_popular_combo_variants() instead of registry.iter_variants() — spec_v3 §D:
#    "Every new strategy gets the no-lookahead truncation test like all others".
# 2. Two EXPLICIT checks spec_v3 §D calls out by name as "the two places a lookahead bug is most
#    likely": the Ichimoku cloud's forward shift, and the Heikin-Ashi recursion — hand-derived/
#    independently-recomputed, not just exercised indirectly through (1).
# ---------------------------------------------------------------------------

def test_no_lookahead_popular_combos():
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        Ts = sorted(set([n // 4, n // 2, (3 * n) // 4]))
        for sid, sname, stype, variant in _registry.iter_popular_combo_variants():
            full_out = variant["signal_fn"](df)
            for T in Ts:
                if T < MIN_WARMUP_T:
                    continue
                df_trunc = df.iloc[:T].reset_index(drop=True)
                trunc_out = variant["signal_fn"](df_trunc)
                label = f"no-lookahead (popular combo) {sid} params={variant['params_str']} {asset}/{tf} T={T}"
                assert stype == "state", f"unexpected popular-combo strategy type: {stype}"
                full_arr = full_out.iloc[:T].to_numpy()
                trunc_arr = trunc_out.to_numpy()
                check(label, np.array_equal(full_arr, trunc_arr),
                      "signal before T changed when future data was added")


def test_ichimoku_shift_explicit():
    """spec_v3 §D: the Ichimoku cloud's forward shift is "the" place a lookahead bug is most
    likely (it's the one indicator here whose whole point is to be evaluated against data from
    26 bars ago). Independently recomputes the raw (unshifted) senkou lines with plain pandas
    rolling ops — NOT by calling into indicators.ichimoku_cloud_lines' own internals — and checks
    the shifted output against that independent computation, plus a truncation no-lookahead
    check and a guard against a no-op ("shift did nothing") bug."""
    import indicators as ind

    n = 120
    high = pd.Series(np.arange(n, dtype=float))
    low = high.copy()
    df = pd.DataFrame({"high": high, "low": low})

    senkou_a, senkou_b = ind.ichimoku_cloud_lines(df, n_tenkan=9, n_kijun=26, n_senkou_b=52,
                                                    cloud_shift=26)

    # Independent recomputation (plain rolling ops, no shared code with the function under test).
    tenkan_raw = (high.rolling(9, min_periods=9).max() + low.rolling(9, min_periods=9).min()) / 2.0
    kijun_raw = (high.rolling(26, min_periods=26).max() + low.rolling(26, min_periods=26).min()) / 2.0
    senkou_a_raw = (tenkan_raw + kijun_raw) / 2.0
    senkou_b_raw = (high.rolling(52, min_periods=52).max() + low.rolling(52, min_periods=52).min()) / 2.0

    t = 100
    check("Ichimoku: senkou A at bar t equals the raw value computed at bar t-26 (shift correctness)",
          abs(senkou_a.iloc[t] - senkou_a_raw.iloc[t - 26]) < 1e-9,
          f"got {senkou_a.iloc[t]} vs {senkou_a_raw.iloc[t - 26]}")
    check("Ichimoku: senkou B at bar t equals the raw value computed at bar t-26 (shift correctness)",
          abs(senkou_b.iloc[t] - senkou_b_raw.iloc[t - 26]) < 1e-9,
          f"got {senkou_b.iloc[t]} vs {senkou_b_raw.iloc[t - 26]}")
    check("Ichimoku: senkou A at bar t does NOT equal the unshifted raw value at t itself "
          "(guards against a no-op/missing shift)",
          abs(senkou_a.iloc[t] - senkou_a_raw.iloc[t]) > 1e-9)

    T = 100
    df_trunc = df.iloc[:T].reset_index(drop=True)
    senkou_a_trunc, senkou_b_trunc = ind.ichimoku_cloud_lines(df_trunc, n_tenkan=9, n_kijun=26,
                                                                n_senkou_b=52, cloud_shift=26)
    check("Ichimoku no-lookahead: senkou A unchanged before T when future bars are added",
          np.allclose(senkou_a.iloc[:T].to_numpy(), senkou_a_trunc.to_numpy(), equal_nan=True))
    check("Ichimoku no-lookahead: senkou B unchanged before T when future bars are added",
          np.allclose(senkou_b.iloc[:T].to_numpy(), senkou_b_trunc.to_numpy(), equal_nan=True))


def test_heikin_ashi_explicit():
    """spec_v3 §D: the Heikin-Ashi recursion is the other place a lookahead bug is most likely
    (ha_open[t] depends on ha_open[t-1], not on a fixed rolling window — a copy/paste from
    ema()/rsi_wilder() elsewhere in this file could easily get the recursion direction backwards).
    Hand-derived expected values (recomputed independently in this docstring/test, not by calling
    the function under test) plus an explicit append-a-future-bar no-lookahead check."""
    import indicators as ind

    df = pd.DataFrame({
        "open": [10.0, 11.0, 9.0, 12.0],
        "high": [12.0, 12.0, 11.0, 13.0],
        "low": [9.0, 10.0, 8.0, 11.0],
        "close": [11.0, 9.5, 10.5, 12.5],
    })
    # ha_close[t] = mean(open,high,low,close)[t]; ha_open[0] = (open[0]+close[0])/2;
    # ha_open[t] = (ha_open[t-1]+ha_close[t-1])/2 for t>0 — hand-computed:
    expected_close = [10.5, 10.625, 9.625, 12.125]
    expected_open = [10.5, 10.5, 10.5625, 10.09375]

    ha_open, ha_close = ind.heikin_ashi(df)
    check("Heikin-Ashi close hand-derived", np.allclose(ha_close.to_numpy(), expected_close),
          f"got {ha_close.to_numpy()}")
    check("Heikin-Ashi open hand-derived", np.allclose(ha_open.to_numpy(), expected_open),
          f"got {ha_open.to_numpy()}")

    df_future = pd.concat(
        [df, pd.DataFrame({"open": [20.0], "high": [21.0], "low": [19.0], "close": [20.5]})],
        ignore_index=True,
    )
    ha_open_future, ha_close_future = ind.heikin_ashi(df_future)
    check("Heikin-Ashi no-lookahead: ha_open unchanged when a future bar is appended",
          np.allclose(ha_open_future.iloc[:4].to_numpy(), ha_open.to_numpy()))
    check("Heikin-Ashi no-lookahead: ha_close unchanged when a future bar is appended",
          np.allclose(ha_close_future.iloc[:4].to_numpy(), ha_close.to_numpy()))


def test_no_lookahead_reference_rows():
    """dca_weekly's buy-week flags depend only on each bar's own calendar date (ISO year/week),
    never on other bars' prices — trivially causal, but we still assert it holds for a truncated
    slice, per spec_v2's "every strategy in the registry" (the reference rows appear in the
    registry table too)."""
    from engine import simulate_dca_weekly
    from data_loader import period_mask, COST

    for asset, tf in [("btc", "1d"), ("btc", "4h")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        mask_full = pd.Series(True, index=df.index)
        _tr_full, mdd_full, detail_full = simulate_dca_weekly(df, mask_full, COST[asset])

        T = (3 * n) // 4
        df_trunc = df.iloc[:T].reset_index(drop=True)
        mask_trunc = pd.Series(True, index=df_trunc.index)
        _tr_t, _mdd_t, detail_trunc = simulate_dca_weekly(df_trunc, mask_trunc, COST[asset])

        # invested-so-far at each bar before T must be identical whether or not later bars exist
        same = np.allclose(
            detail_full["invested"].to_numpy()[:T], detail_trunc["invested"].to_numpy(),
            equal_nan=True,
        )
        check(f"no-lookahead dca_weekly {asset}/{tf} T={T}", same)


def test_indicator_spot_checks():
    import indicators as ind

    # --- RSI: hand-derived known values (Wilder's exact recursive formula, n=3) ---
    # closes = [10,12,11,13,12,14]; delta=[nan,2,-1,2,-1,2]
    # seed at t=3: avg_gain=mean([2,0,2])=4/3, avg_loss=mean([0,1,0])=1/3
    # t=4: avg_gain=(4/3*2+0)/3=8/9, avg_loss=(1/3*2+1)/3=5/9 -> RSI=100-100/(1+8/5)=61.538461...
    # t=5: avg_gain=(8/9*2+2)/3=34/27, avg_loss=(5/9*2+0)/3=10/27 -> RS=3.4 -> RSI=77.272727...
    closes = pd.Series([10.0, 12.0, 11.0, 13.0, 12.0, 14.0])
    rsi3 = ind.rsi_wilder(closes, 3)
    check("indicator RSI(3) hand-derived value at t=4",
          abs(rsi3.iloc[4] - 61.538461538) < 1e-6, f"got {rsi3.iloc[4]}")
    check("indicator RSI(3) hand-derived value at t=5",
          abs(rsi3.iloc[5] - 77.272727273) < 1e-6, f"got {rsi3.iloc[5]}")

    # --- RSI: analytic edge cases ---
    up = pd.Series(np.arange(1.0, 40.0))       # strictly increasing -> no losses ever -> RSI=100
    rsi_up = ind.rsi_wilder(up, 14)
    check("indicator RSI(14) == 100 on strictly increasing series",
          bool((rsi_up.iloc[15:] == 100.0).all()))
    down = pd.Series(np.arange(40.0, 1.0, -1.0))  # strictly decreasing -> RSI=0
    rsi_down = ind.rsi_wilder(down, 14)
    check("indicator RSI(14) == 0 on strictly decreasing series",
          bool((rsi_down.iloc[15:] == 0.0).all()))
    flat = pd.Series([50.0] * 30)               # no gains, no losses -> RSI=50 by convention
    rsi_flat = ind.rsi_wilder(flat, 14)
    check("indicator RSI(14) == 50 on flat series",
          bool((rsi_flat.iloc[15:] == 50.0).all()))

    # --- EMA: hand-derived, span=2 -> alpha=2/3 ---
    # EMA[0]=10; EMA[1]=2/3*20+1/3*10=16.6667; EMA[2]=2/3*30+1/3*16.6667=25.5556
    e = ind.ema(pd.Series([10.0, 20.0, 30.0]), 2)
    check("indicator EMA(2) hand-derived", np.allclose(e.to_numpy(), [10.0, 16.666666667, 25.555555556]))

    # --- MACD: constant series -> everything is exactly 0 ---
    const = pd.Series([100.0] * 50)
    macd_line, signal_line, hist = ind.macd(const, 12, 26, 9)
    check("indicator MACD == 0 on constant series",
          bool(np.allclose(macd_line, 0.0) and np.allclose(signal_line, 0.0) and np.allclose(hist, 0.0)))

    # --- Bollinger: constant series -> std=0, bands collapse to the price ---
    mid, upper, lower = ind.bollinger_bands(const, 20, 2.0)
    tail_ok = (mid.iloc[19:] == 100.0).all() and (upper.iloc[19:] == 100.0).all() and (lower.iloc[19:] == 100.0).all()
    check("indicator Bollinger bands collapse to price on constant series", bool(tail_ok))

    # --- Donchian: small hand-traceable example ---
    # high=low=[1,2,3,4,5], n_high=n_low=2 -> upper=shift(1).rolling(2).max()=[nan,nan,2,3,4]
    #                                          lower=shift(1).rolling(2).min()=[nan,nan,1,2,3]
    tiny = pd.DataFrame({"high": [1.0, 2.0, 3.0, 4.0, 5.0], "low": [1.0, 2.0, 3.0, 4.0, 5.0]})
    upper, lower = ind.donchian(tiny, 2, 2)
    check("indicator Donchian upper hand-derived",
          np.allclose(upper.to_numpy(), [np.nan, np.nan, 2, 3, 4], equal_nan=True))
    check("indicator Donchian lower hand-derived",
          np.allclose(lower.to_numpy(), [np.nan, np.nan, 1, 2, 3], equal_nan=True))

    # --- Supertrend: sanity (no crash, direction only takes valid values, None during ATR warmup) ---
    n_rows = 40
    base = np.linspace(100, 140, n_rows)
    synth = pd.DataFrame({
        "open": base, "high": base + 1.0, "low": base - 1.0, "close": base + 0.3,
    })
    direction = ind.supertrend(synth, 10, 3.0)
    check("indicator Supertrend: None during ATR(10) warm-up",
          direction.iloc[:10].isna().all())
    check("indicator Supertrend: only up/down after warm-up",
          set(direction.iloc[10:].unique()) <= {"up", "down"})


# ---------------------------------------------------------------------------
# Korean edition (Upbit KRW-BTC/KRW-ETH) — additive extension, nothing above this line is
# modified.
#
# 1. Upbit response parser test, on a hand-written fake payload (no network) — checks ascending
#    order, correct normalized columns, and that the still-forming candle gets dropped.
# 2. Korean page content test — the mandatory verbatim disclaimer must appear exactly twice
#    (top and bottom) and none of the four banned words may appear anywhere, on both the
#    full-data page and the "no data this week" page.
# ---------------------------------------------------------------------------

def test_upbit_parser():
    import fetch_data as fd

    # Hand-written fake payload, in Upbit's real response shape and order (NEWEST first).
    fake_payload = [
        {"market": "KRW-BTC", "candle_date_time_utc": "2024-01-03T00:00:00",
         "opening_price": 61000000.0, "high_price": 62000000.0, "low_price": 60500000.0,
         "trade_price": 61800000.0, "candle_acc_trade_volume": 123.456},
        {"market": "KRW-BTC", "candle_date_time_utc": "2024-01-02T00:00:00",
         "opening_price": 60000000.0, "high_price": 61200000.0, "low_price": 59800000.0,
         "trade_price": 61000000.0, "candle_acc_trade_volume": 200.0},
        {"market": "KRW-BTC", "candle_date_time_utc": "2024-01-01T00:00:00",
         "opening_price": 59000000.0, "high_price": 60200000.0, "low_price": 58500000.0,
         "trade_price": 60000000.0, "candle_acc_trade_volume": 150.0},
    ]

    df = fd._parse_upbit_page(fake_payload)

    check("Upbit parser: correct normalized columns",
          list(df.columns) == ["date", "open", "high", "low", "close", "volume"],
          f"got {list(df.columns)}")
    check("Upbit parser: 3 rows in, 3 rows out", len(df) == 3, f"got {len(df)}")
    check("Upbit parser: ascending order (oldest first)",
          list(df["date"]) == sorted(df["date"]),
          f"dates not ascending: {list(df['date'])}")
    check("Upbit parser: oldest row's date is 2024-01-01",
          df["date"].iloc[0] == pd.Timestamp("2024-01-01"))
    check("Upbit parser: newest row's date is 2024-01-03",
          df["date"].iloc[-1] == pd.Timestamp("2024-01-03"))
    check("Upbit parser: field mapping correct (close <- trade_price)",
          df["close"].iloc[-1] == 61800000.0, f"got {df['close'].iloc[-1]}")
    check("Upbit parser: field mapping correct (open/high/low/volume)",
          (df["open"].iloc[0] == 59000000.0 and df["high"].iloc[0] == 60200000.0
           and df["low"].iloc[0] == 58500000.0 and df["volume"].iloc[0] == 150.0))

    # Partial-candle drop: pretend "now" is only 12 hours after the newest 1d candle's open, so
    # that candle has not fully closed yet (a 1d step needs 24h) and must be dropped.
    now_mid_candle = pd.Timestamp("2024-01-03T12:00:00")
    dropped = fd._drop_still_forming_upbit(df, "1d", now=now_mid_candle)
    check("Upbit parser: still-forming last 1d candle is dropped",
          len(dropped) == 2 and dropped["date"].iloc[-1] == pd.Timestamp("2024-01-02"),
          f"got {list(dropped['date'])}")

    # Now pretend "now" is well after the newest candle's close (24h+) — nothing should be dropped.
    now_after_close = pd.Timestamp("2024-01-04T01:00:00")
    kept = fd._drop_still_forming_upbit(df, "1d", now=now_after_close)
    check("Upbit parser: fully-closed last candle is kept",
          len(kept) == 3, f"got {len(kept)}")


def test_korean_page_disclaimer_and_banned_words():
    import tempfile
    import build_site

    BANNED = ["추천", "수익 보장", "확실", "필승"]

    def _assert_page_ok(html_text, label):
        count = html_text.count(config.LEGAL_DISCLAIMER_KO)
        check(f"Korean page ({label}): mandatory disclaimer appears exactly twice (top+bottom)",
              count == 2, f"found {count} occurrence(s)")
        for word in BANNED:
            check(f"Korean page ({label}): banned word absent — {word!r}",
                  word not in html_text, f"found banned word {word!r}")

    with tempfile.TemporaryDirectory() as tmp:
        # 1) The "no data this week" page (requirement 6's key case — Upbit unavailable).
        empty_path = os.path.join(tmp, "empty_index.html")
        build_site.build_empty_edition_page_ko(empty_path, as_of="2026-09-05")
        with open(empty_path, encoding="utf-8") as f:
            empty_html = f.read()
        _assert_page_ok(empty_html, "no-data notice")
        check("Korean no-data page shows the '이번 주는 결과가 없습니다' notice",
              "이번 주는 결과가 없습니다" in empty_html)

        # 2) A full-data page, built from a small synthetic payload (no dependency on real Upbit
        #    data or on run_weekly.py having run yet).
        synthetic_payload = {
            "project_name": config.PROJECT_NAME,
            "tagline": config.TAGLINE_KO,
            "generated_at": "2026-09-05T00:00:00+00:00",
            "as_of": "2026-09-05",
            "tally": {"ALIVE": 1, "FADING": 1, "DEAD": 0, "TOO FEW TRADES": 0},
            "last_price": {"KRW-BTC_1d": 163000000.0},
            "rows": [
                {
                    "strategy_id": "sma_cross", "strategy_name": "SMA crossover", "type": "state",
                    "params": "10-50", "asset": "KRW-BTC", "timeframe": "1d",
                    "as_of": "2026-09-05", "verdict": "ALIVE",
                    "is": {"profit_factor": 1.5, "mdd": 40.0},
                    "oos": {"total_return": 0.3, "profit_factor": 1.4, "mdd": 20.0,
                            "n_trades": 35, "win_rate": 50.0, "bh_return": 0.2, "bh_mdd": 30.0,
                            "fee_drag": 0.01},
                    "suspicious": False, "edition": "ko",
                },
                {
                    "strategy_id": "dca_weekly", "strategy_name": "Weekly DCA (reference)",
                    "type": "reference", "params": "-", "asset": "KRW-BTC", "timeframe": "1d",
                    "as_of": "2026-09-05", "verdict": None,
                    "is": {"total_return": 0.4, "mdd": 25.0},
                    "oos": {"total_return": 0.25, "mdd": 15.0, "fee_drag": 0.005},
                    "suspicious": False, "edition": "ko",
                },
            ],
            "suspicious": [],
            "legal_disclaimer_ko": config.LEGAL_DISCLAIMER_KO,
            "repo_url": config.REPO_URL, "signup_url": "",
        }
        full_path = os.path.join(tmp, "full_index.html")
        build_site.build_index_ko(synthetic_payload, full_path)
        with open(full_path, encoding="utf-8") as f:
            full_html = f.read()
        _assert_page_ok(full_html, "full data")
        check("Korean full-data page shows the ALIVE badge as 생존",
              "생존" in full_html and "ALIVE" in full_html)
        check("Korean full-data page shows a Korean(English) strategy name",
              "이동평균 교차 (SMA crossover)" in full_html)
        check("Korean full-data page formats the last price in KRW",
              "₩163,000,000" in full_html, "KRW price formatting not found")

        # 3) Methodology page gets the same disclaimer-twice / banned-word treatment.
        meth_path = os.path.join(tmp, "methodology.html")
        build_site.build_methodology_ko(meth_path)
        with open(meth_path, encoding="utf-8") as f:
            meth_html = f.read()
        _assert_page_ok(meth_html, "methodology")


# ---------------------------------------------------------------------------
# Stocks edition (spec_v3 §A) — additive extension, nothing above this line is modified.
#
# Stooq/Yahoo are both network-blocked in this dev environment exactly like Bitstamp/Upbit, so
# these unit-test the pure parsers (_parse_stooq_csv, _parse_yahoo_chart_json) and the
# same-day-bar-drop helper (_drop_unclosed_stocks_bar) against hand-written fake payloads.
# ---------------------------------------------------------------------------

def test_stooq_parser():
    import fetch_data as fd

    good_csv = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,470.42,472.14,468.17,472.65,74882300\n"
        "2024-01-03,470.29,470.90,467.23,468.79,59991100\n"
        "2024-01-04,468.35,470.44,467.05,467.28,52117200\n"
    )
    df = fd._parse_stooq_csv(good_csv)
    check("Stooq parser: correct normalized columns",
          list(df.columns) == ["date", "open", "high", "low", "close", "volume"],
          f"got {list(df.columns)}")
    check("Stooq parser: 3 rows in, 3 rows out", len(df) == 3, f"got {len(df)}")
    check("Stooq parser: ascending order (oldest first)",
          list(df["date"]) == sorted(df["date"]))
    check("Stooq parser: field mapping correct (close on last row)",
          df["close"].iloc[-1] == 467.28, f"got {df['close'].iloc[-1]}")
    check("Stooq parser: oldest row's date is 2024-01-02",
          df["date"].iloc[0] == pd.Timestamp("2024-01-02"))

    # Malformed responses (Stooq's actual failure modes) must come back empty, never raise.
    check("Stooq parser: empty body -> empty frame", fd._parse_stooq_csv("").empty)
    check("Stooq parser: HTML error page -> empty frame",
          fd._parse_stooq_csv("<html><body>Exceeded the daily hits limit</body></html>").empty)
    check("Stooq parser: unexpected header -> empty frame",
          fd._parse_stooq_csv("Symbol,Date,Time\nSPY.US,2024-01-02,00:00\n").empty)
    check("Stooq parser: None input -> empty frame", fd._parse_stooq_csv(None).empty)


def test_yahoo_parser():
    import fetch_data as fd

    good_payload = {
        "chart": {
            "result": [{
                "timestamp": [1704196800, 1704283200, 1704369600],
                "indicators": {
                    "quote": [{
                        "open": [470.42, 470.29, None],
                        "high": [472.14, 470.90, 469.0],
                        "low": [468.17, 467.23, 466.0],
                        "close": [472.65, 468.79, 467.5],
                        "volume": [74882300, 59991100, 52117200],
                    }]
                },
            }],
            "error": None,
        }
    }
    df = fd._parse_yahoo_chart_json(good_payload)
    check("Yahoo parser: correct normalized columns",
          list(df.columns) == ["date", "open", "high", "low", "close", "volume"],
          f"got {list(df.columns)}")
    check("Yahoo parser: null-OHLC bar dropped (3 in, 2 out)", len(df) == 2, f"got {len(df)}")
    check("Yahoo parser: ascending order (oldest first)", list(df["date"]) == sorted(df["date"]))
    check("Yahoo parser: field mapping correct (first row close)",
          df["close"].iloc[0] == 472.65, f"got {df['close'].iloc[0]}")

    check("Yahoo parser: missing 'chart' key -> empty frame", fd._parse_yahoo_chart_json({}).empty)
    check("Yahoo parser: null result -> empty frame",
          fd._parse_yahoo_chart_json({"chart": {"result": None, "error": {"code": "Not Found"}}}).empty)
    check("Yahoo parser: None payload -> empty frame", fd._parse_yahoo_chart_json(None).empty)


# ---------------------------------------------------------------------------
# Robustness map (spec_v3 §C) — additive extension, nothing above this line is modified.
#
# This is a DIAGNOSTIC-ONLY feature: these tests check (1) the grid candidate rule matches
# spec_v3 §C's own wording, (2) the "MA pairs keep fast < slow" filter actually filters,
# (3) the grid never exceeds 3x3=9 points for a 2-parameter variant, (4) computing it never
# mutates the registered params dict it was given (that dict IS registry.py's own live object —
# corrupting it would corrupt the real backtest), and (5) it runs fast enough in practice on the
# local BTC 1d+4h data (spec_v3 §C's ~10-minute CI budget).
# ---------------------------------------------------------------------------

def test_robustness_neighbour_values():
    import robustness as rb

    check("robustness neighbours: k-type float (k=0.5) -> {0.4, 0.5, 0.6}",
          rb.neighbour_values("k", 0.5) == [0.4, 0.5, 0.6],
          f"got {rb.neighbour_values('k', 0.5)}")
    check("robustness neighbours: k-type float floors at 0.1 (k=0.1 -> no candidate below 0.1)",
          min(rb.neighbour_values("k", 0.1)) >= 0.1,
          f"got {rb.neighbour_values('k', 0.1)}")
    check("robustness neighbours: window int (n=200) -> {150, 200, 250}",
          rb.neighbour_values("n", 200) == [150, 200, 250],
          f"got {rb.neighbour_values('n', 200)}")
    check("robustness neighbours: window int floors at 2 (n=2 never produces 0 or negative)",
          min(rb.neighbour_values("n", 2)) >= 2, f"got {rb.neighbour_values('n', 2)}")
    frac = rb.neighbour_values("threshold", -0.05)
    check("robustness neighbours: fractional non-k param (threshold=-0.05) stays negative and "
          "is NOT rounded to an integer (would degenerate to 0)",
          all(v < 0 for v in frac), f"got {frac}")
    check("robustness neighbours: threshold=-0.05 middle candidate is the registered value",
          -0.05 in frac, f"got {frac}")


def test_robustness_ma_pair_and_grid_size():
    import robustness as rb

    df = load_raw("btc", "1d")
    n = len(df)
    oos_mask = pd.Series(False, index=df.index)
    oos_mask.iloc[n - 300:] = True  # a plausible-sized OOS-like window for this smoke test
    cost = 0.001

    grid = rb.evaluate_variant_grid("sma_cross", {"n_fast": 10, "n_slow": 50}, "state", None,
                                      df, oos_mask, cost)
    check("robustness sma_cross: grid never exceeds 9 points (2 numeric params)", len(grid) <= 9,
          f"got {len(grid)}")
    check("robustness sma_cross: grid is non-empty (base point always survives the fast<slow filter)",
          len(grid) >= 1)
    # Re-derive fast/slow from each grid point's own label to check the "keep fast < slow" filter.
    for g in grid:
        parts = dict(kv.split("=") for kv in g["params"].split(", "))
        check(f"robustness sma_cross: fast < slow held for grid point {g['params']}",
              int(parts["n_fast"]) < int(parts["n_slow"]))

    grid_1param = rb.evaluate_variant_grid("above_sma", {"n": 200}, "state", None,
                                             df, oos_mask, cost)
    check("robustness above_sma: 1 numeric param -> at most 3 grid points", len(grid_1param) <= 3,
          f"got {len(grid_1param)}")

    grid_none = rb.evaluate_variant_grid("bb_mr", {}, "state", None, df, oos_mask, cost)
    check("robustness bb_mr: no numeric params -> empty grid (nothing to vary)",
          grid_none == [], f"got {grid_none}")


def test_robustness_does_not_mutate_registered_params():
    import copy
    import robustness as rb

    df = load_raw("btc", "1d")
    n = len(df)
    oos_mask = pd.Series(False, index=df.index)
    oos_mask.iloc[n - 300:] = True

    base_params = {"n_fast": 10, "n_slow": 50}
    snapshot = copy.deepcopy(base_params)
    rb.compute_robustness("sma_cross", base_params, "state", None, df, oos_mask, 0.001)
    check("robustness: computing the grid never mutates the registered params dict it was given",
          base_params == snapshot, f"params dict changed to {base_params}")


def test_robustness_runtime_on_local_btc_data():
    """Not a correctness test — a runtime smoke-check against spec_v3 §C's own "~10 minutes in
    CI" budget, measured here on the local BTC 1d+4h data as this task's report requires. Well
    under budget is expected (OOS-window-only simulation loops, vectorized signal computation);
    if this ever regresses toward the budget, that's the signal to switch to 1-D grids per
    spec_v3 §C's own fallback instruction."""
    import time
    import registry as _reg
    import robustness as rb

    for asset, tf in [("btc", "1d"), ("btc", "4h")]:
        if resolve_path(asset, tf) is None:
            print(f"[SKIP] {asset} {tf}: no data file"); continue
        df = load_raw(asset, tf)
        n = len(df)
        oos_mask = pd.Series(False, index=df.index)
        oos_mask.iloc[max(0, n - 800):] = True
        cost = COST[asset]
        t0 = time.time()
        for sid, sname, stype, variant in _reg.iter_variants():
            rb.evaluate_variant_grid(sid, variant["params"], stype, variant.get("hold_n"),
                                       df, oos_mask, cost)
        dt = time.time() - t0
        check(f"robustness runtime {asset}/{tf}: full registry grid well under the 10-minute "
              f"CI budget ({dt:.2f}s)", dt < 60.0, f"took {dt:.2f}s")


def test_write_api_v1():
    """spec_v3 §B: _write_api_v1 must (1) write latest.json + a history/<as_of>.json snapshot,
    (2) build/merge history/index.json across repeated calls (dedup on as_of, newest first)
    rather than clobbering earlier weeks' entries, and (3) never touch another edition's files."""
    import json as _json
    import tempfile
    import run_weekly as rw

    with tempfile.TemporaryDirectory() as tmp:
        orig_docs_dir = config.DOCS_DIR
        config.DOCS_DIR = tmp
        try:
            rw._write_api_v1("en", {"as_of": "2026-08-01", "rows": [], "edition": "en"})
            rw._write_api_v1("en", {"as_of": "2026-08-08", "rows": [], "edition": "en"})
            # Re-writing the same as_of (e.g. a re-run the same week) must not duplicate it.
            rw._write_api_v1("en", {"as_of": "2026-08-08", "rows": [{"x": 1}], "edition": "en"})

            api_dir = os.path.join(tmp, "api", "v1", "en")
            check("write_api_v1: latest.json written",
                  os.path.exists(os.path.join(api_dir, "latest.json")))
            check("write_api_v1: latest.json reflects the most recent write",
                  _json.load(open(os.path.join(api_dir, "latest.json")))["rows"] == [{"x": 1}])
            check("write_api_v1: both history snapshots present",
                  os.path.exists(os.path.join(api_dir, "history", "2026-08-01.json"))
                  and os.path.exists(os.path.join(api_dir, "history", "2026-08-08.json")))

            with open(os.path.join(api_dir, "history", "index.json")) as f:
                idx = _json.load(f)
            check("write_api_v1: history/index.json has exactly 2 entries (dedup on as_of)",
                  len(idx["history"]) == 2, f"got {idx['history']}")
            check("write_api_v1: history/index.json sorted newest-first",
                  [e["as_of"] for e in idx["history"]] == ["2026-08-08", "2026-08-01"],
                  f"got {[e['as_of'] for e in idx['history']]}")

            check("write_api_v1: does not create a sibling edition's directory",
                  not os.path.exists(os.path.join(tmp, "api", "v1", "ko")))
        finally:
            config.DOCS_DIR = orig_docs_dir


def test_stocks_edition_pipeline():
    """End-to-end check of the stocks edition's row-building + page templates, using the same
    real SPY daily file the rest of this dev box already has locally
    (data_loader.load_raw("spy", "1d") — v1's own SPY series, not fetch_data.py's Stooq/Yahoo
    path). This is pure computation on already-local data (no network involved), so unlike
    fetch_ohlc_stooq/fetch_ohlc_yahoo it CAN be exercised end-to-end here: it verifies
    _build_rows_for_asset_tf_impl (252 bars_per_year, 0.02% cost) and build_site.build_index/
    build_methodology render a real stocks-edition page without error, using a real (if
    differently-sourced) series — not just the synthetic Korean-style payload used elsewhere."""
    import tempfile
    import run_weekly as rw
    import build_site

    if resolve_path("spy", "1d") is None:
        print("[SKIP] test_stocks_edition_pipeline: no local SPY file")
        return

    df = load_raw("spy", "1d")
    df = df[df["date"] >= pd.Timestamp(config.STOCKS_DATA_START)].reset_index(drop=True)
    as_of, rows, suspicious = rw._build_rows_for_asset_tf_impl(
        "SPY", "1d", df, config.COST["SPY"], config.STOCKS_BARS_PER_YEAR)
    for r in rows:
        r["edition"] = "stocks"

    check("Stocks pipeline: produces the full registry row count (22 variants + 2 reference)",
          len(rows) == _registry.count_variants() + 2, f"got {len(rows)}")

    payload = {
        "project_name": config.PROJECT_NAME, "edition": "stocks", "lang": "en",
        "tagline": config.TAGLINE, "generated_at": "2026-09-07T00:00:00+00:00",
        "as_of": str(as_of.date()), "oos_days": config.OOS_DAYS,
        "tally": __import__("verdict").tally([r["verdict"] for r in rows if r["verdict"]]),
        "rows": rows, "suspicious": suspicious,
        "legal_disclaimer": config.LEGAL_DISCLAIMER,
        "repo_url": config.REPO_URL, "signup_url": config.SIGNUP_URL,
    }

    with tempfile.TemporaryDirectory() as tmp:
        index_path = build_site.build_index(
            payload, out_path=os.path.join(tmp, "index.html"),
            assets=config.STOCKS_ASSETS, timeframes=config.STOCKS_TIMEFRAMES,
            lang_links='<a href="../index.html">English (crypto)</a>')
        meth_path = build_site.build_methodology(
            out_path=os.path.join(tmp, "methodology.html"), assets=config.STOCKS_ASSETS,
            lang_links='<a href="../methodology.html">English (crypto)</a>')
        with open(index_path, encoding="utf-8") as f:
            index_html = f.read()
        with open(meth_path, encoding="utf-8") as f:
            meth_html = f.read()

        check("Stocks index page: SPY section present", 'id="SPY-1d"' in index_html)
        check("Stocks index page: no QQQ rows rendered (no local QQQ file in this dev env)",
              'id="QQQ-1d"' not in index_html)
        check("Stocks index page: shows a verdict badge", "badge" in index_html)
        check("Stocks methodology page: SPY cost row present (0.02%)", "0.02%" in meth_html)
        check("Stocks methodology page: no KRW-BTC cost row leaked in (edition isolation)",
              "KRW-BTC" not in meth_html)


def test_drop_unclosed_stocks_bar():
    import fetch_data as fd

    df = pd.DataFrame({
        "date": [pd.Timestamp("2026-09-04"), pd.Timestamp("2026-09-05"), pd.Timestamp("2026-09-07")],
        "open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0], "low": [1.0, 2.0, 3.0],
        "close": [1.0, 2.0, 3.0], "volume": [1.0, 1.0, 1.0],
    })
    now_same_day = pd.Timestamp("2026-09-07T15:00:00")
    dropped = fd._drop_unclosed_stocks_bar(df, now=now_same_day)
    check("Stocks: today's (still-forming) bar is dropped",
          len(dropped) == 2 and dropped["date"].iloc[-1] == pd.Timestamp("2026-09-05"),
          f"got {list(dropped['date'])}")

    now_next_day = pd.Timestamp("2026-09-08T01:00:00")
    kept = fd._drop_unclosed_stocks_bar(df, now=now_next_day)
    check("Stocks: yesterday's (fully closed) bar is kept", len(kept) == 3, f"got {len(kept)}")

    check("Stocks: empty frame is a no-op",
          fd._drop_unclosed_stocks_bar(pd.DataFrame(columns=df.columns)).empty)


# ---------------------------------------------------------------------------
# spec_v3 §E: "Check my strategy" via GitHub Issues — parser/validator unit test, 5 good + 5 bad
# inputs (spec's own requirement). Uses check_issue.py's own parse_issue_body/validate directly
# (not the full run_check backtest, which needs local data files that may not exist for every
# asset in this dev environment) — this test is about the strict-regex parsing/validation gate,
# the part spec_v3 §E calls out as the injection-safety-critical piece.
# ---------------------------------------------------------------------------

def _issue_body(edition, asset, timeframe, strategy, params, checked=True):
    box = "[x]" if checked else "[ ]"
    return (
        f"### Edition\n\n{edition}\n\n"
        f"### Asset\n\n{asset}\n\n"
        f"### Timeframe\n\n{timeframe}\n\n"
        f"### Strategy type\n\n{strategy}\n\n"
        f"### Params\n\n{params}\n\n"
        f"### Confirmation\n\n"
        f"- {box} I understand this is an automated educational backtest, not investment advice.\n"
    )


def test_check_issue_parser_validator():
    import check_issue as ci

    good_cases = [
        ("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=10, n_slow=50"),
        ("en", "en:BTCUSD", "4h", "vol_breakout", "k=0.5"),
        ("stocks", "stocks:SPY", "1d", "macd", ""),
        ("ko", "ko:KRW-BTC", "1d", "rsi_uptrend", "n_sma=200"),
        ("en", "en:BTCUSD", "1d", "dip_pct", "threshold=-0.05, n_hold=5"),
    ]
    for edition, asset, tf, sid, params in good_cases:
        body = _issue_body(edition, asset, tf, sid, params, checked=True)
        fields = ci.parse_issue_body(body)
        try:
            normalized = ci.validate(fields)
            ok, detail = True, ""
        except ci.ValidationError as e:
            ok, detail = False, str(e)
        check(f"check_issue: good input accepted ({sid}, '{params}')", ok, detail)

    bad_cases = [
        # unknown strategy id
        ("en", "en:BTCUSD", "1d", "not_a_real_strategy", "", True),
        # fast >= slow
        ("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=50, n_slow=10", True),
        # k out of the 0.1..2.0 bound
        ("en", "en:BTCUSD", "1d", "vol_breakout", "k=5.0", True),
        # asset prefix doesn't match the edition field
        ("en", "ko:KRW-BTC", "1d", "rsi_uptrend", "n_sma=200", True),
        # confirmation checkbox not checked
        ("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=10, n_slow=50", False),
    ]
    for edition, asset, tf, sid, params, checked in bad_cases:
        body = _issue_body(edition, asset, tf, sid, params, checked=checked)
        fields = ci.parse_issue_body(body)
        try:
            ci.validate(fields)
            ok = False
        except ci.ValidationError:
            ok = True
        check(f"check_issue: bad input rejected ({sid}, '{params}', checked={checked})", ok)

    # Extra hardening checks called out by spec_v3 §E ("never eval user text; parse with a strict
    # regex"): a params field that tries to smuggle Python syntax must be rejected by the regex,
    # never evaluated.
    injection_attempts = ["__import__('os').system('echo hi')", "n_fast=10 and True",
                           "n_fast=10, n_slow=(50)"]
    for bad_params in injection_attempts:
        try:
            ci.parse_params(bad_params)
            ok = False
        except ci.ValidationError:
            ok = True
        check(f"check_issue: injection-shaped params string rejected ({bad_params!r})", ok)

    # Fixed-strategy params must be empty; non-empty params for a fixed strategy is an error.
    body = _issue_body("en", "en:BTCUSD", "1d", "macd", "n=5", checked=True)
    try:
        ci.validate(ci.parse_issue_body(body))
        ok = False
    except ci.ValidationError:
        ok = True
    check("check_issue: non-empty params for a fixed strategy rejected", ok)


def test_check_issue_dry_run_end_to_end():
    """The dry-run CLI path (spec_v3 §E: `python check_issue.py --body sample.md --dry-run`),
    exercised against real local BTCUSD data end-to-end (parse -> validate -> run -> render),
    without ever touching the network or GitHub."""
    import check_issue as ci

    body = _issue_body("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=10, n_slow=50", checked=True)
    comment_md, is_valid = ci.build_comment(body)
    check("check_issue: end-to-end dry run on real BTCUSD data succeeds", is_valid, comment_md)
    check("check_issue: dry-run comment carries the valid-status marker",
          "check-issue-status: valid" in comment_md)
    check("check_issue: dry-run comment shows the verdict line",
          "**Verdict:**" in comment_md)
    check("check_issue: dry-run comment links to methodology",
          config.PAGES_URL + "/methodology.html" in comment_md)

    bad_body = _issue_body("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=50, n_slow=10", checked=True)
    bad_comment, bad_valid = ci.build_comment(bad_body)
    check("check_issue: end-to-end dry run on bad input is rejected, not crashed", not bad_valid)
    check("check_issue: invalid comment carries the invalid-status marker",
          "check-issue-status: invalid" in bad_comment)


# ---------------------------------------------------------------------------
# This task's T2: paid "extended check" via Gumroad license keys — fake HTTP responses (valid,
# refunded, invalid, network error) for verify_gumroad_license, the free-path-unchanged guarantee
# when no license key is given, the unverified-key note, and the extended-check renderer's extra
# sections. GUMROAD_PRODUCT_ID/GUMROAD_URL and requests.post are saved/restored around every test
# so they can never leak into any other test in this file.
# ---------------------------------------------------------------------------

def _issue_body_with_license(edition, asset, timeframe, strategy, params, license_key="",
                               checked=True):
    box = "[x]" if checked else "[ ]"
    lic = license_key if license_key else "_No response_"
    return (
        f"### Edition\n\n{edition}\n\n"
        f"### Asset\n\n{asset}\n\n"
        f"### Timeframe\n\n{timeframe}\n\n"
        f"### Strategy type\n\n{strategy}\n\n"
        f"### Params\n\n{params}\n\n"
        f"### License key (optional, for extended check)\n\n{lic}\n\n"
        f"### Confirmation\n\n"
        f"- {box} I understand this is an automated educational backtest, not investment advice.\n"
    )


class _FakeGumroadResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_gumroad_verify_fake_http():
    import check_issue as ci
    import requests

    orig_post = requests.post
    saved_pid = os.environ.pop("GUMROAD_PRODUCT_ID", None)
    try:
        # 1. GUMROAD_PRODUCT_ID unset -> unverified with the spec's own clear message, and no
        #    HTTP call is even attempted (nothing to verify against).
        calls = []
        requests.post = lambda *a, **k: (calls.append(1), _FakeGumroadResponse(200, {"success": True}))[1]
        verified, reason = ci.verify_gumroad_license("ANY-KEY")
        check("gumroad: GUMROAD_PRODUCT_ID unset -> unverified", verified is False)
        check("gumroad: GUMROAD_PRODUCT_ID unset -> spec's exact clear message",
              reason == "extended checks are not enabled yet", reason)
        check("gumroad: GUMROAD_PRODUCT_ID unset -> no HTTP call made", len(calls) == 0)

        os.environ["GUMROAD_PRODUCT_ID"] = "prod_test123"

        # 2. valid, unrefunded purchase -> verified.
        requests.post = lambda *a, **k: _FakeGumroadResponse(
            200, {"success": True, "purchase": {"refunded": False, "chargebacked": False, "disputed": False}})
        verified, reason = ci.verify_gumroad_license("GOOD-KEY")
        check("gumroad: valid unrefunded purchase -> verified", verified is True, reason)

        # 3. refunded purchase -> NOT verified even though success: true.
        requests.post = lambda *a, **k: _FakeGumroadResponse(
            200, {"success": True, "purchase": {"refunded": True, "chargebacked": False, "disputed": False}})
        verified, reason = ci.verify_gumroad_license("REFUNDED-KEY")
        check("gumroad: refunded purchase -> unverified", verified is False)

        # 3b. chargebacked / disputed purchases -> also NOT verified.
        requests.post = lambda *a, **k: _FakeGumroadResponse(
            200, {"success": True, "purchase": {"refunded": False, "chargebacked": True, "disputed": False}})
        v_cb, _ = ci.verify_gumroad_license("CHARGEBACK-KEY")
        check("gumroad: chargebacked purchase -> unverified", v_cb is False)
        requests.post = lambda *a, **k: _FakeGumroadResponse(
            200, {"success": True, "purchase": {"refunded": False, "chargebacked": False, "disputed": True}})
        v_disp, _ = ci.verify_gumroad_license("DISPUTED-KEY")
        check("gumroad: disputed purchase -> unverified", v_disp is False)

        # 4. invalid key (Gumroad's own documented failure shape: success: false).
        requests.post = lambda *a, **k: _FakeGumroadResponse(404, {"success": False, "message": "That license does not exist for the provided product."})
        verified, reason = ci.verify_gumroad_license("BAD-KEY")
        check("gumroad: invalid key (success: false, HTTP 404) -> unverified", verified is False)

        # 5. network error -> unverified, never a crash.
        def raise_conn_error(*a, **k):
            raise requests.exceptions.ConnectionError("simulated network failure")
        requests.post = raise_conn_error
        verified, reason = ci.verify_gumroad_license("ANY-KEY")
        check("gumroad: network error -> unverified, no exception propagates", verified is False)
    finally:
        requests.post = orig_post
        if saved_pid is None:
            os.environ.pop("GUMROAD_PRODUCT_ID", None)
        else:
            os.environ["GUMROAD_PRODUCT_ID"] = saved_pid


def test_check_issue_license_free_path_unchanged():
    """spec's own requirement: 'free path unchanged byte-for-byte when no key'."""
    import check_issue as ci

    body_no_license_field = _issue_body("en", "en:BTCUSD", "1d", "sma_cross", "n_fast=10, n_slow=50")
    body_with_empty_license = _issue_body_with_license(
        "en", "en:BTCUSD", "1d", "sma_cross", "n_fast=10, n_slow=50", license_key="")

    comment_a, valid_a = ci.build_comment(body_no_license_field)
    comment_b, valid_b = ci.build_comment(body_with_empty_license)
    check("check_issue: free-check comment is byte-for-byte identical whether the license field "
          "is absent entirely or present-but-empty",
          valid_a and valid_b and comment_a == comment_b)
    check("check_issue: free-check comment (no key) has no license/extended-check mention",
          "License key" not in comment_a and "Extended check (licensed)" not in comment_a)


def test_check_issue_license_unverified_note():
    import check_issue as ci

    saved_pid = os.environ.pop("GUMROAD_PRODUCT_ID", None)
    saved_url = os.environ.pop("GUMROAD_URL", None)
    try:
        body = _issue_body_with_license("en", "en:BTCUSD", "1d", "sma_cross",
                                          "n_fast=10, n_slow=50", license_key="SOME-KEY")
        comment_md, is_valid = ci.build_comment(body)
        check("check_issue: an unverifiable license key still runs (and returns) the free check",
              is_valid)
        check("check_issue: unverified-key comment still has the free-check scorecard",
              "**Verdict:**" in comment_md)
        check("check_issue: unverified-key comment names the 'not enabled yet' reason "
              "(GUMROAD_PRODUCT_ID unset in this test)",
              "Extended checks are not enabled yet" in comment_md, comment_md)
        check("check_issue: unverified-key note says the free check ran instead",
              "ran the free check instead" in comment_md)
        check("check_issue: unverified-key comment has NO extended-check header",
              "Extended check (licensed)" not in comment_md)
        check("check_issue: with GUMROAD_URL unset, the note has no dangling 'Keys are sold at'",
              "Keys are sold at" not in comment_md)

        os.environ["GUMROAD_URL"] = "https://example.gumroad.com/l/netcheck-extended"
        comment_md2, _ = ci.build_comment(body)
        check("check_issue: GUMROAD_URL, when set, is included in the unverified-key note",
              "https://example.gumroad.com/l/netcheck-extended" in comment_md2)
    finally:
        if saved_pid is None:
            os.environ.pop("GUMROAD_PRODUCT_ID", None)
        else:
            os.environ["GUMROAD_PRODUCT_ID"] = saved_pid
        if saved_url is None:
            os.environ.pop("GUMROAD_URL", None)
        else:
            os.environ["GUMROAD_URL"] = saved_url


def test_check_issue_extended_check_sections():
    import check_issue as ci
    import requests

    orig_post = requests.post
    saved_pid = os.environ.pop("GUMROAD_PRODUCT_ID", None)
    try:
        os.environ["GUMROAD_PRODUCT_ID"] = "prod_test123"
        requests.post = lambda *a, **k: _FakeGumroadResponse(
            200, {"success": True, "purchase": {"refunded": False, "chargebacked": False, "disputed": False}})

        body = _issue_body_with_license("en", "en:BTCUSD", "1d", "sma_cross",
                                          "n_fast=10, n_slow=50", license_key="GOOD-KEY")
        comment_md, is_valid = ci.build_comment(body)
        check("check_issue: extended check with a verified license key succeeds",
              is_valid, comment_md[:400])
        check("check_issue: extended comment states 'Extended check (licensed)' at the top",
              comment_md.lstrip().startswith("## Extended check (licensed)"), comment_md[:80])
        check("check_issue: extended check covers BTCUSD 1d (the committed data)",
              "### BTCUSD 1d" in comment_md)
        check("check_issue: extended check covers BTCUSD 4h too (ALL timeframes, not just the "
              "one selected on the issue form)", "### BTCUSD 4h" in comment_md)
        check("check_issue: extended check notes ETHUSD was skipped (no local data in this dev env)",
              "ETHUSD" in comment_md and "Skipped" in comment_md)
        check("check_issue: extended check includes the full OOS trade list (collapsible)",
              "Full OOS trade list" in comment_md and "<details>" in comment_md)
        check("check_issue: extended check includes an OOS monthly-returns table",
              "OOS monthly returns" in comment_md)
        check("check_issue: extended check uses the wider 5x5 robustness grid, not the weekly "
              "page's 3x3", "Robustness map (5x5" in comment_md)
        check("check_issue: extended comment still carries the valid-status marker",
              "check-issue-status: valid" in comment_md)
        check("check_issue: extended comment keeps the same legal disclaimer as the free check",
              config.LEGAL_DISCLAIMER in comment_md)
        check("check_issue: extended comment links to methodology",
              config.PAGES_URL + "/methodology.html" in comment_md)
    finally:
        requests.post = orig_post
        if saved_pid is None:
            os.environ.pop("GUMROAD_PRODUCT_ID", None)
        else:
            os.environ["GUMROAD_PRODUCT_ID"] = saved_pid


# ---------------------------------------------------------------------------
# S1: programmatic SEO pages (seo_pages.py) — exercised against the real BTCUSD payload this dev
# box already has in results/latest.json (produced by an earlier run_weekly.py run), the same
# pattern test_stocks_edition_pipeline uses above.
# ---------------------------------------------------------------------------

def test_seo_pages_strategy_page_count_and_shape():
    import json as _json
    import tempfile
    import seo_pages as sp
    import registry as _reg

    latest_path = os.path.join(config.RESULTS_DIR, "latest.json")
    if not os.path.exists(latest_path):
        print("[SKIP] test_seo_pages_strategy_page_count_and_shape: no results/latest.json "
              "(run run_weekly.py --offline first)")
        return
    with open(latest_path, encoding="utf-8") as f:
        payload = _json.load(f)

    orig_docs, orig_docs_ko, orig_docs_stocks = config.DOCS_DIR, config.DOCS_DIR_KO, config.DOCS_DIR_STOCKS
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.DOCS_DIR_KO = os.path.join(tmp, "ko")
    config.DOCS_DIR_STOCKS = os.path.join(tmp, "stocks")
    sp.EDITION_OUT_DIR["en"] = config.DOCS_DIR
    sp.EDITION_OUT_DIR["ko"] = config.DOCS_DIR_KO
    sp.EDITION_OUT_DIR["stocks"] = config.DOCS_DIR_STOCKS
    try:
        manifest = sp.build_all({"en": payload, "ko": None, "stocks": None})
        n_assets = len({r["asset"] for r in payload["rows"]})
        expected = (_reg.count_variants() + _reg.count_popular_combo_variants()) * n_assets
        check("seo_pages: one page per (strategy variant incl. popular combos, asset)",
              len(manifest["en"]) == expected, f"got {len(manifest['en'])}, expected {expected}")
        check("seo_pages: ko/stocks produce zero pages when they have no data this run",
              manifest["ko"] == [] and manifest["stocks"] == [])

        sample = manifest["en"][0]
        page_path = os.path.join(config.DOCS_DIR, "s", sample["filename"])
        check("seo_pages: page file actually written", os.path.exists(page_path))
        with open(page_path, encoding="utf-8") as f:
            html_out = f.read()
        check("seo_pages: has a <title>", "<title>" in html_out)
        check("seo_pages: has a canonical link", 'rel="canonical"' in html_out)
        check("seo_pages: has a meta description", 'name="description"' in html_out)
        check("seo_pages: links back to the main table", 'href="../index.html"' in html_out)
        check("seo_pages: links to methodology", 'href="../methodology.html"' in html_out)
        check("seo_pages: links to the check-your-own-strategy issue template",
              "issues/new?template=check-strategy.yml" in html_out)
        check("seo_pages: carries the GoatCounter snippet", "goatcounter" in html_out)
        check("seo_pages: carries the legal disclaimer", config.LEGAL_DISCLAIMER in html_out)

        index_path = os.path.join(config.DOCS_DIR, "s", "index.html")
        check("seo_pages: writes an /s/ index page", os.path.exists(index_path))

        urls = [(config.PAGES_URL + "/index.html", payload["as_of"])]
        sitemap_path = sp.build_sitemap(manifest, urls)
        with open(sitemap_path, encoding="utf-8") as f:
            sitemap_xml = f.read()
        check("seo_pages: sitemap.xml is well-formed XML and non-trivial",
              sitemap_xml.startswith("<?xml") and sitemap_xml.count("<url>") == len(manifest["en"]) + 1)

        robots_path = sp.build_robots()
        with open(robots_path, encoding="utf-8") as f:
            robots_txt = f.read()
        check("seo_pages: robots.txt allows all and points at the sitemap",
              "Allow: /" in robots_txt and "Sitemap:" in robots_txt)
    finally:
        config.DOCS_DIR, config.DOCS_DIR_KO, config.DOCS_DIR_STOCKS = orig_docs, orig_docs_ko, orig_docs_stocks
        sp.EDITION_OUT_DIR["en"] = orig_docs
        sp.EDITION_OUT_DIR["ko"] = orig_docs_ko
        sp.EDITION_OUT_DIR["stocks"] = orig_docs_stocks


def test_seo_pages_no_banned_korean_words():
    """A synthetic Korean payload (the real dev box has zero Upbit data offline — see README) run
    through the exact same seo_pages code path as the real English one above, checked for the four
    banned words the same way test_korean_page_disclaimer_and_banned_words already checks the main
    Korean pages."""
    import copy
    import json as _json
    import tempfile
    import seo_pages as sp

    latest_path = os.path.join(config.RESULTS_DIR, "latest.json")
    if not os.path.exists(latest_path):
        print("[SKIP] test_seo_pages_no_banned_korean_words: no results/latest.json")
        return
    with open(latest_path, encoding="utf-8") as f:
        payload = _json.load(f)
    ko_payload = copy.deepcopy(payload)
    ko_payload["edition"] = "ko"
    for r in ko_payload["rows"] + ko_payload["popular_combos"]:
        r["asset"] = "KRW-BTC" if r["asset"] == "BTCUSD" else r["asset"]
        r["edition"] = "ko"

    orig_docs, orig_docs_ko = config.DOCS_DIR, config.DOCS_DIR_KO
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.DOCS_DIR_KO = os.path.join(tmp, "ko")
    sp.EDITION_OUT_DIR["en"] = config.DOCS_DIR
    sp.EDITION_OUT_DIR["ko"] = config.DOCS_DIR_KO
    try:
        manifest = sp.build_all({"ko": ko_payload})
        check("seo_pages: ko synthetic payload produces pages", len(manifest["ko"]) > 0)
        banned = ["추천", "수익 보장", "확실", "필승"]
        for page in manifest["ko"]:
            path = os.path.join(config.DOCS_DIR_KO, "s", page["filename"])
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for word in banned:
                check(f"seo_pages ko: no banned word '{word}' in {page['filename']}",
                      word not in text)
    finally:
        config.DOCS_DIR, config.DOCS_DIR_KO = orig_docs, orig_docs_ko
        sp.EDITION_OUT_DIR["en"] = orig_docs
        sp.EDITION_OUT_DIR["ko"] = orig_docs_ko


# ---------------------------------------------------------------------------
# R1: pre-registration ledger (ledger.py, registry_ledger.json, docs/registry.html)
# ---------------------------------------------------------------------------

def test_ledger_matches_registry_bijection():
    """Every tradeable registry variant (registry.iter_variants() + iter_popular_combo_variants()
    — the same universe verdict.py ever badges) has exactly one ledger entry, and vice versa: no
    ledger entry that doesn't correspond to a current registry variant. REFERENCE rows are excluded
    from both sides on purpose (they never get a verdict either)."""
    import registry as _reg
    import ledger as _ldg

    expected_ids = {
        f"{sid}:{variant['params_str']}"
        for sid, _sname, _stype, variant in _reg.iter_variants()
    } | {
        f"{sid}:{variant['params_str']}"
        for sid, _sname, _stype, variant in _reg.iter_popular_combo_variants()
    }
    ledger_entries = _ldg.load_ledger()
    ledger_ids = {e["id"] for e in ledger_entries}

    check("ledger: registry_ledger.json is non-empty", len(ledger_entries) > 0)
    check("ledger: no duplicate ids", len(ledger_ids) == len(ledger_entries))
    missing_from_ledger = expected_ids - ledger_ids
    extra_in_ledger = ledger_ids - expected_ids
    check("ledger: every registry variant has a ledger entry",
          not missing_from_ledger, f"missing: {sorted(missing_from_ledger)}")
    check("ledger: every ledger entry corresponds to a current registry variant",
          not extra_in_ledger, f"extra: {sorted(extra_in_ledger)}")

    for e in ledger_entries:
        check(f"ledger {e['id']}: has registered_on/registered_commit/source/rule_text",
              bool(e.get("registered_on")) and bool(e.get("registered_commit"))
              and e.get("source") in ("textbook", "popular_combo", "community")
              and bool(e.get("rule_text")))


def test_ledger_immutable_against_snapshot():
    """No entry present in the checked-in snapshot (registry_ledger.snapshot.json) may ever differ
    from the live ledger (registry_ledger.json) — this is the mechanical enforcement of REGISTRY.md
    rule 2 ("no edits after registration"). New entries (present in the live ledger but not yet in
    the snapshot — e.g. a newly-accepted community proposal) are fine and expected over time; a
    changed EXISTING entry is not."""
    import ledger as _ldg

    live = {e["id"]: e for e in _ldg.load_ledger(_ldg.LEDGER_PATH)}
    snapshot = {e["id"]: e for e in _ldg.load_ledger(_ldg.SNAPSHOT_PATH)}

    check("ledger snapshot: file exists and is non-empty", len(snapshot) > 0)
    check("ledger: live ledger contains every snapshotted id",
          set(snapshot) <= set(live), f"missing: {sorted(set(snapshot) - set(live))}")

    for eid, snap_entry in snapshot.items():
        live_entry = live.get(eid)
        if live_entry is None:
            continue
        for field in ("params", "params_str", "source", "registered_on", "registered_commit",
                      "rule_text", "strategy_id", "strategy_name"):
            check(f"ledger immutability {eid}.{field}: unchanged since snapshot",
                  live_entry.get(field) == snap_entry.get(field),
                  f"live={live_entry.get(field)!r} snapshot={snap_entry.get(field)!r}")


def test_registry_page_builds_and_no_banned_korean_words():
    import tempfile
    import build_site as bs

    orig_docs, orig_docs_ko = config.DOCS_DIR, config.DOCS_DIR_KO
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.DOCS_DIR_KO = os.path.join(tmp, "ko")
    try:
        en_path = bs.build_registry_page()
        ko_path = bs.build_registry_page_ko()
        check("registry page: docs/registry.html written", os.path.exists(en_path))
        check("registry page: docs/ko/registry.html written", os.path.exists(ko_path))

        with open(en_path, encoding="utf-8") as f:
            en_html = f.read()
        check("registry page: has a <title>", "<title>" in en_html)
        check("registry page: links to propose-strategy.yml issue template",
              "issues/new?template=propose-strategy.yml" in en_html)
        check("registry page: lists every ledger entry's id",
              all(f">{e['id']}<" in en_html for e in __import__("ledger").load_ledger()))

        with open(ko_path, encoding="utf-8") as f:
            ko_html = f.read()
        banned = ["추천", "수익 보장", "확실", "필승"]
        for word in banned:
            check(f"registry page ko: no banned word '{word}'", word not in ko_html)
        check("registry page ko: carries the Korean disclaimer",
              config.LEGAL_DISCLAIMER_KO in ko_html)
        check("registry page ko: disclaimer appears at top and bottom",
              ko_html.count(config.LEGAL_DISCLAIMER_KO) == 2)
    finally:
        config.DOCS_DIR, config.DOCS_DIR_KO = orig_docs, orig_docs_ko


# ---------------------------------------------------------------------------
# R2: Strategy Decay Index (decay.py, charts.py, docs/index-history.html)
# ---------------------------------------------------------------------------

def test_decay_index_computation():
    import json as _json
    import tempfile
    import decay as dc
    import verdict as vd

    tmp = tempfile.mkdtemp()
    # Week 1: 20 alive, 30 fading, 40 dead, 10 too-few (n=100) -> decay_index = 40/90.
    with open(os.path.join(tmp, "2026-08-01.json"), "w") as f:
        _json.dump({"tally": {vd.ALIVE: 20, vd.FADING: 30, vd.DEAD: 40, vd.TOO_FEW: 10}}, f)
    # Week 2, Korean edition file naming (ko_<as_of>.json) — must NOT be picked up by the "en"
    # pattern, and its own all-zero tally must be skipped (no data that week, not "0% dead").
    with open(os.path.join(tmp, "ko_2026-08-08.json"), "w") as f:
        _json.dump({"tally": {vd.ALIVE: 0, vd.FADING: 0, vd.DEAD: 0, vd.TOO_FEW: 0}}, f)
    # Week 3 (en): all TOO FEW (n=5) -> ge10 denominator is 0 -> decay_index must be None, not a
    # ZeroDivisionError and not silently 0.
    with open(os.path.join(tmp, "2026-08-08.json"), "w") as f:
        _json.dump({"tally": {vd.ALIVE: 0, vd.FADING: 0, vd.DEAD: 0, vd.TOO_FEW: 5}}, f)
    # A non-matching filename must be ignored outright.
    with open(os.path.join(tmp, "not-a-history-file.txt"), "w") as f:
        f.write("junk")

    points = dc.compute_index_history("en", history_dir=tmp)
    check("decay: en pattern ignores the ko_-prefixed file and the .txt file",
          len(points) == 2, f"got {len(points)}")
    check("decay: points sorted ascending by as_of",
          [p["as_of"] for p in points] == ["2026-08-01", "2026-08-08"])
    p1 = points[0]
    check("decay: week1 shares (20/30/40/10 of 100)",
          abs(p1["alive"] - 0.20) < 1e-9 and abs(p1["fading"] - 0.30) < 1e-9
          and abs(p1["dead"] - 0.40) < 1e-9 and abs(p1["too_few"] - 0.10) < 1e-9
          and p1["n"] == 100)
    check("decay: week1 decay_index = dead / (alive+fading+dead) = 40/90",
          abs(p1["decay_index"] - (40 / 90)) < 1e-9)
    p2 = points[1]
    check("decay: week3 (all TOO FEW) has decay_index=None, not 0 or an error", p2["decay_index"] is None)

    ko_points = dc.compute_index_history("ko", history_dir=tmp)
    check("decay: ko pattern picks up ko_2026-08-08.json, and its all-zero tally is skipped",
          ko_points == [])

    out_path = dc.write_index_history("en", out_root=os.path.join(tmp, "api"), history_dir=tmp)
    check("decay: write_index_history writes a file", os.path.exists(out_path))
    loaded = dc.load_index_history("en", out_root=os.path.join(tmp, "api"))
    check("decay: load_index_history round-trips what was written", loaded == points)


def test_decay_index_page_builds_and_no_banned_korean_words():
    import tempfile
    import build_site as bs

    latest_path = os.path.join(config.RESULTS_DIR, "latest.json")
    if not os.path.exists(latest_path):
        print("[SKIP] test_decay_index_page_builds_and_no_banned_korean_words: no results/latest.json")
        return

    orig_docs, orig_docs_ko = config.DOCS_DIR, config.DOCS_DIR_KO
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.DOCS_DIR_KO = os.path.join(tmp, "ko")
    try:
        import decay as dc
        dc.write_index_history("en", out_root=os.path.join(tmp, "api", "v1"))
        dc.write_index_history("ko", out_root=os.path.join(tmp, "api", "v1"))
        dc.write_index_history("stocks", out_root=os.path.join(tmp, "api", "v1"))

        en_path = bs.build_index_history_page()
        ko_path = bs.build_index_history_page_ko()
        check("index-history page: docs/index-history.html written", os.path.exists(en_path))
        check("index-history page: docs/ko/index-history.html written", os.path.exists(ko_path))

        with open(en_path, encoding="utf-8") as f:
            en_html = f.read()
        check("index-history page: has an <svg> chart (en has archived data)", "<svg" in en_html)
        check("index-history page: mentions both en and stocks edition labels",
              "English (crypto" in en_html and "Stocks (SPY, QQQ)" in en_html)

        with open(ko_path, encoding="utf-8") as f:
            ko_html = f.read()
        banned = ["추천", "수익 보장", "확실", "필승"]
        for word in banned:
            check(f"index-history page ko: no banned word '{word}'", word not in ko_html)
        check("index-history page ko: carries the Korean disclaimer",
              config.LEGAL_DISCLAIMER_KO in ko_html)
    finally:
        config.DOCS_DIR, config.DOCS_DIR_KO = orig_docs, orig_docs_ko


def test_seo_pages_sparkline_at_3plus_history_points():
    """A synthetic 3-week history (distinct OOS PF each week for the same variant) must produce a
    sparkline on that strategy's page; a synthetic 2-week history must not (task R2's own
    threshold: ">=3 history points")."""
    import json as _json
    import tempfile
    import seo_pages as sp

    def _fake_payload(as_of, pf):
        return {
            "as_of": as_of,
            "rows": [{
                "strategy_id": "sma_cross", "strategy_name": "SMA crossover", "params": "10-50",
                "asset": "BTCUSD", "timeframe": "1d", "type": "state", "verdict": "FADING",
                "is": {}, "oos": {"profit_factor": pf, "n_trades": 15},
            }],
            "popular_combos": [],
        }

    orig_docs, orig_hist = config.DOCS_DIR, config.HISTORY_DIR
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.HISTORY_DIR = os.path.join(tmp, "history")
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    sp.EDITION_OUT_DIR["en"] = config.DOCS_DIR
    try:
        for as_of, pf in [("2026-07-01", 1.1), ("2026-07-08", 1.4)]:
            with open(os.path.join(config.HISTORY_DIR, f"{as_of}.json"), "w") as f:
                _json.dump(_fake_payload(as_of, pf), f)
        latest = _fake_payload("2026-07-08", 1.4)
        with open(os.path.join(config.HISTORY_DIR, "2026-07-08.json"), "w") as f:
            _json.dump(latest, f)
        manifest = sp.build_all({"en": latest})
        page_path = os.path.join(config.DOCS_DIR, "s", manifest["en"][0]["filename"])
        with open(page_path, encoding="utf-8") as f:
            html_2wk = f.read()
        check("sparkline: NOT shown with only 2 history points", 'class="sparkline"' not in html_2wk)

        with open(os.path.join(config.HISTORY_DIR, "2026-07-15.json"), "w") as f:
            _json.dump(_fake_payload("2026-07-15", 1.8), f)
        latest3 = _fake_payload("2026-07-15", 1.8)
        manifest3 = sp.build_all({"en": latest3})
        page_path3 = os.path.join(config.DOCS_DIR, "s", manifest3["en"][0]["filename"])
        with open(page_path3, encoding="utf-8") as f:
            html_3wk = f.read()
        check("sparkline: shown with 3 history points", 'class="sparkline"' in html_3wk)
        check("sparkline: contains an <svg>", "<svg" in html_3wk)
    finally:
        config.DOCS_DIR, config.HISTORY_DIR = orig_docs, orig_hist
        sp.EDITION_OUT_DIR["en"] = orig_docs


# ---------------------------------------------------------------------------
# S2: weekly digest (digest.py)
# ---------------------------------------------------------------------------

def test_digest_first_week_and_flips():
    import json as _json
    import digest as dg

    curr = {
        "as_of": "2026-09-08",
        "tally": {"ALIVE": 1, "FADING": 1, "DEAD": 0, "TOO FEW TRADES": 0},
        "rows": [
            {"strategy_id": "sma_cross", "strategy_name": "SMA crossover", "params": "10-50",
             "asset": "BTCUSD", "timeframe": "1d", "type": "state", "verdict": "ALIVE",
             "oos": {"fee_drag": 0.01, "n_trades": 40, "profit_factor": 1.5}},
            {"strategy_id": "macd", "strategy_name": "MACD signal cross", "params": "12-26-9",
             "asset": "BTCUSD", "timeframe": "1d", "type": "state", "verdict": "FADING",
             "oos": {"fee_drag": 0.2, "n_trades": 35, "profit_factor": 1.05}},
        ],
        "popular_combos": [],
    }
    prev = {
        "as_of": "2026-09-01",
        "tally": {"ALIVE": 0, "FADING": 2, "DEAD": 0, "TOO FEW TRADES": 0},
        "rows": [
            {"strategy_id": "sma_cross", "strategy_name": "SMA crossover", "params": "10-50",
             "asset": "BTCUSD", "timeframe": "1d", "type": "state", "verdict": "FADING",
             "oos": {"fee_drag": 0.01, "n_trades": 38, "profit_factor": 1.1}},
            {"strategy_id": "macd", "strategy_name": "MACD signal cross", "params": "12-26-9",
             "asset": "BTCUSD", "timeframe": "1d", "type": "state", "verdict": "FADING",
             "oos": {"fee_drag": 0.15, "n_trades": 33, "profit_factor": 1.02}},
        ],
        "popular_combos": [],
    }

    orig_hist_dir = config.HISTORY_DIR
    import tempfile
    tmp = tempfile.mkdtemp()
    config.HISTORY_DIR = tmp
    try:
        d_first = dg.compute_digest("en", curr)
        check("digest: first week (no history file at all) has no flips and first_week=True",
              d_first["first_week"] is True and d_first["flips"] == [])

        with open(os.path.join(tmp, "2026-09-01.json"), "w") as f:
            _json.dump(prev, f)
        d_second = dg.compute_digest("en", curr)
        check("digest: second week finds the previous snapshot",
              d_second["first_week"] is False and d_second["prev_as_of"] == "2026-09-01")
        check("digest: detects exactly one verdict flip (sma_cross FADING -> ALIVE)",
              len(d_second["flips"]) == 1 and d_second["flips"][0]["strategy_id"] == "sma_cross"
              and d_second["flips"][0]["from"] == "FADING" and d_second["flips"][0]["to"] == "ALIVE")
        check("digest: best/worst OOS PF picked only among rows with >=30 trades",
              d_second["best_pf"]["strategy_id"] == "sma_cross"
              and d_second["worst_pf"]["strategy_id"] == "macd")

        title = dg.digest_title_en(d_second)
        check("digest: title matches the required 'Week of <as_of>: N alive / ...' shape",
              title.startswith("Week of 2026-09-08:") and "what changed" in title)
        liners = dg.one_liners_en(d_second)
        check("digest: exactly 3 one-liners", len(liners) == 3, f"got {len(liners)}")
        for s in liners:
            check(f"digest: one-liner has no opinion/advice language ({s[:40]}...)",
                  not any(w in s.lower() for w in ["should", "recommend", "guarantee", "advice"]))
    finally:
        config.HISTORY_DIR = orig_hist_dir


def test_digest_build_writes_pages_and_feeds():
    import json as _json
    import tempfile
    import digest as dg

    latest_path = os.path.join(config.RESULTS_DIR, "latest.json")
    if not os.path.exists(latest_path):
        print("[SKIP] test_digest_build_writes_pages_and_feeds: no results/latest.json")
        return
    with open(latest_path, encoding="utf-8") as f:
        payload = _json.load(f)

    orig_docs, orig_results, orig_hist = config.DOCS_DIR, config.RESULTS_DIR, config.HISTORY_DIR
    tmp = tempfile.mkdtemp()
    config.DOCS_DIR = tmp
    config.RESULTS_DIR = os.path.join(tmp, "results")
    config.HISTORY_DIR = os.path.join(config.RESULTS_DIR, "history")
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    dg.DIGEST_DIR = os.path.join(config.RESULTS_DIR, "digest")
    orig_subdir = dict(dg.EDITION_DOCS_SUBDIR)
    dg.EDITION_DOCS_SUBDIR["en"] = config.DOCS_DIR
    dg.EDITION_DOCS_SUBDIR["ko"] = os.path.join(tmp, "ko")
    dg.EDITION_DOCS_SUBDIR["stocks"] = os.path.join(tmp, "stocks")
    try:
        out = dg.build_all({"en": payload, "ko": None, "stocks": None})
        check("digest: build_for_edition returns a digest dict for en", out["en"] is not None)
        check("digest: returns None for editions with no data", out["ko"] is None and out["stocks"] is None)

        page_path = os.path.join(config.DOCS_DIR, "digest", f"{payload['as_of']}.html")
        check("digest: per-as_of HTML page written", os.path.exists(page_path))
        check("digest: digest index page written",
              os.path.exists(os.path.join(config.DOCS_DIR, "digest", "index.html")))

        feed_xml_path = os.path.join(config.DOCS_DIR, "feed.xml")
        check("digest: docs/feed.xml written", os.path.exists(feed_xml_path))
        with open(feed_xml_path, encoding="utf-8") as f:
            feed_xml = f.read()
        check("digest: feed.xml is RSS 2.0 with full HTML content per item",
              "<rss version=\"2.0\"" in feed_xml and "<content:encoded>" in feed_xml
              and "<item>" in feed_xml)

        feed_json_path = os.path.join(config.DOCS_DIR, "feed.json")
        check("digest: docs/feed.json written", os.path.exists(feed_json_path))
        with open(feed_json_path, encoding="utf-8") as f:
            feed_json = _json.load(f)
        check("digest: feed.json is JSON Feed 1.1 with >=1 item",
              feed_json.get("version") == "https://jsonfeed.org/version/1.1"
              and len(feed_json.get("items", [])) >= 1
              and "content_html" in feed_json["items"][0])
    finally:
        config.DOCS_DIR, config.RESULTS_DIR, config.HISTORY_DIR = orig_docs, orig_results, orig_hist
        dg.DIGEST_DIR = os.path.join(config.RESULTS_DIR, "digest")
        dg.EDITION_DOCS_SUBDIR.update(orig_subdir)




# ---------------------------------------------------------------------------
# S3: optional publishers (publish.py) — every provider must exit 0 with no secret configured,
# and every provider's payload shape is checked against a monkeypatched requests.post (never a
# real network call: this dev environment blocks every one of these hosts anyway).
# ---------------------------------------------------------------------------

_PUBLISH_ENV_KEYS = [
    "BUTTONDOWN_API_KEY", "BUTTONDOWN_SEND_KO", "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD",
    "MASTODON_INSTANCE", "MASTODON_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY", "HF_TOKEN",
    "HF_DATASET_REPO",
]


def test_publish_no_secrets_exits_zero():
    import publish

    saved = {k: os.environ.pop(k, None) for k in _PUBLISH_ENV_KEYS}
    try:
        check("publish buttondown: returns 0 with no secret", publish.cmd_buttondown() == 0)
        check("publish bluesky: returns 0 with no secret", publish.cmd_bluesky() == 0)
        check("publish mastodon: returns 0 with no secret", publish.cmd_mastodon() == 0)
        check("publish kaggle: returns 0 with no secret", publish.cmd_kaggle() == 0)
        check("publish huggingface: returns 0 with no secret", publish.cmd_huggingface() == 0)
        check("publish all: returns 0 with no secrets at all", publish.main(["all"]) == 0)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_publish_payload_shapes_fake_http():
    import json as _json
    import tempfile
    import publish
    import digest as dg

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers})

        class _Resp:
            status_code = 200
            text = "{}"

            def raise_for_status(self):
                pass

            def json(self):
                return {"accessJwt": "fake-jwt", "did": "did:plc:fake"}

        return _Resp()

    orig_post = publish.requests.post
    orig_digest_dir = dg.DIGEST_DIR
    tmp = tempfile.mkdtemp()
    dg.DIGEST_DIR = tmp
    fake_digest = {
        "edition": "en", "as_of": "2026-09-05",
        "tally": {"ALIVE": 8, "FADING": 17, "DEAD": 12, "TOO FEW TRADES": 7},
        "title": "Week of 2026-09-05: 8 alive / 17 fading / 12 dead — what changed",
        "markdown": "# test digest\n- one\n- two", "html_body": "<ul><li>x</li></ul>",
    }
    with open(os.path.join(tmp, "en_2026-09-05.json"), "w") as f:
        _json.dump(fake_digest, f)

    env_updates = {
        "BUTTONDOWN_API_KEY": "tok123", "BLUESKY_HANDLE": "user.bsky.social",
        "BLUESKY_APP_PASSWORD": "app-pass", "MASTODON_INSTANCE": "https://mastodon.example",
        "MASTODON_TOKEN": "mtok",
    }
    saved = {k: os.environ.get(k) for k in env_updates}
    os.environ.update(env_updates)
    try:
        publish.requests.post = fake_post
        publish.cmd_buttondown()
        publish.cmd_bluesky()
        publish.cmd_mastodon()
    finally:
        publish.requests.post = orig_post
        dg.DIGEST_DIR = orig_digest_dir
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    bd_call = next((c for c in calls if c["url"] == publish.BUTTONDOWN_URL), None)
    check("publish buttondown: posts to api.buttondown.com/v1/emails", bd_call is not None)
    check("publish buttondown: Authorization: Token <key>",
          bool(bd_call) and bd_call["headers"].get("Authorization", "") == "Token tok123")
    check("publish buttondown: body has subject/body/status=about_to_send",
          bool(bd_call) and {"subject", "body", "status"} <= set(bd_call["json"].keys())
          and bd_call["json"]["status"] == "about_to_send")

    sess_call = next((c for c in calls if c["url"] == publish.BLUESKY_SESSION_URL), None)
    check("publish bluesky: createSession posts identifier/password",
          bool(sess_call) and {"identifier", "password"} <= set(sess_call["json"].keys()))
    rec_call = next((c for c in calls if c["url"] == publish.BLUESKY_RECORD_URL), None)
    check("publish bluesky: creates an app.bsky.feed.post record with a link facet",
          bool(rec_call) and rec_call["json"]["record"].get("$type") == "app.bsky.feed.post"
          and len(rec_call["json"]["record"].get("facets") or []) == 1
          and rec_call["headers"].get("Authorization") == "Bearer fake-jwt")

    masto_call = next((c for c in calls if c["url"].endswith("/api/v1/statuses")), None)
    check("publish mastodon: posts {status: ...} with Bearer auth",
          bool(masto_call) and "status" in masto_call["json"]
          and masto_call["headers"].get("Authorization") == "Bearer mtok")
    check("publish: no subcommand exceeded Bluesky's 300-char post limit",
          bool(rec_call) and len(rec_call["json"]["record"]["text"].encode("utf-8")) <= 300)




if __name__ == "__main__":
    test_no_lookahead_ma_cross()
    test_no_lookahead_vol_breakout()
    test_sanity_cost()
    test_no_lookahead_registry()
    test_no_lookahead_popular_combos()
    test_ichimoku_shift_explicit()
    test_heikin_ashi_explicit()
    test_no_lookahead_reference_rows()
    test_indicator_spot_checks()
    test_upbit_parser()
    test_korean_page_disclaimer_and_banned_words()
    test_stooq_parser()
    test_yahoo_parser()
    test_drop_unclosed_stocks_bar()
    test_stocks_edition_pipeline()
    test_write_api_v1()
    test_robustness_neighbour_values()
    test_robustness_ma_pair_and_grid_size()
    test_robustness_does_not_mutate_registered_params()
    test_robustness_runtime_on_local_btc_data()
    test_check_issue_parser_validator()
    test_check_issue_dry_run_end_to_end()
    test_gumroad_verify_fake_http()
    test_check_issue_license_free_path_unchanged()
    test_check_issue_license_unverified_note()
    test_check_issue_extended_check_sections()
    test_seo_pages_strategy_page_count_and_shape()
    test_seo_pages_no_banned_korean_words()
    test_ledger_matches_registry_bijection()
    test_ledger_immutable_against_snapshot()
    test_registry_page_builds_and_no_banned_korean_words()
    test_decay_index_computation()
    test_decay_index_page_builds_and_no_banned_korean_words()
    test_seo_pages_sparkline_at_3plus_history_points()
    test_digest_first_week_and_flips()
    test_digest_build_writes_pages_and_feeds()
    test_publish_no_secrets_exits_zero()
    test_publish_payload_shapes_fake_http()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} TEST(S) FAILED:")
        for name, detail in FAILURES:
            print(f" - {name}: {detail}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED (pandas-engine tests; run crosscheck_bt.py separately for §6.2)")
        sys.exit(0)
