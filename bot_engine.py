"""
bot_engine.py — task B1: a LOT-BASED simulator for "bot templates" (grid bots and DCA/safety-order
bots, as commonly configured on Pionex / 3Commas). Deliberately a SEPARATE module from engine.py —
engine.py's existing functions (`simulate_ma_cross`, `simulate_vol_breakout`,
`simulate_hold_n_bars`, `simulate_dca_weekly`, `buy_and_hold`) are UNCHANGED by this file.

Every simulator here shares engine.py's own house conventions so the rest of the pipeline
(metrics.compute_metrics, verdict.assign_verdict, robustness's grid idea) can reuse them unchanged:

  - FULL price arrays in, a contiguous [start_idx, end_idx] period window (from a boolean mask),
    equity reset to 1.0 (start capital) at start_idx.
  - A lot's buy fill price = reference price * (1 + cost); a lot's sell fill price =
    reference price * (1 - cost) — cost is never a separate cash outflow, it is embedded in the
    executed price, exactly like engine.py's simulators.
  - No leverage, long-only spot, all bookkeeping in fractions of the running "capital" (which
    starts at 1.0 and is re-based to the current cash balance at every fresh activation/deal, so
    a prior deal's gain or loss compounds into the next one's sizing — the same spirit as every
    other simulator in this repo being a single multiplicative equity curve).
  - `trades`: a list of {entry_date, entry_price, exit_date, exit_price, return} dicts —
    "return" is the dollar-weighted P&L of the whole completed unit (one grid-cell round trip for
    grid_bot, one whole deal for the DCA bots) — this is exactly the shape metrics.compute_metrics
    already consumes.
  - `equity_df`: a DataFrame[date, equity, position] covering every bar in the period, mark-to-
    market each bar (`position` = 1 whenever any lot/deal is open during that bar).
  - No lookahead by construction: a bar's own fills/state transitions only ever read that bar's
    own OHLC and state carried forward from strictly earlier bars — never anything past bar t.
    (Verified by tests.py's `test_no_lookahead_bot_templates`, the same truncation-invariance
    property engine.py's simulators are checked against.)

Both bots below are described precisely by spec_v2's own general framing (per-bar decisions using
only that bar's OHLC and prior state) but their execution shape — many small, independently-
tracked open lots funded with a slice of capital each, rather than one all-in position — needed a
purpose-built engine, which is why this file exists rather than shoehorning them into
`simulate_ma_cross`'s single-position state machine.
"""
from __future__ import annotations
import itertools
import math

import numpy as np
import pandas as pd

from engine import _period_bounds
from metrics import compute_metrics


# =============================================================================
# grid_bot — "Pionex-style spot grid" (task B1.1)
# =============================================================================

def _grid_levels(price: float, range_pct: float, n_grids: int) -> np.ndarray:
    r = range_pct / 100.0
    lower, upper = price * (1 - r), price * (1 + r)
    return np.linspace(lower, upper, n_grids)


