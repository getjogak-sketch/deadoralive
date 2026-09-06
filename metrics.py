"""
metrics.py — §5 metrics computation, moved out of engine.py per spec_v2 §5
("metrics.py — v1 compute_metrics 분리 (기존 engine.py에서 이동해도 되지만 import 경로 유지)").

The function body is byte-for-byte the v1 implementation (engine.compute_metrics) — nothing
about the metric definitions changed, only its location. engine.py re-imports it so
`from engine import compute_metrics` (the v1/import-path-preserving form) keeps working.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_metrics(trades: list, equity_df: pd.DataFrame, bh_return: float, bh_mdd: float,
                     bars_per_year: float, n_bars_period: int):
    equity = equity_df["equity"].to_numpy(dtype=float)
    position = equity_df["position"].to_numpy()

    total_return = equity[-1] / 1.0 - 1.0 if len(equity) else np.nan

    n_bars = len(equity)
    years = n_bars / bars_per_year if bars_per_year else np.nan
    if len(equity) and equity[-1] > 0 and years > 0:
        cagr = equity[-1] ** (1.0 / years) - 1.0
    else:
        cagr = np.nan

    running_max = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    dd = 1.0 - equity / running_max
    mdd = float(dd.max()) * 100.0 if len(dd) else 0.0

    # per-bar returns for Sharpe: prepend the reference 1.0 baseline
    prev = np.concatenate([[1.0], equity[:-1]])
    bar_returns = equity / prev - 1.0
    if len(bar_returns) > 1 and np.std(bar_returns, ddof=1) > 0:
        sharpe = (np.mean(bar_returns) / np.std(bar_returns, ddof=1)) * np.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    n_trades = len(trades)
    rets = np.array([tr["return"] for tr in trades], dtype=float) if n_trades else np.array([])
    win_rate = float(np.mean(rets > 0)) * 100.0 if n_trades else 0.0
    pos_sum = rets[rets > 0].sum() if n_trades else 0.0
    neg_sum = rets[rets < 0].sum() if n_trades else 0.0
    if neg_sum < 0:
        profit_factor = pos_sum / abs(neg_sum)
    else:
        profit_factor = np.inf if pos_sum > 0 else np.nan

    # max consecutive losing trades (return < 0)
    max_consec_loss = 0
    cur = 0
    for r in rets:
        if r < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    avg_trade_return = float(np.mean(rets)) if n_trades else 0.0
    exposure = float(np.mean(position == 1)) * 100.0 if n_bars else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "bh_return": bh_return,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "mdd": mdd,
        "bh_mdd": bh_mdd,
        "sharpe": sharpe,
        "exposure": exposure,
        "max_consec_loss": max_consec_loss,
        "avg_trade_return": avg_trade_return,
    }
