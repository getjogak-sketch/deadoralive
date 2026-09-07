"""
registry.py — spec_v2 §3 strategy table, verbatim (22 tradeable variants + 2 reference rows).

This is the ONLY place strategy ids/params/rules are assembled. Nowhere else in the codebase may
add, remove, or retune a strategy or a parameter (spec_v2 §7). Parameter *values* themselves come
from strategies.py's own fixed constants where v1 already defined them (sma_cross, vol_breakout)
so there is a single source of truth, not a second hard-coded copy.

Every entry's `type` says which engine primitive runs it:
  "state"      -> engine.simulate_ma_cross(df, signal_fn(df), mask, cost)          (v1 shape)
  "onebar"     -> engine.simulate_vol_breakout(df, *signal_fn(df), mask, cost)     (v1 shape)
  "holdN"      -> engine.simulate_hold_n_bars(df, signal_fn(df), hold_n, mask, cost)  (new)
  "reference"  -> handled specially by run_weekly.py (buy_and_hold / dca_weekly), no verdict.
"""
from __future__ import annotations
import strategies as strat
import bot_engine as bots

# ---------------------------------------------------------------------------
# state-type strategies
# ---------------------------------------------------------------------------

_SMA_CROSS_VARIANTS = [
    {"params": {"n_fast": nf, "n_slow": ns}, "params_str": f"{nf}-{ns}",
     "signal_fn": (lambda df, nf=nf, ns=ns: strat.ma_cross_target_state(df["close"], nf, ns))}
    for nf, ns in strat.MA_CROSS_PARAMS  # (10,50) (20,100) (50,200) — v1's own fixed grid
]

_EMA_CROSS_VARIANTS = [
    {"params": {"n_fast": 12, "n_slow": 26}, "params_str": "12-26",
     "signal_fn": (lambda df: strat.ema_cross_target_state(df["close"], 12, 26))},
]

_ABOVE_SMA_VARIANTS = [
    {"params": {"n": n}, "params_str": f"n{n}",
     "signal_fn": (lambda df, n=n: strat.above_sma_target_state(df["close"], n))}
    for n in (200, 50)
]

_DONCHIAN_VARIANTS = [
    {"params": {"n_high": nh, "n_low": nl}, "params_str": f"{nh}-{nl}",
     "signal_fn": (lambda df, nh=nh, nl=nl: strat.donchian_target_state(df, nh, nl))}
    for nh, nl in [(20, 10), (55, 20)]
]

_RSI_MR_VARIANTS = [
    {"params": {"exit": ex}, "params_str": f"exit{ex}",
     "signal_fn": (lambda df, ex=ex: strat.rsi_mr_target_state(df["close"], ex))}
    for ex in (50, 70)
]

_RSI2_CONNORS_VARIANTS = [
    {"params": {}, "params_str": "fixed",
     "signal_fn": (lambda df: strat.rsi2_connors_target_state(df))},
]

_BB_MR_VARIANTS = [
    {"params": {}, "params_str": "20-2",
     "signal_fn": (lambda df: strat.bb_mr_target_state(df["close"]))},
]

_BB_BREAKOUT_VARIANTS = [
    {"params": {}, "params_str": "20-2",
     "signal_fn": (lambda df: strat.bb_breakout_target_state(df["close"]))},
]

_MACD_VARIANTS = [
    {"params": {}, "params_str": "12-26-9",
     "signal_fn": (lambda df: strat.macd_target_state(df["close"]))},
]

_SUPERTREND_VARIANTS = [
    {"params": {}, "params_str": "10-3",
     "signal_fn": (lambda df: strat.supertrend_target_state(df, 10, 3.0))},
]

_TSMOM_VARIANTS = [
    {"params": {"n": n}, "params_str": f"n{n}",
     "signal_fn": (lambda df, n=n: strat.tsmom_target_state(df["close"], n))}
    for n in (30, 90)
]

_DIP_3DOWN_VARIANTS = [
    {"params": {}, "params_str": "fixed",
     "signal_fn": (lambda df: strat.dip_3down_target_state(df["close"]))},
]

