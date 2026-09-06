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


def iter_variants():
    """Yield (strategy_id, strategy_name, strategy_type, variant_dict) for every tradeable
    (verdict-eligible) registry entry — i.e. everything except REFERENCE."""
    for entry in REGISTRY:
        for variant in entry["variants"]:
            yield entry["id"], entry["name"], entry["type"], variant


def count_variants() -> int:
    return sum(len(entry["variants"]) for entry in REGISTRY)