def simulate_grid_bot(df: pd.DataFrame, mask: pd.Series, cost: float, range_pct: float,
                       n_grids: int = 20):
    """
    Grid bot, spot, long-only. At activation (the period's first bar, and again the bar after
    every reset) the grid is re-centered on the activation price P: `n_grids` equally spaced
    levels spanning [P*(1-range_pct/100), P*(1+range_pct/100)]. Every level strictly below P is
    "armed" with a standing buy order funded with (capital at that activation) / n_grids; levels
    at/above P start with no order (held as idle cash for this activation — spec B1.1: "hold the
    rest as cash").

    Per bar, levels are walked in ascending price order, at most one fill per level per bar
    (conservative, per spec): an "armed" level buys when the bar's low <= its price; a "holding"
    level sells (and re-arms at its own level) when the bar's high >= the level one step above it.
    If the bar's CLOSE ends up outside [lower, upper], every open lot is liquidated at that close
    and the grid re-activates (re-centered on that close) starting the *next* bar.

    Judgment call (documented): "capital" for the capital/n_grids sizing is the account's current
    cash at the moment of (re-)activation — since no lots are open at that instant, this equals
    total equity there, so a prior activation's gain or loss compounds into the next one's grid
    sizing, the same "one multiplicative equity curve" convention every other simulator here uses,
    rather than a permanently fixed 1.0/n_grids notional that would over- or under-size a rebuilt
    grid after the account has drifted away from its starting capital.

    Returns (trades, equity_df[date, equity, position]).
    """
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    cash = 1.0
    trades = []
    eq_dates, eq_vals, eq_pos = [], [], []

    levels = None
    state = None          # per-level: "inactive" | "armed" | "holding"
    units = None
    entry_price = None
    entry_date = None
    lower = upper = None
    pending_reactivate_price = None  # set at bar t (reset), consumed at bar t+1

    def _activate(price: float, capital: float):
        nonlocal levels, state, units, entry_price, entry_date, lower, upper
        levels = _grid_levels(price, range_pct, n_grids)
        lower, upper = levels[0], levels[-1]
        state = ["inactive"] * n_grids
        units = [0.0] * n_grids
        entry_price = [None] * n_grids
        entry_date = [None] * n_grids
        per_level_cash = capital / n_grids
        for i in range(n_grids):
            if levels[i] < price:
                state[i] = "armed"
        return per_level_cash

    per_level_cash = _activate(opens[start_idx], cash)

    for t in range(start_idx, end_idx + 1):
        if pending_reactivate_price is not None:
            per_level_cash = _activate(pending_reactivate_price, cash)
            pending_reactivate_price = None

        # Ascending price order, at most one fill per level per bar.
        for i in range(n_grids):
            if state[i] == "holding":
                if i + 1 < n_grids and highs[t] >= levels[i + 1]:
                    fill = levels[i + 1] * (1 - cost)
                    trades.append({
                        "entry_date": entry_date[i], "entry_price": entry_price[i],
                        "exit_date": dates[t], "exit_price": fill,
                        "return": fill / entry_price[i] - 1.0,
                    })
                    cash += units[i] * fill
                    units[i] = 0.0
                    entry_price[i] = None
                    entry_date[i] = None
                    state[i] = "armed"
            elif state[i] == "armed":
                if lows[t] <= levels[i]:
                    fill = levels[i] * (1 + cost)
                    units[i] = per_level_cash / fill
                    cash -= per_level_cash
                    entry_price[i] = fill
                    entry_date[i] = dates[t]
                    state[i] = "holding"

        is_last_bar = (t == end_idx)
        close_outside = closes[t] < lower or closes[t] > upper
        if close_outside or is_last_bar:
            # Liquidate every open lot at this bar's close (reset, or forced period-end close-out).
            for i in range(n_grids):
                if state[i] == "holding":
                    fill = closes[t] * (1 - cost)
                    trades.append({
                        "entry_date": entry_date[i], "entry_price": entry_price[i],
                        "exit_date": dates[t], "exit_price": fill,
                        "return": fill / entry_price[i] - 1.0,
                    })
                    cash += units[i] * fill
                    units[i] = 0.0
                    entry_price[i] = None
                    entry_date[i] = None
                    state[i] = "inactive"
            if close_outside and not is_last_bar:
                pending_reactivate_price = closes[t]

        held_this_bar = any(s == "holding" for s in state)
        lots_value = sum(units[i] * closes[t] for i in range(n_grids) if state[i] == "holding")
        equity = cash + lots_value
        eq_dates.append(dates[t])
        eq_vals.append(equity)
        eq_pos.append(1 if held_this_bar else 0)

    equity_df = pd.DataFrame({"date": eq_dates, "equity": eq_vals, "position": eq_pos})
    return trades, equity_df


# =============================================================================
# dca_bot / dca_bot_sl — "3Commas-style DCA (safety orders)" (task B1.2 / B1.3)
# =============================================================================

_BASE_FRAC = 0.10
_SO_FRACS = [0.10 * (1.5 ** i) for i in range(1, 6)]   # SO1..SO5: 15%, 22.5%, 33.75%, 50.625%, 75.9375%
_TP_FRAC = 0.015


