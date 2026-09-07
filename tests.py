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


if __name__ == "__main__":
    test_no_lookahead_ma_cross()
    test_no_lookahead_vol_breakout()
    test_sanity_cost()
    test_no_lookahead_registry()
    test_no_lookahead_reference_rows()
    test_indicator_spot_checks()
    test_upbit_parser()
    test_korean_page_disclaimer_and_banned_words()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} TEST(S) FAILED:")
        for name, detail in FAILURES:
            print(f" - {name}: {detail}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED (pandas-engine tests; run crosscheck_bt.py separately for §6.2)")
        sys.exit(0)
