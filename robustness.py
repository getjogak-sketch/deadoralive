"""
robustness.py — spec_v3 §C: parameter-neighbourhood robustness map.

DIAGNOSTIC ONLY. Every value this module produces is informational; nothing here is read by
verdict.py, and nothing here ever changes a value in registry.py — the registered parameters
that actually decide a strategy's verdict are exactly the ones in registry.py, untouched. This
module only asks, after the fact: "if we'd registered a slightly different number, would OOS
profit factor still look reasonable?" — a strategy that only works at its exact registered
numbers is more likely a historical coincidence than a real edge.

Reuses the existing, already-tested engine primitives (engine.simulate_ma_cross,
simulate_vol_breakout, simulate_hold_n_bars) and metrics.compute_metrics UNCHANGED — this module
adds no new simulation logic, only a small dispatch table mapping each registry strategy id back
to the underlying strategies.py/indicators.py function that computes its signal from arbitrary
numeric parameters (registry.py's own variant closures bake in one fixed set of numbers, which is
exactly right for the real backtest but is the one thing a grid needs to vary).

Grid rule (spec_v3 §C), per numeric parameter p:
  - literally named "k" (Larry Williams' breakout multiplier): {p-0.1, p, p+0.1}, floored at 0.1.
  - a non-integer float NOT named "k" (e.g. dip_pct's -5% threshold): spec_v3 §C only names the
    two cases above; extended here (a judgment call, documented in README) as a proportional
    +/-25% shift like the integer case, but WITHOUT rounding to the nearest integer (rounding a
    fractional threshold to an int would collapse it to 0) and with the sign preserved.
  - everything else (an integer-valued window/lookback length): {round(p*0.75), p, round(p*1.25)},
    floored at 2.
  - "MA pairs" (a variant whose numeric params are exactly {n_fast, n_slow}): combinations with
    fast >= slow are dropped, per spec_v3 §C ("for MA pairs keep fast < slow").
Two numeric params -> up to 3x3=9 grid points; one -> up to 3.
"""
from __future__ import annotations
import itertools
import math

import numpy as np
import pandas as pd

import strategies as strat
from engine import simulate_ma_cross, simulate_vol_breakout, simulate_hold_n_bars
from metrics import compute_metrics

# ---------------------------------------------------------------------------
# Per-strategy-id: how to recompute its signal from an arbitrary {param_name: value} dict, using
# ONLY functions strategies.py/indicators.py already export (no new signal logic here). Keys must
# match registry.py's own variant["params"] dict keys exactly, since run_weekly.py passes that
# dict straight through (perturbed) to whichever of these is looked up by strategy id.
# ---------------------------------------------------------------------------
_RAW_SIGNAL = {
    "sma_cross": lambda df, p: strat.ma_cross_target_state(df["close"], p["n_fast"], p["n_slow"]),
    "ema_cross": lambda df, p: strat.ema_cross_target_state(df["close"], p["n_fast"], p["n_slow"]),
    "above_sma": lambda df, p: strat.above_sma_target_state(df["close"], p["n"]),
    "donchian": lambda df, p: strat.donchian_target_state(df, p["n_high"], p["n_low"]),
    "vol_breakout": lambda df, p: strat.vol_breakout_targets(df, p["k"]),
    "vol_breakout_trend": lambda df, p: strat.vol_breakout_trend_targets(df, p["k"], p["sma_n"]),
    "rsi_mr": lambda df, p: strat.rsi_mr_target_state(df["close"], p["exit"]),
    "tsmom": lambda df, p: strat.tsmom_target_state(df["close"], p["n"]),
    "dip_pct": lambda df, p: strat.dip_pct_entry_trigger(df["close"], p["threshold"]),
    # spec_v3 §D "Popular combos" — additive. `ichimoku_cloud` has no entry here since it has no
    # numeric params (see registry.py's own comment on that judgment call); evaluate_variant_grid
    # already returns [] for any strategy with zero numeric params before this table is consulted.
    "ema_9_21": lambda df, p: strat.ema_cross_target_state(df["close"], p["n_fast"], p["n_slow"]),
    "ema200_macd": lambda df, p: strat.ema200_macd_target_state(df, p["n_ema"]),
    "rsi_uptrend": lambda df, p: strat.rsi_uptrend_target_state(df, p["n_sma"]),
    "bb_squeeze": lambda df, p: strat.bb_squeeze_target_state(df, p["n_lookback"], p["n_confirm"]),
    "heikin_ashi_trend": lambda df, p: strat.heikin_ashi_trend_target_state(df, p["n_confirm"]),
    "supertrend_ema200": lambda df, p: strat.supertrend_ema200_target_state(df, p["n_ema"]),
}

# Strategy ids with no numeric parameter at all (registry.py's variant["params"] == {}) never
# reach _RAW_SIGNAL — evaluate_variant_grid returns an empty grid for them (spec_v3 §C: "For every
# ... registry variant WITH >=1 numeric parameter" — the others simply have nothing to vary).


def _is_wholenumber(x) -> bool:
    return isinstance(x, (int, np.integer)) or (isinstance(x, float) and float(x).is_integer())