# ---------------------------------------------------------------------------
# one-bar-type strategies
# ---------------------------------------------------------------------------

_VOL_BREAKOUT_VARIANTS = [
    {"params": {"k": k}, "params_str": f"k{k}",
     "signal_fn": (lambda df, k=k: strat.vol_breakout_targets(df, k))}
    for k in strat.VOL_BREAKOUT_PARAMS  # 0.5, 0.7 — v1's own fixed grid
]

_VOL_BREAKOUT_TREND_VARIANTS = [
    {"params": {"k": 0.5, "sma_n": 20}, "params_str": "k0.5-sma20",
     "signal_fn": (lambda df: strat.vol_breakout_trend_targets(df, 0.5, 20))},
]

# ---------------------------------------------------------------------------
# hold-N-bar-type strategy
# ---------------------------------------------------------------------------

_DIP_PCT_VARIANTS = [
    {"params": {"threshold": -0.05, "n_hold": 5}, "params_str": "-5pct-5bar", "hold_n": 5,
     "signal_fn": (lambda df: strat.dip_pct_entry_trigger(df["close"], -0.05))},
]

# ---------------------------------------------------------------------------
# The registry table (spec_v2 §3), in table order
# ---------------------------------------------------------------------------

REGISTRY = [
    {"id": "sma_cross", "name": "SMA crossover", "type": "state",
     "rule": "fast SMA > slow SMA -> long", "variants": _SMA_CROSS_VARIANTS},
    {"id": "ema_cross", "name": "EMA crossover", "type": "state",
     "rule": "EMA fast > EMA slow -> long", "variants": _EMA_CROSS_VARIANTS},
    {"id": "above_sma", "name": "Price above SMA", "type": "state",
     "rule": "close > SMA(n) -> long", "variants": _ABOVE_SMA_VARIANTS},
    {"id": "donchian", "name": "Donchian/Turtle breakout", "type": "state",
     "rule": "close > prior N-bar high -> long; close < prior M-bar low -> flat",
     "variants": _DONCHIAN_VARIANTS},
    {"id": "vol_breakout", "name": "Larry Williams volatility breakout", "type": "onebar",
     "rule": "v1 spec.md §4B: open + k*(prior day range) intrabar breakout, 1-bar hold",
     "variants": _VOL_BREAKOUT_VARIANTS},
    {"id": "vol_breakout_trend", "name": "Volatility breakout + trend filter", "type": "onebar",
     "rule": "v1 §4B, gated on open > SMA(20) of the prior bar",
     "variants": _VOL_BREAKOUT_TREND_VARIANTS},
    {"id": "rsi_mr", "name": "RSI mean reversion", "type": "state",
     "rule": "RSI(14) < 30 -> long; RSI(14) > exit -> flat", "variants": _RSI_MR_VARIANTS},
    {"id": "rsi2_connors", "name": "Connors RSI(2)", "type": "state",
     "rule": "RSI(2) < 10 AND close > SMA(200) -> long; close > SMA(5) -> flat",
     "variants": _RSI2_CONNORS_VARIANTS},
    {"id": "bb_mr", "name": "Bollinger mean reversion", "type": "state",
     "rule": "close < lower band(20,2) -> long; close > middle band -> flat",
     "variants": _BB_MR_VARIANTS},
    {"id": "bb_breakout", "name": "Bollinger breakout", "type": "state",
     "rule": "close > upper band(20,2) -> long; close < middle band -> flat",
     "variants": _BB_BREAKOUT_VARIANTS},
    {"id": "macd", "name": "MACD signal cross", "type": "state",
     "rule": "MACD(12,26,9) line > signal -> long", "variants": _MACD_VARIANTS},
    {"id": "supertrend", "name": "Supertrend", "type": "state",
     "rule": "Supertrend(10,3) direction up -> long", "variants": _SUPERTREND_VARIANTS},
    {"id": "tsmom", "name": "Time-series momentum", "type": "state",
     "rule": "close[t] > close[t-n] -> long", "variants": _TSMOM_VARIANTS},
    {"id": "dip_3down", "name": "Buy the dip (3 down closes)", "type": "state",
     "rule": "3 consecutive down closes -> long; first up close -> flat",
     "variants": _DIP_3DOWN_VARIANTS},
    {"id": "dip_pct", "name": "Buy the dip (-x% bar)", "type": "holdN",
     "rule": "bar return <= -5% -> enter next open, exit 5 bars later at the open",
     "variants": _DIP_PCT_VARIANTS},
]

