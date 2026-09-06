"""
crosscheck_bt.py — §6.2 two-engine cross-check for ma_cross only.

Compares the pandas engine (engine.simulate_ma_cross) against backtesting.py, on the same data
and same cost, over one continuous range per (asset, timeframe, params): from the IS start
through the OOS end. A single continuous range is used (rather than the IS/OOS-reset convention
used for reported results) so both engines carry a genuinely identical, uninterrupted position
history — the IS/OOS equity reset at the period boundary is a reporting convention (see
README.md), not a claim about the underlying execution mechanics, and this check is about
mechanics: SMA-crossover entries/exits at next-bar open, and cost application.

backtesting.py settings, per spec §6.2:
  cash        = 1e9 (large, to minimize integer-share rounding)
  commission  = cost (per spec's per-side cost)
  exclusive_orders = True
  trade_on_close   = False (default; entries/exits fill at the next bar's open)

Comparison: n_trades must match exactly; total_return must match within 2% relative error.
Any remaining difference is explained in README.md (typically integer-share rounding in
backtesting.py, which our pandas engine does not model — we assume fractional shares).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

from data_loader import load_raw, PERIODS, COST
from strategies import ma_cross_target_state, MA_CROSS_PARAMS
from engine import simulate_ma_cross

CASH = 1e9


def sma(values, n):
    s = pd.Series(values)
    return s.rolling(n, min_periods=n).mean().to_numpy()


def make_strategy(n_fast, n_slow, block_until_idx):
    """
    block_until_idx: the 0-based position WITHIN THE FED SUB-FRAME of the last warm-up bar
    (i.e. absolute start_idx - 1). No order may be placed for any bar before that position, so
    the strategy is guaranteed flat going into that bar's decision — exactly mirroring the pandas
    engine's per-period reset (local `position = 0` at period start, decision read from the
    full-history signal at position start_idx-1). This reproduces "if already long at the first
    bar of the period, enter at that bar's open" without special-casing it.
    """
    class SmaCross(Strategy):
        def init(self):
            close = self.data.Close
            self.fast = self.I(sma, close, n_fast)
            self.slow = self.I(sma, close, n_slow)

        def next(self):
            i = len(self.data) - 1
            if i < block_until_idx:
                return
            if np.isnan(self.fast[-1]) or np.isnan(self.slow[-1]):
                return
            long_signal = self.fast[-1] > self.slow[-1]
            if long_signal and not self.position:
                self.buy()
            elif not long_signal and self.position:
                self.position.close()

    return SmaCross


def run_backtesting_engine(df: pd.DataFrame, start_idx: int, end_idx: int, n_fast: int,
                            n_slow: int, cost: float):
    """
    Feed backtesting.py the FULL price history from row 0 through end_idx, so its SMA at every
    bar exactly matches the pandas engine's full-series computation (a fixed-window rolling mean
    depends only on the trailing window, not on how much history precedes it). The strategy is
    blocked from trading on any bar before start_idx-1 (see make_strategy) so it starts flat at
    the same decision point the pandas engine does, then finalize_trades=True force-closes any
    still-open position at the final bar's close, matching the pandas engine's period-end forced
    liquidation.
    """
    sub = df.iloc[0:end_idx + 1].copy()
    sub = sub.set_index("date")
    sub = sub.rename(columns={"open": "Open", "high": "High", "low": "Low",
                               "close": "Close", "volume": "Volume"})

    block_until_idx = start_idx - 1
    bt = Backtest(sub, make_strategy(n_fast, n_slow, block_until_idx), cash=CASH, commission=cost,
                  exclusive_orders=True, trade_on_close=False, finalize_trades=True)
    stats = bt.run()
    trades = stats["_trades"]

    n_trades = len(trades)
    if n_trades:
        total_return = float(np.prod(1.0 + trades["ReturnPct"].to_numpy()) - 1.0)
    else:
        total_return = 0.0
    return n_trades, total_return


def run_pandas_engine(df: pd.DataFrame, start_idx: int, end_idx: int, n_fast: int, n_slow: int,
                       cost: float):
    state = ma_cross_target_state(df["close"], n_fast, n_slow)
    mask = pd.Series(False, index=df.index)
    mask.iloc[start_idx:end_idx + 1] = True
    trades, equity_df = simulate_ma_cross(df, state, mask, cost)
    total_return = equity_df["equity"].iloc[-1] - 1.0 if len(equity_df) else 0.0
    return len(trades), total_return


def main():
    results = []
    all_ok = True
    for asset, tf in [("btc", "1d"), ("btc", "4h"), ("spy", "1d")]:
        df = load_raw(asset, tf)
        cost = COST[asset]
        is_start = pd.Timestamp(PERIODS[asset]["IS"][0])
        oos_end = pd.Timestamp(PERIODS[asset]["OOS"][1]) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        idx_in_range = np.flatnonzero((df["date"] >= is_start).to_numpy() & (df["date"] <= oos_end).to_numpy())
        start_idx, end_idx = int(idx_in_range[0]), int(idx_in_range[-1])

        for n_fast, n_slow in MA_CROSS_PARAMS:
            n_pd, r_pd = run_pandas_engine(df, start_idx, end_idx, n_fast, n_slow, cost)
            n_bt, r_bt = run_backtesting_engine(df, start_idx, end_idx, n_fast, n_slow, cost)

            rel_err = abs(r_pd - r_bt) / max(abs(r_bt), 1e-9)
            trades_match = (n_pd == n_bt)
            return_ok = rel_err <= 0.02

            ok = trades_match and return_ok
            all_ok = all_ok and ok
            results.append({
                "asset": asset, "tf": tf, "n_fast": n_fast, "n_slow": n_slow,
                "n_trades_pandas": n_pd, "n_trades_bt": n_bt,
                "total_return_pandas": r_pd, "total_return_bt": r_bt,
                "rel_err": rel_err, "trades_match": trades_match, "return_ok": return_ok, "ok": ok,
            })
            print(f"{asset}/{tf} ({n_fast},{n_slow}): "
                  f"n_trades pandas={n_pd} bt={n_bt} match={trades_match} | "
                  f"total_return pandas={r_pd:.4f} bt={r_bt:.4f} rel_err={rel_err:.4%} ok={return_ok}")

    out = pd.DataFrame(results)
    out.to_csv("/home/claude/bt/results/crosscheck.csv", index=False)
    print()
    print("ALL CROSSCHECKS OK" if all_ok else "SOME CROSSCHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