def neighbour_values(name: str, value) -> list:
    """One numeric parameter's grid candidates, per the module docstring's rule. Returns a
    sorted, deduplicated list (collapsing to fewer than 3 points at small values, e.g. n=2, is
    expected and handled by the caller — the floor exists so a candidate never becomes
    nonsensical, e.g. window length 0)."""
    if name == "k":
        lo = max(round(value - 0.1, 4), 0.1)
        hi = round(value + 0.1, 4)
        return sorted({lo, round(float(value), 4), hi})
    if isinstance(value, float) and not _is_wholenumber(value):
        lo, hi = value * 0.75, value * 1.25
        sign = 1.0 if value >= 0 else -1.0
        lo = sign * max(abs(lo), 0.001)
        hi = sign * max(abs(hi), 0.001)
        return sorted({round(lo, 6), round(float(value), 6), round(hi, 6)})
    v = int(round(value))
    lo = max(int(round(v * 0.75)), 2)
    hi = int(round(v * 1.25))
    return sorted({lo, v, hi})


def evaluate_variant_grid(sid: str, base_params: dict, stype: str, hold_n,
                           df: pd.DataFrame, oos_mask: pd.Series, cost: float) -> list:
    """Runs the OOS-only backtest (spec_v3 §C: "evaluate OOS net Profit Factor") at every grid
    point near base_params, using the SAME engine simulators the real backtest uses. Returns a
    list of {"params": "name=value, ...", "oos_pf": float|"inf"|None, "oos_n_trades": int}, one
    entry per (deduplicated, MA-pair-filtered) grid point, base params included as one of them.
    Empty list if this strategy has no numeric parameter (nothing to vary) or isn't in the
    _RAW_SIGNAL dispatch table yet (never silently wrong — an id missing from that table is a
    programming error in THIS module, not a reason to fabricate a result, so it also returns []
    rather than raising, since this is a diagnostic column that must never break a weekly run)."""
    numeric_names = [k for k, v in base_params.items()
                      if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)]
    if not numeric_names or sid not in _RAW_SIGNAL:
        return []

    candidate_lists = [neighbour_values(name, base_params[name]) for name in numeric_names]
    is_ma_pair = set(numeric_names) == {"n_fast", "n_slow"}

    results = []
    seen = set()
    for combo in itertools.product(*candidate_lists):
        params = dict(base_params)
        for name, val in zip(numeric_names, combo):
            params[name] = val
        if is_ma_pair and params["n_fast"] >= params["n_slow"]:
            continue
        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)

        signal_out = _RAW_SIGNAL[sid](df, params)
        this_hold_n = params.get("n_hold", hold_n)

        if stype == "state":
            trades, eq = simulate_ma_cross(df, signal_out, oos_mask, cost)
        elif stype == "onebar":
            target, triggered = signal_out
            trades, eq = simulate_vol_breakout(df, target, triggered, oos_mask, cost)
        elif stype == "holdN":
            trades, eq = simulate_hold_n_bars(df, signal_out, this_hold_n, oos_mask, cost)
        else:
            continue

        m = compute_metrics(trades, eq, 0.0, 0.0, 1.0, int(oos_mask.sum()))
        pf = m["profit_factor"]
        if isinstance(pf, float) and math.isnan(pf):
            pf_out = None
        elif isinstance(pf, float) and math.isinf(pf):
            pf_out = "inf"
        else:
            pf_out = pf

        label = ", ".join(f"{name}={val}" for name, val in zip(numeric_names, combo))
        results.append({"params": label, "oos_pf": pf_out, "oos_n_trades": m["n_trades"]})

    return results


def compute_robustness(sid: str, base_params: dict, stype: str, hold_n,
                        df: pd.DataFrame, oos_mask: pd.Series, cost: float) -> dict:
    """Builds the exact `robustness` object stored on a row (spec_v3 §C): the grid, the
    ready-to-render `share_pf_ge_1` fraction, and a note reiterating that this never touches the
    verdict. `share_pf_ge_1` counts grid points with OOS PF >= 1.0 AND n_trades >= 10 (spec_v3 §C's
    own wording) — a point with too few OOS trades to trust is not counted as evidence either
    way."""
    grid = evaluate_variant_grid(sid, base_params, stype, hold_n, df, oos_mask, cost)
    if not grid:
        return {
            "grid": [],
            "share_pf_ge_1": None,
            "n_pass": None,
            "n_total": 0,
            "note": "diagnostic; verdict uses registered params only; this variant has no "
                    "tunable numeric parameter to vary",
        }

    def _pf_ge_1(pf):
        if pf == "inf":
            return True
        return isinstance(pf, (int, float)) and pf >= 1.0

    passing = sum(
        1 for g in grid if _pf_ge_1(g["oos_pf"]) and g["oos_n_trades"] >= 10
    )
    return {
        "grid": grid,
        "share_pf_ge_1": passing / len(grid),
        "n_pass": passing,
        "n_total": len(grid),
        "note": "diagnostic; verdict uses registered params only",
    }