# Reference rows (spec_v2 §3: "참고" — no verdict badge; IS/OOS shown for context only).
REFERENCE = [
    {"id": "dca_weekly", "name": "Weekly DCA (reference)", "type": "reference",
     "rule": "fixed notional bought at the open of the first bar of every week; never sells"},
    {"id": "buy_and_hold", "name": "Buy & hold (reference)", "type": "reference",
     "rule": "v1 spec.md §5: buy at period's first open, hold to period's last close"},
]


# ===========================================================================
# spec_v3 §D "Popular combos" — additive only, nothing above this line (REGISTRY, REFERENCE,
# iter_variants, count_variants) is modified. Same execution rules as REGISTRY above (state at
# close t -> fill at open t+1, long-only, all-in) — every variant here is dispatched through the
# same "state" engine primitive (engine.simulate_ma_cross) as REGISTRY's own state-type
# strategies, so no new engine code was needed for this whole group. Rendered on every page as
# its own separate table (spec_v3 §D), never merged into REGISTRY/the main per-(asset, timeframe)
# sections — kept in a second list plus a second iterator (iter_popular_combo_variants) rather
# than appended into REGISTRY, precisely so nothing about REGISTRY/iter_variants/count_variants
# (already relied on by run_weekly.py's row counts and tests.py's no-lookahead loop) changes.
#
# Numeric parameters exposed for the spec_v3 §C robustness grid: only each combo's OWN
# combo-defining number(s) (the ones spec_v3 §D's own examples name: 9/21, 200, 120, 5) — the
# underlying named indicator's standard recipe numbers (RSI's 14/30/70, MACD's 12/26/9,
# Bollinger's 20/2, Supertrend's 10/3, Ichimoku's 9/26/52) are treated as fixed, exactly like
# REGISTRY's own already-fixed `rsi_mr`/`macd`/`bb_mr`/`supertrend` entries never grid RSI's
# window or MACD's three periods either. `ichimoku_cloud` therefore has zero numeric params (its
# three periods are all "standard recipe" constants) and so gets no robustness grid at all — a
# documented judgment call, since spec_v3 §D names examples rather than an exhaustive list.
# ===========================================================================

_EMA_9_21_VARIANTS = [
    {"params": {"n_fast": 9, "n_slow": 21}, "params_str": "9-21",
     "signal_fn": (lambda df: strat.ema_cross_target_state(df["close"], 9, 21))},
]

_EMA200_MACD_VARIANTS = [
    {"params": {"n_ema": 200}, "params_str": "ema200-macd12.26.9",
     "signal_fn": (lambda df: strat.ema200_macd_target_state(df, 200))},
]

_RSI_UPTREND_VARIANTS = [
    {"params": {"n_sma": 200}, "params_str": "rsi14-sma200",
     "signal_fn": (lambda df: strat.rsi_uptrend_target_state(df, 200))},
]

_BB_SQUEEZE_VARIANTS = [
    {"params": {"n_lookback": 120, "n_confirm": 5}, "params_str": "bb20.2-look120-conf5",
     "signal_fn": (lambda df: strat.bb_squeeze_target_state(df, 120, 5))},
]

_ICHIMOKU_CLOUD_VARIANTS = [
    {"params": {}, "params_str": "9-26-52",
     "signal_fn": (lambda df: strat.ichimoku_cloud_target_state(df))},
]

_HEIKIN_ASHI_TREND_VARIANTS = [
    {"params": {"n_confirm": 2}, "params_str": "conf2",
     "signal_fn": (lambda df: strat.heikin_ashi_trend_target_state(df, 2))},
]