def simulate_dca_bot(df: pd.DataFrame, mask: pd.Series, cost: float, so_step_pct: float,
                      stop_loss_pct: float | None = None):
    """
    3Commas-style DCA bot with safety orders, spot, long-only. A "deal" starts the moment none is
    open (the period's first bar, and immediately the bar after any deal closes): a base order
    (10% of capital) buys at that bar's OPEN. While a deal is open, up to 5 safety orders (each
    1.5x the previous order's size — 15%, 22.5%, 33.75%, 50.625%, 75.9375% of capital — at a fixed
    `so_step_pct` price step below the base order's own fill price, compounding: level i =
    base_price * (1 - so_step_pct/100)^i) fill whenever the bar's LOW reaches their level, in
    order (a level can only be reached once the shallower ones already have been, since the levels
    are monotonically descending — so several can fill in one deep-crash bar). A safety order that
    the remaining cash cannot fund is permanently skipped for the rest of this deal (cash only
    shrinks within a deal, so once one order is unaffordable, every deeper one is too).

    Take-profit sells the WHOLE deal (all filled lots) when the bar's HIGH reaches 1.5% above the
    deal's running (cost-inclusive) average entry price. If `stop_loss_pct` is given (dca_bot_sl),
    a stop-loss also sells the whole deal when the bar's LOW reaches `stop_loss_pct` below that
    same average — checked before take-profit within a bar (the conservative, worst-case order
    when both could apply on the same bar).

    Judgment call (documented): "capital" for the 10%/15%/... sizing is the account's current cash
    at the moment each new deal starts (no lots are open then, so this equals total equity) — the
    same "one multiplicative equity curve, capital re-based at each fresh start" convention
    simulate_grid_bot above uses, so a prior deal's P&L compounds into the next deal's sizing.
    Safety-order/TP/SL level prices are computed from the base order's raw (pre-cost) fill
    reference price; cost is applied only to each individual order's own executed fill.

    Returns (trades, equity_df[date, equity, position]).
    """
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    step = so_step_pct / 100.0

    cash = 1.0
    trades = []
    eq_dates, eq_vals, eq_pos = [], [], []

    deal_open = False
    base_ref = None            # raw (pre-cost) base-order reference price, for SO level spacing
    total_units = total_cost = 0.0
    avg_price = None
    entry_date = None
    next_so = 1                # next safety-order index (1..5) still eligible
    so_skipped = False
    capital_ref = cash         # re-based to current cash at the instant each new deal starts
    deal_min_low = None        # running min of the bar low over the open deal's life, for the
                                # "max open drawdown of a single deal" footnote metric below

    for t in range(start_idx, end_idx + 1):
        if not deal_open:
            capital_ref = cash  # no lots open right now, so cash == total equity here
            base_ref = opens[t]
            fill = base_ref * (1 + cost)
            size = capital_ref * _BASE_FRAC
            total_units = size / fill
            total_cost = size
            cash -= size
            avg_price = fill
            entry_date = dates[t]
            deal_open = True
            next_so = 1
            so_skipped = False
            deal_min_low = None

        # Coordinator follow-up: track the deal's worst mark-to-market drawdown so far — how far
        # the bar's low has dipped below the deal's running average entry, as a fraction of that
        # average. Cheap: one running min, updated every bar the deal is open, no extra pass.
        deal_min_low = lows[t] if deal_min_low is None else min(deal_min_low, lows[t])

        # Safety orders: sequential, ascending in price step (deepest = largest index).
        while next_so <= 5 and not so_skipped:
            level = base_ref * ((1 - step) ** next_so)
            size = capital_ref * _SO_FRACS[next_so - 1]
            if cash < size - 1e-15:
                so_skipped = True
                break
            if lows[t] <= level:
                fill = level * (1 + cost)
                units = size / fill
                cash -= size
                total_units += units
                total_cost += size
                avg_price = total_cost / total_units
                next_so += 1
            else:
                break

        is_last_bar = (t == end_idx)
        closed_this_bar = False
        if stop_loss_pct is not None:
            sl_level = avg_price * (1 - stop_loss_pct)
            if lows[t] <= sl_level:
                fill = sl_level * (1 - cost)
                proceeds = total_units * fill
                trades.append({
                    "entry_date": entry_date, "entry_price": avg_price,
                    "exit_date": dates[t], "exit_price": fill,
                    "return": proceeds / total_cost - 1.0,
                    "max_open_dd": max(0.0, (avg_price - deal_min_low) / avg_price),
                })
                cash += proceeds
                deal_open = False
                closed_this_bar = True

        if not closed_this_bar:
            tp_level = avg_price * (1 + _TP_FRAC)
            if highs[t] >= tp_level:
                fill = tp_level * (1 - cost)
                proceeds = total_units * fill
                trades.append({
                    "entry_date": entry_date, "entry_price": avg_price,
                    "exit_date": dates[t], "exit_price": fill,
                    "return": proceeds / total_cost - 1.0,
                    "max_open_dd": max(0.0, (avg_price - deal_min_low) / avg_price),
                })
                cash += proceeds
                deal_open = False
                closed_this_bar = True

        if is_last_bar and deal_open:
            fill = closes[t] * (1 - cost)
            proceeds = total_units * fill
            trades.append({
                "entry_date": entry_date, "entry_price": avg_price,
                "exit_date": dates[t], "exit_price": fill,
                "return": proceeds / total_cost - 1.0,
                "max_open_dd": max(0.0, (avg_price - deal_min_low) / avg_price),
            })
            cash += proceeds
            deal_open = False

        equity = cash + (total_units * closes[t] if deal_open else 0.0)
        eq_dates.append(dates[t])
        eq_vals.append(equity)
        eq_pos.append(1 if deal_open else 0)

    equity_df = pd.DataFrame({"date": eq_dates, "equity": eq_vals, "position": eq_pos})
    return trades, equity_df


