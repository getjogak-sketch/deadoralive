"""
engine.py — equity simulation, trade log, and metrics (§5) for both strategies.

Design (documented further in README.md):

- Both simulators take the FULL price arrays (so indicators/targets already reflect proper
  warm-up) plus a contiguous [start_idx, end_idx] window (inclusive positions into the full
  arrays) identifying one period (IS or OOS). Equity is reset to 1.0 at start_idx independently
  for every period, and the local `position` state variable also resets to flat at start_idx —
  this is exactly what produces the spec's "if target state is already long at the first bar of
  the period, enter at that bar's open" behavior, with no special-casing needed: the desired
  state for bar `start_idx` is read from the full-series target_state at position start_idx-1
  (computed using pre-period warm-up data), compared against the reset `position=0`, and if it
  says long, a normal buy transition fires at open[start_idx].
- If a position is still open at end_idx (period's last bar), it is force-liquidated at that
  bar's close (cost applied), and counted as a completed trade, per spec §5.
- Per-bar equity factors are constructed so a bar's return correctly attributes: (a) the
  close-to-close return of a position held through the whole bar, (b) the open-to-close return
  of a position entered this bar, (c) the previous-close-to-open return of a position exited this
  bar, and any combination when both an exit and a fresh re-entry occur in the same bar
  (vol_breakout's "consecutive trigger" case, spec §4B).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _period_bounds(mask: pd.Series):
    idx = np.flatnonzero(mask.to_numpy())
    if len(idx) == 0:
        raise ValueError("Empty period mask")
    start_idx, end_idx = int(idx[0]), int(idx[-1])
    if not np.array_equal(idx, np.arange(start_idx, end_idx + 1)):
        raise ValueError("Period mask is not contiguous")
    return start_idx, end_idx


# ---------------------------------------------------------------------------
# MA Crossover simulation
# ---------------------------------------------------------------------------

def simulate_ma_cross(df: pd.DataFrame, target_state: pd.Series, mask: pd.Series, cost: float):
    """
    Returns (trades: list[dict], equity: pd.DataFrame[date, equity, position]).
    """
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    state = target_state.to_numpy()

    position = 0
    entry_price = None
    entry_date = None
    trades = []
    eq_dates = []
    eq_vals = []
    eq_pos = []
    equity = 1.0

    for t in range(start_idx, end_idx + 1):
        pos_before = position
        desired = bool(state[t - 1]) if t - 1 >= 0 else False

        if desired != pos_before:
            if desired:  # flat -> long: buy at open[t]
                fill = opens[t] * (1 + cost)
                entry_price = fill
                entry_date = dates[t]
                position = 1
            else:  # long -> flat: sell at open[t]
                fill = opens[t] * (1 - cost)
                trades.append({
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": dates[t],
                    "exit_price": fill,
                    "return": fill / entry_price - 1.0,
                })
                position = 0
                entry_price = None
                entry_date = None

        # bar equity factor
        if pos_before == 1 and position == 1:
            factor = closes[t] / closes[t - 1]
        elif pos_before == 0 and position == 1:
            factor = closes[t] / entry_price
        elif pos_before == 1 and position == 0:
            factor = fill / closes[t - 1]
        else:
            factor = 1.0

        is_last_bar = (t == end_idx)
        if is_last_bar and position == 1:
            exit_fill = closes[t] * (1 - cost)
            trades.append({
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": dates[t],
                "exit_price": exit_fill,
                "return": exit_fill / entry_price - 1.0,
            })
            # rescale this bar's factor to reflect the forced liquidation at (1-cost)*close
            factor *= (1 - cost)
            position = 0
            entry_price = None
            entry_date = None

        equity *= factor
        eq_dates.append(dates[t])
        eq_vals.append(equity)
        eq_pos.append(position)

    equity_df = pd.DataFrame({"date": eq_dates, "equity": eq_vals, "position": eq_pos})
    return trades, equity_df


# ---------------------------------------------------------------------------
# Volatility Breakout simulation
# ---------------------------------------------------------------------------

def simulate_vol_breakout(df: pd.DataFrame, target: pd.Series, triggered: pd.Series,
                           mask: pd.Series, cost: float):
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    tgt = target.to_numpy(dtype=float)
    trig = triggered.to_numpy()

    position = 0
    entry_price = None
    entry_date = None
    trades = []
    eq_dates = []
    eq_vals = []
    eq_pos = []
    equity = 1.0

    for t in range(start_idx, end_idx + 1):
        pos_before = position
        held_this_bar = False  # for exposure: any exposure to the asset during bar t

        # Step 1: mandatory exit at this bar's open of any position carried in
        factor_exit_part = 1.0
        if pos_before == 1:
            exit_fill = opens[t] * (1 - cost)
            trades.append({
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": dates[t],
                "exit_price": exit_fill,
                "return": exit_fill / entry_price - 1.0,
            })
            factor_exit_part = exit_fill / closes[t - 1]
            position = 0
            entry_price = None
            entry_date = None
            held_this_bar = True

        # Step 2: this bar's own independent breakout check
        factor_entry_part = 1.0
        if bool(trig[t]):
            fill = max(opens[t], tgt[t]) * (1 + cost)
            entry_price = fill
            entry_date = dates[t]
            position = 1
            factor_entry_part = closes[t] / fill
            held_this_bar = True

        # Step 3: forced close if this is the last bar of the period and still holding
        is_last_bar = (t == end_idx)
        if is_last_bar and position == 1:
            exit_fill2 = closes[t] * (1 - cost)
            trades.append({
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": dates[t],
                "exit_price": exit_fill2,
                "return": exit_fill2 / entry_price - 1.0,
            })
            factor_entry_part = exit_fill2 / entry_price
            position = 0
            entry_price = None
            entry_date = None

        equity *= (factor_exit_part * factor_entry_part)
        eq_dates.append(dates[t])
        eq_vals.append(equity)
        eq_pos.append(1 if held_this_bar else 0)

    equity_df = pd.DataFrame({"date": eq_dates, "equity": eq_vals, "position": eq_pos})
    return trades, equity_df


# ---------------------------------------------------------------------------
# Buy & hold reference
# ---------------------------------------------------------------------------

def buy_and_hold(df: pd.DataFrame, mask: pd.Series, cost: float):
    """
    Per spec §5: buy at period's first bar open, hold to period's last bar close,
    cost applied once on each side. Returns (bh_return, bh_mdd, equity_df[date, equity]).
    MDD is computed off the mark-to-market path (entry cost applied, exit cost not — a constant
    scale factor at the very last point only, so it does not affect the drawdown path); bh_return
    applies the exit cost to the final value.
    """
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    basis = opens[start_idx] * (1 + cost)
    path = closes[start_idx:end_idx + 1] / basis
    bh_equity = pd.DataFrame({"date": dates[start_idx:end_idx + 1], "equity": path})

    final_value = closes[end_idx] * (1 - cost) / basis
    bh_return = final_value - 1.0

    running_max = np.maximum.accumulate(path)
    dd = 1.0 - path / running_max
    bh_mdd = float(dd.max()) * 100.0

    return bh_return, bh_mdd, bh_equity


# ---------------------------------------------------------------------------
# Metrics (§5)
# ---------------------------------------------------------------------------
# Moved to metrics.py per spec_v2 §5 ("v1 compute_metrics 분리 ... import 경로 유지"); re-imported
# here, unchanged, so existing `from engine import compute_metrics` callers keep working.
from metrics import compute_metrics  # noqa: E402,F401


# ---------------------------------------------------------------------------
# netcheck (spec_v2 §3) extension — additive only, nothing above this line is modified.
#
# Two new simulators for the two v1 "shapes" don't cover (spec_v2 §3): a fixed holding-period
# entry (dip_pct) and the DCA reference series (dca_weekly). Both follow the same conventions as
# the v1 simulators above: FULL price arrays in, a contiguous [start_idx, end_idx] period window,
# equity reset to 1.0 at start_idx, and (for simulate_hold_n_bars) forced liquidation at the
# period's last close if still in a position.
# ---------------------------------------------------------------------------

def simulate_hold_n_bars(df: pd.DataFrame, entry_trigger: pd.Series, n_hold: int,
                          mask: pd.Series, cost: float):
    """
    "Hold-N-bar" strategy type (spec_v2 §3, e.g. `dip_pct`): entry_trigger[t] (decided at the
    close of bar t, using only data <= t) causes a buy at bar t+1's open; the position is held
    for exactly n_hold bars from the entry bar, then sold at that bar's open — regardless of any
    intervening signal (long-only, single position, no stacking: new triggers while already in a
    position are ignored, exactly like every other strategy here being "fully invested or fully
    flat").

    Per-bar equity attribution mirrors simulate_vol_breakout's split-factor approach, generalized
    with a third case (continuing to hold across a bar with neither an entry nor a scheduled
    exit), since — unlike vol_breakout's always-1-bar holds — an exit and a fresh entry CAN both
    land on the same bar here (a scheduled N-bar exit coinciding with a new -x% trigger from the
    prior bar).

    Returns (trades, equity_df[date, equity, position]).
    """
    start_idx, end_idx = _period_bounds(mask)
    dates = df["date"].to_numpy()
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    trig = entry_trigger.to_numpy()

    position = 0
    entry_price = None
    entry_date = None
    exit_at = None  # absolute bar index at which a scheduled exit is due (that bar's open)
    trades = []
    eq_dates, eq_vals, eq_pos = [], [], []
    equity = 1.0

    for t in range(start_idx, end_idx + 1):
        pos_before = position
        exited_this_bar = False
        factor_exit_part = 1.0

        if pos_before == 1 and exit_at is not None and t == exit_at:
            exit_fill = opens[t] * (1 - cost)
            trades.append({
                "entry_date": entry_date, "entry_price": entry_price,
                "exit_date": dates[t], "exit_price": exit_fill,
                "return": exit_fill / entry_price - 1.0,
            })
            factor_exit_part = exit_fill / closes[t - 1]
            position = 0
            entry_price = None
            entry_date = None
            exit_at = None
            exited_this_bar = True

        entered_this_bar = False
        factor_entry_or_hold_part = 1.0
        if position == 0 and t - 1 >= 0 and bool(trig[t - 1]):
            fill = opens[t] * (1 + cost)
            entry_price = fill
            entry_date = dates[t]
            position = 1
            exit_at = t + n_hold
            entered_this_bar = True
            factor_entry_or_hold_part = closes[t] / fill
        elif pos_before == 1 and not exited_this_bar:
            # continued holding across this bar, no action
            factor_entry_or_hold_part = closes[t] / closes[t - 1]

        held_this_bar = (pos_before == 1) or (position == 1)

        is_last_bar = (t == end_idx)
        factor = factor_exit_part * factor_entry_or_hold_part
        if is_last_bar and position == 1:
            exit_fill2 = closes[t] * (1 - cost)
            trades.append({
                "entry_date": entry_date, "entry_price": entry_price,
                "exit_date": dates[t], "exit_price": exit_fill2,
                "return": exit_fill2 / entry_price - 1.0,
            })
            factor *= (1 - cost)
            position = 0
            entry_price = None
            entry_date = None
            exit_at = None

        equity *= factor
        eq_dates.append(dates[t])
        eq_vals.append(equity)
        eq_pos.append(1 if held_this_bar else 0)

    equity_df = pd.DataFrame({"date": eq_dates, "equity": eq_vals, "position": eq_pos})
    return trades, equity_df


def simulate_dca_weekly(df: pd.DataFrame, mask: pd.Series, cost: float, notional: float = 1.0):
    """
    Reference-only "participate, never sell" DCA series (spec_v2 §3 `dca_weekly`): buys a fixed
    notional amount at the open of the first bar of every ISO calendar week within the period,
    applying the buy-side cost; never sells. Per spec_v2 §3, "지표는 수익률·MDD만" (only return and
    MDD are reported for this reference row — no PF/trades/win-rate, which aren't well-defined
    for a strategy with zero sells).

    Because DCA keeps adding capital over time, there is no single "equity curve starting at 1.0"
    the way the other simulators have; instead we track:
        ratio[t] = (units held so far) * close[t]  /  (total notional invested so far)
    which is 1.0 exactly at each purchase's own fill price and drifts with the market between
    purchases and after the last one — a running "am I above or below my average cost basis"
    view. total_return is this ratio's final value minus 1; MDD is the max drawdown of this ratio
    path (from the first purchase onward — undefined before any capital is committed). This is a
    simplifying convention (documented in README), not a claim of a canonical "DCA equity curve"
    definition.

    Returns (total_return, mdd, detail_df[date, nav, invested, ratio]).
    """
    idx = np.flatnonzero(mask.to_numpy())
    start_idx, end_idx = int(idx[0]), int(idx[-1])
    dates = df["date"]
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    period_dates = dates.iloc[start_idx:end_idx + 1]
    iso = period_dates.dt.isocalendar()
    week_keys = list(zip(iso["year"].to_numpy(), iso["week"].to_numpy()))
    seen = set()
    buy_flags = np.zeros(len(week_keys), dtype=bool)
    for i, k in enumerate(week_keys):
        if k not in seen:
            seen.add(k)
            buy_flags[i] = True

    units = 0.0
    invested = 0.0
    eq_dates, nav_list, inv_list = [], [], []
    for offset, t in enumerate(range(start_idx, end_idx + 1)):
        if buy_flags[offset]:
            fill = opens[t] * (1 + cost)
            units += notional / fill
            invested += notional
        eq_dates.append(dates.iloc[t])
        nav_list.append(units * closes[t])
        inv_list.append(invested)

    nav = np.array(nav_list, dtype=float)
    inv = np.array(inv_list, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(inv > 0, nav / inv, np.nan)

    valid = ~np.isnan(ratio)
    total_return = float(ratio[valid][-1] - 1.0) if valid.any() else np.nan

    if valid.any():
        r = ratio[valid]
        running_max = np.maximum.accumulate(r)
        dd = 1.0 - r / running_max
        mdd = float(dd.max()) * 100.0
    else:
        mdd = 0.0

    detail_df = pd.DataFrame({"date": eq_dates, "nav": nav, "invested": inv, "ratio": ratio})
    return total_return, mdd, detail_df