_SUPERTREND_EMA200_VARIANTS = [
    {"params": {"n_ema": 200}, "params_str": "st10.3-ema200",
     "signal_fn": (lambda df: strat.supertrend_ema200_target_state(df, 200))},
]

POPULAR_COMBOS = [
    {"id": "ema_9_21", "name": "EMA 9/21 crossover", "type": "state",
     "rule": "EMA9 > EMA21 -> long, else flat", "variants": _EMA_9_21_VARIANTS},
    {"id": "ema200_macd", "name": "EMA200 trend + MACD cross", "type": "state",
     "rule": "close > EMA200 AND MACD(12,26,9) line > signal -> long; "
             "MACD line < signal OR close < EMA200 -> flat",
     "variants": _EMA200_MACD_VARIANTS},
    {"id": "rsi_uptrend", "name": "RSI dip in uptrend", "type": "state",
     "rule": "RSI(14) < 30 AND close > SMA200 -> long; RSI(14) > 70 OR close < SMA200 -> flat",
     "variants": _RSI_UPTREND_VARIANTS},
    {"id": "bb_squeeze", "name": "Bollinger squeeze breakout", "type": "state",
     "rule": "bandwidth at/under its own prior 120-bar low ('squeeze') within the last 5 bars "
             "AND close > upper band(20,2) -> long; close < middle band -> flat",
     "variants": _BB_SQUEEZE_VARIANTS},
    {"id": "ichimoku_cloud", "name": "Ichimoku cloud breakout", "type": "state",
     "rule": "close > cloud top -> long; close < cloud bottom -> flat "
             "(cloud shifted +26 bars, no lookahead)",
     "variants": _ICHIMOKU_CLOUD_VARIANTS},
    {"id": "heikin_ashi_trend", "name": "Heikin-Ashi colour", "type": "state",
     "rule": "2 consecutive HA close > HA open -> long; first HA close < HA open -> flat",
     "variants": _HEIKIN_ASHI_TREND_VARIANTS},
    {"id": "supertrend_ema200", "name": "Supertrend + EMA200 filter", "type": "state",
     "rule": "Supertrend(10,3) up AND close > EMA200 -> long; Supertrend flips down -> flat",
     "variants": _SUPERTREND_EMA200_VARIANTS},
]


def iter_popular_combo_variants():
    """Same shape as iter_variants() below, but over POPULAR_COMBOS instead of REGISTRY."""
    for entry in POPULAR_COMBOS:
        for variant in entry["variants"]:
            yield entry["id"], entry["name"], entry["type"], variant


def count_popular_combo_variants() -> int:
    return sum(len(entry["variants"]) for entry in POPULAR_COMBOS)


def iter_variants():
    """Yield (strategy_id, strategy_name, strategy_type, variant_dict) for every tradeable
    (verdict-eligible) registry entry — i.e. everything except REFERENCE."""
    for entry in REGISTRY:
        for variant in entry["variants"]:
            yield entry["id"], entry["name"], entry["type"], variant


def count_variants() -> int:
    return sum(len(entry["variants"]) for entry in REGISTRY)