# =============================================================================
# Registry-facing dispatch (used by run_weekly.py's bot-template row builder) — mirrors
# registry.py's own variant["sim_fn"] closures; kept here (rather than in registry.py) purely so
# every bot_engine-specific parameter/default lives in one file.
# =============================================================================

_SIM = {
    "grid_bot": lambda df, mask, cost, p: simulate_grid_bot(df, mask, cost, p["range_pct"], 20),
    "dca_bot": lambda df, mask, cost, p: simulate_dca_bot(df, mask, cost, p["so_step_pct"]),
    "dca_bot_sl": lambda df, mask, cost, p: simulate_dca_bot(
        df, mask, cost, p["so_step_pct"], stop_loss_pct=p["stop_loss_pct"]),
}


def run(sid: str, params: dict, df: pd.DataFrame, mask: pd.Series, cost: float):
    return _SIM[sid](df, mask, cost, params)


def max_deal_dd(trades: list) -> float | None:
    """Coordinator follow-up (2026-09-07): 'max open drawdown of a single deal' for the DCA bots —
    the worst (avg_entry - low)/avg_entry seen across any one deal's open lifetime, maxed over every
    deal in `trades`. grid_bot's trades have no `max_open_dd` field (a grid lot's "drawdown" isn't
    a comparable single-deal concept — see the footnote text instead), so this returns None for it
    and for an empty trades list; callers render that as '-'."""
    vals = [t["max_open_dd"] for t in trades if "max_open_dd" in t]
    return max(vals) if vals else None


# =============================================================================
# Robustness diagnostic (spec_v3 §C's idea, applied to bot templates per task B1: "Robustness
# grid: apply to range_pct and so_step only (±25%)") — same output shape as robustness.py's
# compute_robustness, kept as a sibling function here (rather than added to robustness.py's own
# _RAW_SIGNAL dispatch, which is built around a "signal function + generic engine dispatch" shape
# these bots don't have) so robustness.py itself needs no changes.
# =============================================================================

def _neighbours_pct(value: float) -> list:
    lo = round(value * 0.75, 6)
    hi = round(value * 1.25, 6)
    return sorted({lo, round(float(value), 6), hi})


def evaluate_bot_grid(sid: str, base_params: dict, df: pd.DataFrame, oos_mask: pd.Series,
                       cost: float) -> list:
    """OOS-only grid over the one varying parameter (range_pct for grid_bot, so_step_pct for both
    DCA variants), +/-25%, base value included. Every other fixed parameter (n_grids, base/safety-
    order sizing, take-profit %, stop-loss %) is held at its registered value — never varied."""
    if sid == "grid_bot":
        param_name = "range_pct"
    elif sid in ("dca_bot", "dca_bot_sl"):
        param_name = "so_step_pct"
    else:
        return []

    results = []
    for val in _neighbours_pct(base_params[param_name]):
        params = dict(base_params)
        params[param_name] = val
        trades, eq = run(sid, params, df, oos_mask, cost)
        m = compute_metrics(trades, eq, 0.0, 0.0, 1.0, int(oos_mask.sum()))
        pf = m["profit_factor"]
        if isinstance(pf, float) and math.isnan(pf):
            pf_out = None
        elif isinstance(pf, float) and math.isinf(pf):
            pf_out = "inf"
        else:
            pf_out = pf
        results.append({"params": f"{param_name}={val}", "oos_pf": pf_out,
                         "oos_n_trades": m["n_trades"]})
    return results


def compute_bot_robustness(sid: str, base_params: dict, df: pd.DataFrame, oos_mask: pd.Series,
                            cost: float) -> dict:
    grid = evaluate_bot_grid(sid, base_params, df, oos_mask, cost)
    if not grid:
        return {
            "grid": [], "share_pf_ge_1": None, "n_pass": None, "n_total": 0,
            "note": "diagnostic; verdict uses registered params only",
        }

    def _pf_ge_1(pf):
        if pf == "inf":
            return True
        return isinstance(pf, (int, float)) and pf >= 1.0

    passing = sum(1 for g in grid if _pf_ge_1(g["oos_pf"]) and g["oos_n_trades"] >= 10)
    return {
        "grid": grid,
        "share_pf_ge_1": passing / len(grid),
        "n_pass": passing,
        "n_total": len(grid),
        "note": "diagnostic; verdict uses registered params only",
    }