# ===========================================================================
# task B1 "Bot templates" — additive only, nothing above this line (REGISTRY, REFERENCE,
# POPULAR_COMBOS, iter_variants, iter_popular_combo_variants) is modified. Grid bots and DCA
# (safety-order) bots as commonly configured on Pionex / 3Commas — rendered on every page as its
# own separate table (task B1), never merged into REGISTRY or POPULAR_COMBOS, exactly the same
# "second/third separate group, second/third separate iterator" shape POPULAR_COMBOS already
# established above.
#
# Every entry's `type` is "lotsim" — dispatched not through engine.py's simulate_ma_cross/
# simulate_vol_breakout/simulate_hold_n_bars (all of which are single-position, boolean-target-
# state machines) but through bot_engine.run(), a purpose-built LOT-BASED simulator (many small,
# independently funded/tracked open lots) — see bot_engine.py's own module docstring for why these
# strategies need a different engine shape than everything else in this registry. `sim_fn` (rather
# than REGISTRY's/POPULAR_COMBOS' `signal_fn`) takes (df, mask, cost) directly and returns
# (trades, equity_df) itself — there is no separate "target state" series for a lot-based bot to
# precompute, since which lots are open is itself the whole state.
#
# Numeric parameters exposed for the robustness grid (task B1's own instruction: "apply to
# range_pct and so_step only, ±25%"): grid_bot's `range_pct`, dca_bot's/dca_bot_sl's
# `so_step_pct`. `n_grids` (fixed at 20), the base/safety-order sizing (10%, 1.5x-scaling up to 5
# orders), the 1.5% take-profit, and dca_bot_sl's -15% stop-loss are all fixed constants, exactly
# like REGISTRY's own already-fixed recipe numbers (RSI's 14/30/70, Bollinger's 20/2, ...) —
# reused verbatim from bot_engine.py's own fixed defaults, never varied by the grid.
# ===========================================================================

_GRID_BOT_VARIANTS = [
    {"params": {"range_pct": r, "n_grids": 20}, "params_str": f"range{r}-grid20",
     "sim_fn": (lambda df, mask, cost, r=r: bots.simulate_grid_bot(df, mask, cost, r, 20))}
    for r in (10, 20, 30)
]

_DCA_BOT_VARIANTS = [
    {"params": {"so_step_pct": s}, "params_str": f"sostep{s}",
     "sim_fn": (lambda df, mask, cost, s=s: bots.simulate_dca_bot(df, mask, cost, s))}
    for s in (1.5, 2.5)
]

_DCA_BOT_SL_VARIANTS = [
    {"params": {"so_step_pct": 2.5, "stop_loss_pct": 0.15}, "params_str": "sostep2.5-sl15",
     "sim_fn": (lambda df, mask, cost: bots.simulate_dca_bot(df, mask, cost, 2.5,
                                                              stop_loss_pct=0.15))},
]

BOT_TEMPLATES = [
    {"id": "grid_bot", "name": "Grid bot (Pionex-style spot grid)", "type": "lotsim",
     "rule": ("20 fixed grid levels spanning +/-range_pct around the price at activation "
              "(and after every reset); each level below the activation price is funded with "
              "capital/20 and buys when the bar's low reaches it, then sells (and re-arms) when "
              "the bar's high reaches one grid step above; if the close ever exits the grid's "
              "range, every open lot is liquidated at that close and the grid re-activates, "
              "re-centered on that close, the next bar"),
     "variants": _GRID_BOT_VARIANTS},
    {"id": "dca_bot", "name": "DCA bot (3Commas-style safety orders)", "type": "lotsim",
     "rule": ("base order = 10% of capital at the next deal's open; up to 5 safety orders, each "
              "1.5x the previous order's size, spaced so_step_pct apart (compounding) below the "
              "base order's own fill price, filling when the bar's low reaches their level (a "
              "safety order the remaining capital cannot fund is skipped); take-profit sells the "
              "whole deal when the bar's high reaches 1.5% above the average entry price; no "
              "stop-loss; a new deal starts the moment none is open"),
     "variants": _DCA_BOT_VARIANTS},
    {"id": "dca_bot_sl", "name": "DCA bot with stop-loss (3Commas-style)", "type": "lotsim",
     "rule": ("same as the DCA bot above (so_step_pct=2.5 only), plus a -15% stop-loss on the "
              "average entry price that sells the whole deal when the bar's low reaches it"),
     "variants": _DCA_BOT_SL_VARIANTS},
]


def iter_bot_template_variants():
    """Same shape as iter_variants()/iter_popular_combo_variants() above, but over BOT_TEMPLATES
    instead — yields (strategy_id, strategy_name, strategy_type, variant_dict)."""
    for entry in BOT_TEMPLATES:
        for variant in entry["variants"]:
            yield entry["id"], entry["name"], entry["type"], variant


def count_bot_template_variants() -> int:
    return sum(len(entry["variants"]) for entry in BOT_TEMPLATES)
