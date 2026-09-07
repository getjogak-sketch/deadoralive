"""
run_edge.py — edge research program (see CRITERIA.md, pre-registered before this script existed).

Question: across every strategy variant this codebase can run (registry.py's own ~29, plus an
expanded parameter grid defined ONLY in this file — never added to the public registry) times
every asset/timeframe this codebase has data for, is there any combination that is robustly
profitable after fees across YEARS of non-overlapping walk-forward windows, and not by luck once
the number of combinations tested is honestly accounted for (via a block-bootstrap placebo)?

Reuses strategies.py/indicators.py/engine.py/metrics.py/registry.py/robustness.py completely
unchanged — this file adds no new signal or simulation logic, only the walk-forward loop, the
multiple-testing selection rule, and the placebo.

Runs in GitHub Actions (`.github/workflows/research-edge.yml`, workflow_dispatch) against real
data fetched by `fetch_data.py` in the same job (data/*.csv is gitignored, so a fresh checkout has
none without that step — see README "Edge research program"). This dev box has whatever CSVs
`fetch_data.py --offline` last wrote (BTCUSD only) — enough to smoke-test the real path, not the
full universe; `--synthetic` is the real self-test and needs no data/ at all.

Usage
    python run_edge.py                        # full run (needs data/*.csv; see fetch_data.py)
    python run_edge.py --synthetic             # self-test on fabricated series (no data needed)
    python run_edge.py --max-assets 6          # sample 6 asset/timeframe files (fixed seed)
"""
from __future__ import annotations
import argparse
import glob
import hashlib
import itertools
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config          # noqa: E402  (read-only: costs, editions, data-start dates — never modified)
import registry        # noqa: E402  (REGISTRY + POPULAR_COMBOS, unchanged)
import strategies as strat  # noqa: E402
import engine           # noqa: E402
import metrics          # noqa: E402
import robustness       # noqa: E402  (reused for its neighbour-grid rule and raw-signal dispatch)
import data_loader      # noqa: E402

OUT_DIR = os.path.join(HERE, "results")

# ---------------------------------------------------------------------------
# Fixed parameters — CRITERIA.md. Nowhere else in this program may redefine one of these.
# ---------------------------------------------------------------------------
WINDOW_MONTHS = 6
N_WINDOWS_TARGET = 12
MIN_WINDOWS = 6
HIT_RATE_MIN = 2.0 / 3.0          # == 8/12, CRITERIA.md E3(a)
MEDIAN_PF_MIN = 1.2               # CRITERIA.md E3(b)
MIN_TOTAL_TRADES = 100            # CRITERIA.md E3(c)
CROSS_ASSET_MIN_OTHERS = 2        # CRITERIA.md E3(d)
CROSS_ASSET_HIT_RATE_MIN = 0.5    # CRITERIA.md E3(d)
NEIGHBOUR_FRACTION_MIN = 2.0 / 3.0  # CRITERIA.md E3(e)
N_SHUFFLES = 20                   # CRITERIA.md E3 placebo
PF_CAP_FOR_MEDIAN = 1e6           # caps an "inf" (loss-free) window's PF for median purposes only
SEED = 20260907
NOMINAL_BARS_PER_YEAR = 252.0     # feeds compute_metrics' cagr/sharpe fields, neither ever reported
_EPS = 1e-9

# CRITERIA.md E1: expanded parameter grid, defined ONLY here — never added to registry.py.
EXTRA_GRID = {
    "sma_cross": [(5, 20), (10, 30), (20, 50), (30, 150), (100, 300)],
    "ema_cross": [(8, 21), (20, 55), (50, 200)],
    "donchian": [(10, 5), (40, 20), (100, 50)],
    "vol_breakout": [0.3, 0.4, 0.6, 0.8, 1.0],
    "tsmom": [20, 60, 120, 250],
    "above_sma": [20, 100, 150],
    "rsi_mr": [60, 80],
}


# ---------------------------------------------------------------------------
# Universe: registry.py's own variants + the extra grid above, in one uniform shape.
# ---------------------------------------------------------------------------

def _mk(sid, name, stype, variant):
    params_str = variant["params_str"]
    return {
        "uid": f"{sid}:{params_str}", "sid": sid, "name": name, "type": stype,
        "params": variant["params"], "params_str": params_str,
        "signal_fn": variant["signal_fn"], "hold_n": variant.get("hold_n"),
    }


def _extra_grid_variants():
    """CRITERIA.md E1's extra grid, in registry.py's own variant-dict shape (params/params_str/
    signal_fn) so every downstream function treats a universe entry identically regardless of
    whether it came from registry.py or from here."""
    out = []
    for nf, ns in EXTRA_GRID["sma_cross"]:
        out.append(("sma_cross", "SMA crossover", "state",
                     {"params": {"n_fast": nf, "n_slow": ns}, "params_str": f"{nf}-{ns}",
                      "signal_fn": (lambda df, nf=nf, ns=ns: strat.ma_cross_target_state(df["close"], nf, ns))}))
    for nf, ns in EXTRA_GRID["ema_cross"]:
        out.append(("ema_cross", "EMA crossover", "state",
                     {"params": {"n_fast": nf, "n_slow": ns}, "params_str": f"{nf}-{ns}",
                      "signal_fn": (lambda df, nf=nf, ns=ns: strat.ema_cross_target_state(df["close"], nf, ns))}))
    for nh, nl in EXTRA_GRID["donchian"]:
        out.append(("donchian", "Donchian/Turtle breakout", "state",
                     {"params": {"n_high": nh, "n_low": nl}, "params_str": f"{nh}-{nl}",
                      "signal_fn": (lambda df, nh=nh, nl=nl: strat.donchian_target_state(df, nh, nl))}))
    for k in EXTRA_GRID["vol_breakout"]:
        out.append(("vol_breakout", "Larry Williams volatility breakout", "onebar",
                     {"params": {"k": k}, "params_str": f"k{k}",
                      "signal_fn": (lambda df, k=k: strat.vol_breakout_targets(df, k))}))
    for n in EXTRA_GRID["tsmom"]:
        out.append(("tsmom", "Time-series momentum", "state",
                     {"params": {"n": n}, "params_str": f"n{n}",
                      "signal_fn": (lambda df, n=n: strat.tsmom_target_state(df["close"], n))}))
    for n in EXTRA_GRID["above_sma"]:
        out.append(("above_sma", "Price above SMA", "state",
                     {"params": {"n": n}, "params_str": f"n{n}",
                      "signal_fn": (lambda df, n=n: strat.above_sma_target_state(df["close"], n))}))
    for ex in EXTRA_GRID["rsi_mr"]:
        out.append(("rsi_mr", "RSI mean reversion", "state",
                     {"params": {"exit": ex}, "params_str": f"exit{ex}",
                      "signal_fn": (lambda df, ex=ex: strat.rsi_mr_target_state(df["close"], ex))}))
    return out


def build_universe() -> list:
    universe = [_mk(sid, name, stype, variant) for sid, name, stype, variant in registry.iter_variants()]
    universe += [_mk(sid, name, stype, variant) for sid, name, stype, variant in registry.iter_popular_combo_variants()]
    universe += [_mk(sid, name, stype, variant) for sid, name, stype, variant in _extra_grid_variants()]
    uids = [v["uid"] for v in universe]
    assert len(uids) == len(set(uids)), "duplicate uid in universe (extra grid overlaps a registered point?)"
    return universe


# ---------------------------------------------------------------------------
# Dataset discovery: every data/<SYMBOL>_<TF>.csv this run finds, classified into a
# config.EDITIONS edition (for reporting only) and floored at that edition's own data-start date.
# ---------------------------------------------------------------------------

def _min_date_for_asset(asset: str):
    by_asset = getattr(config, "DATA_START_BY_ASSET", {})
    if asset in by_asset:
        return by_asset[asset]
    if asset in getattr(config, "STOCKS_ASSETS", []):
        return config.STOCKS_DATA_START
    if asset in getattr(config, "MACRO_ASSETS", []):
        return config.MACRO_DATA_START
    if asset in config.ASSETS:
        return config.DATA_START
    return None


def discover_datasets(max_assets: int | None = None, seed: int = SEED) -> list:
    edition_of = {}
    for ed_name, ed in config.EDITIONS.items():
        for a in ed["assets"]:
            edition_of[a] = ed_name

    found = []
    for p in sorted(glob.glob(os.path.join(config.DATA_DIR, "*.csv"))):
        base = os.path.basename(p)[:-4]
        if "_" not in base:
            continue
        asset, tf = base.rsplit("_", 1)
        if asset not in edition_of or asset not in config.COST:
            continue  # not a known edition asset — skip rather than guess a cost/edition for it
        found.append((asset, tf, p))

    if max_assets is not None and len(found) > max_assets:
        rng = random.Random(seed)
        found = sorted(found)
        rng.shuffle(found)
        found = sorted(found[:max_assets])

    datasets = []
    for asset, tf, p in sorted(found):
        try:
            df = data_loader.load_generic(p, min_date=_min_date_for_asset(asset))
        except Exception as e:  # noqa: BLE001 — a bad/partial CSV must not kill the whole run
            print(f"[edge] WARNING: failed to load {p}: {e}")
            continue
        if len(df) < 400:
            print(f"[edge] skip {asset} {tf}: only {len(df)} rows (too short for a walk-forward)")
            continue
        datasets.append({"asset": asset, "tf": tf, "df": df, "cost": config.COST[asset],
                          "edition": edition_of[asset]})
    return datasets


# ---------------------------------------------------------------------------
# Walk-forward windows (CRITERIA.md E2): up to 12 consecutive, non-overlapping 6-month windows
# covering the most recent 6 years of THIS series' own data, most recent window ending on the
# series' last bar. A window whose start falls before the series' first bar is dropped (not
# available), never fabricated.
# ---------------------------------------------------------------------------

def build_windows(df: pd.DataFrame) -> list:
    dates = df["date"]
    end = pd.Timestamp(dates.iloc[-1])
    data_start = pd.Timestamp(dates.iloc[0])
    boundaries = [end]
    for _ in range(N_WINDOWS_TARGET):
        boundaries.append(boundaries[-1] - pd.DateOffset(months=WINDOW_MONTHS))
    boundaries = list(reversed(boundaries))  # oldest .. newest
    n = len(boundaries)
    windows = []
    for i in range(n - 1):
        w_start, w_end = boundaries[i], boundaries[i + 1]
        if w_start < data_start:
            continue  # not enough history for this window — never fabricated, just unavailable
        mask = (dates >= w_start) & (dates <= w_end) if i == n - 2 else (dates >= w_start) & (dates < w_end)
        if int(mask.sum()) == 0:
            continue
        windows.append({"start": w_start, "end": w_end, "mask": mask})
    return windows


# ---------------------------------------------------------------------------
# Per-variant, per-window evaluation (reuses engine.py/metrics.py exactly as every other part of
# this codebase does — no new simulation logic here).
# ---------------------------------------------------------------------------

def compute_signal(variant: dict, df: pd.DataFrame):
    return variant["signal_fn"](df)


def _pf_value(m: dict):
    pf = m["profit_factor"]
    if isinstance(pf, float) and math.isnan(pf):
        return None
    if isinstance(pf, float) and math.isinf(pf):
        return float("inf")
    return float(pf)


def run_signal_on_windows(stype: str, signal_out, hold_n, df: pd.DataFrame, windows: list, cost: float) -> list:
    results = []
    for w in windows:
        mask = w["mask"]
        if stype == "state":
            trades, eq = engine.simulate_ma_cross(df, signal_out, mask, cost)
        elif stype == "onebar":
            target, triggered = signal_out
            trades, eq = engine.simulate_vol_breakout(df, target, triggered, mask, cost)
        elif stype == "holdN":
            trades, eq = engine.simulate_hold_n_bars(df, signal_out, hold_n, mask, cost)
        else:
            continue  # "reference" rows never reach this program (registry.iter_variants excludes them)
        m = metrics.compute_metrics(trades, eq, 0.0, 0.0, NOMINAL_BARS_PER_YEAR, int(mask.sum()))
        ret = m["total_return"]
        results.append({
            "start": w["start"].strftime("%Y-%m-%d"), "end": w["end"].strftime("%Y-%m-%d"),
            "pf": _pf_value(m), "trades": m["n_trades"],
            "ret": None if (isinstance(ret, float) and math.isnan(ret)) else float(ret),
        })
    return results


def aggregate(window_results: list) -> dict:
    """CRITERIA.md E2's per-combination aggregate. A zero-trade window has an undefined PF and is
    never counted as a 'hit' — deliberately conservative (sitting flat is not evidence of edge)."""
    n_eval = len(window_results)
    numeric = [w for w in window_results if w["pf"] is not None]
    hits = sum(1 for w in numeric if w["pf"] >= 1.0)
    hit_rate = (hits / n_eval) if n_eval else 0.0
    pfs_capped = [PF_CAP_FOR_MEDIAN if w["pf"] == float("inf") else w["pf"] for w in numeric]
    median_pf = float(np.median(pfs_capped)) if pfs_capped else None
    worst_pf = float(min(pfs_capped)) if pfs_capped else None
    total_trades = sum(w["trades"] for w in window_results)
    return {"n_windows": n_eval, "hits": hits, "hit_rate": hit_rate,
            "median_pf": median_pf, "worst_pf": worst_pf, "total_trades": total_trades}


def passes_core(agg: dict) -> bool:
    """CRITERIA.md E3(a)-(c)."""
    return (agg["n_windows"] >= MIN_WINDOWS
            and agg["hit_rate"] >= HIT_RATE_MIN - _EPS
            and agg["median_pf"] is not None and agg["median_pf"] >= MEDIAN_PF_MIN
            and agg["total_trades"] >= MIN_TOTAL_TRADES)


def evaluate_dataset(ds: dict, universe: list):
    df, cost = ds["df"], ds["cost"]
    windows = build_windows(df)
    out = {}
    for variant in universe:
        signal_out = compute_signal(variant, df)
        wres = run_signal_on_windows(variant["type"], signal_out, variant["hold_n"], df, windows, cost)
        out[variant["uid"]] = {"agg": aggregate(wres), "windows": wres}
    return out, windows


def evaluate_universe(datasets: list, universe: list):
    all_results, windows_by_key = {}, {}
    for ds in datasets:
        key = (ds["asset"], ds["tf"])
        res, windows = evaluate_dataset(ds, universe)
        all_results[key] = res
        windows_by_key[key] = windows
    return all_results, windows_by_key


# ---------------------------------------------------------------------------
# CRITERIA.md E3(d)/(e): cross-asset consistency and neighbour-grid robustness. Both computed
# LAZILY, only for combinations that already pass (a)-(c) — a pure performance optimisation (a
# combination that fails (a)-(c) can never become a survivor no matter what (d)/(e) say), not a
# rule change.
# ---------------------------------------------------------------------------

def hit_rate_by_asset(all_results: dict, uid: str, datasets: list) -> dict:
    """asset symbol -> best hit rate across that asset's own timeframes, for this exact uid."""
    out = {}
    for ds in datasets:
        r = all_results.get((ds["asset"], ds["tf"]), {}).get(uid)
        if r is None:
            continue
        out[ds["asset"]] = max(out.get(ds["asset"], -1.0), r["agg"]["hit_rate"])
    return out


def neighbour_hit_fraction(sid: str, base_params: dict, stype: str, hold_n, df: pd.DataFrame,
                            windows: list, cost: float):
    """CRITERIA.md E3(e): reuses robustness.py's own neighbour-grid rule (neighbour_values, the
    MA-pair fast<slow filter) and its raw-signal dispatch table (robustness._RAW_SIGNAL) — the
    same numbers, walked forward through this program's OWN windows instead of robustness.py's
    single OOS window, each neighbour scored by its own hit rate >= 50% (not a single-window PF
    check). Returns None (criterion vacuously satisfied — see CRITERIA.md E2) for a strategy with
    no tunable numeric parameter."""
    numeric_names = [k for k, v in base_params.items()
                      if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)]
    if not numeric_names or sid not in robustness._RAW_SIGNAL:
        return None
    candidate_lists = [robustness.neighbour_values(name, base_params[name]) for name in numeric_names]
    is_ma_pair = set(numeric_names) == {"n_fast", "n_slow"}
    seen, total, passing = set(), 0, 0
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
        signal_out = robustness._RAW_SIGNAL[sid](df, params)
        this_hold_n = params.get("n_hold", hold_n)
        wres = run_signal_on_windows(stype, signal_out, this_hold_n, df, windows, cost)
        agg = aggregate(wres)
        total += 1
        if agg["n_windows"] >= 1 and agg["hit_rate"] >= 0.5 - _EPS:
            passing += 1
    return (passing / total) if total else None


def find_survivors(all_results: dict, windows_by_key: dict, datasets: list, universe: list):
    universe_by_uid = {v["uid"]: v for v in universe}
    precandidates = []
    for ds in datasets:
        for uid, r in all_results.get((ds["asset"], ds["tf"]), {}).items():
            if passes_core(r["agg"]):
                precandidates.append((ds, uid, r))

    survivors, hr_cache = [], {}
    for ds, uid, r in precandidates:
        if uid not in hr_cache:
            hr_cache[uid] = hit_rate_by_asset(all_results, uid, datasets)
        others = sorted(a for a, hr in hr_cache[uid].items()
                        if a != ds["asset"] and hr >= CROSS_ASSET_HIT_RATE_MIN - _EPS)
        if len(others) < CROSS_ASSET_MIN_OTHERS:
            continue
        variant = universe_by_uid[uid]
        windows = windows_by_key[(ds["asset"], ds["tf"])]
        nf = neighbour_hit_fraction(variant["sid"], variant["params"], variant["type"],
                                     variant["hold_n"], ds["df"], windows, ds["cost"])
        if nf is not None and nf < NEIGHBOUR_FRACTION_MIN - _EPS:
            continue
        survivors.append({
            "uid": uid, "sid": variant["sid"], "name": variant["name"], "params": variant["params"],
            "params_str": variant["params_str"], "asset": ds["asset"], "tf": ds["tf"],
            "edition": ds["edition"], "agg": r["agg"], "cross_asset_others": others,
            "neighbour_fraction": nf,
        })
    return survivors, precandidates


# ---------------------------------------------------------------------------
# Placebo (CRITERIA.md E3): block bootstrap by calendar month on log returns, rebuilding a full
# synthetic OHLC path — documented simplification, see CRITERIA.md E3 for the reasoning.
# ---------------------------------------------------------------------------

def block_bootstrap_ohlc(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    dates = df["date"]

    if n < 3:
        return df.copy()

    log_ret = np.diff(np.log(close))  # log_ret[i] = log(close[i+1]/close[i]), i in [0, n-2]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel_range = np.where(close != 0, (high - low) / close, 0.0)
    rel_range = np.nan_to_num(rel_range, nan=0.0, posinf=0.0, neginf=0.0)

    # Blocks = calendar-month groups of RETURN positions (position i+1, the bar each return
    # lands on) — each block's own internal order is kept, only the order of blocks is shuffled.
    month_key = dates.dt.to_period("M").iloc[1:].to_numpy()
    positions = np.arange(1, n)
    block_df = pd.DataFrame({"pos": positions, "month": month_key})
    blocks = [g["pos"].to_numpy() for _, g in block_df.groupby("month", sort=True)]
    if not blocks:
        return df.copy()

    target_len = n - 1
    picked, total_len, guard = [], 0, 0
    while total_len < target_len and guard < 20000:
        b = blocks[rng.integers(0, len(blocks))]
        picked.append(b)
        total_len += len(b)
        guard += 1
    src_positions = np.concatenate(picked)[:target_len] if picked else np.array([], dtype=int)
    if len(src_positions) < target_len:  # defensive only — sum(len(blocks)) == n-1 always
        pad = np.resize(positions, target_len - len(src_positions))
        src_positions = np.concatenate([src_positions, pad])

    synth_log_ret = log_ret[src_positions - 1]
    synth_rel_range = rel_range[src_positions]

    synth_close = np.empty(n, dtype=float)
    synth_close[0] = close[0]
    synth_close[1:] = close[0] * np.exp(np.cumsum(synth_log_ret))

    synth_open = np.empty(n, dtype=float)
    synth_open[0] = float(df["open"].iloc[0])
    synth_open[1:] = synth_close[:-1]   # "open = prev close" per CRITERIA.md E3

    rr = np.empty(n, dtype=float)
    rr[0] = rel_range[0]
    rr[1:] = synth_rel_range

    synth_high = synth_close * (1.0 + rr / 2.0)
    synth_low = synth_close * (1.0 - rr / 2.0)
    hi_bound = np.maximum(synth_open, synth_close)
    lo_bound = np.minimum(synth_open, synth_close)
    synth_high = np.maximum(synth_high, hi_bound)
    synth_low = np.minimum(np.maximum(synth_low, 1e-9), lo_bound)

    return pd.DataFrame({
        "date": dates.to_numpy(), "open": synth_open, "high": synth_high,
        "low": synth_low, "close": synth_close,
        "volume": df["volume"].to_numpy() if "volume" in df.columns else np.zeros(n),
    })


def _shuffle_seed(seed: int, s: int, asset: str, tf: str) -> int:
    h = hashlib.sha256(f"{seed}:{s}:{asset}:{tf}".encode()).hexdigest()
    return int(h[:8], 16)


def run_placebo(datasets: list, universe: list, n_shuffles: int = N_SHUFFLES, seed: int = SEED,
                 verbose: bool = False) -> list:
    counts = []
    for s in range(n_shuffles):
        shuffled = []
        for ds in datasets:
            rng = np.random.default_rng(_shuffle_seed(seed, s, ds["asset"], ds["tf"]))
            shuffled.append({**ds, "df": block_bootstrap_ohlc(ds["df"], rng)})
        all_results, windows_by_key = evaluate_universe(shuffled, universe)
        survivors, _ = find_survivors(all_results, windows_by_key, shuffled, universe)
        counts.append(len(survivors))
        if verbose:
            print(f"[edge] placebo shuffle {s + 1}/{n_shuffles}: {len(survivors)} survivor(s)")
    return counts


# ---------------------------------------------------------------------------
# Encryption (CRITERIA.md E4) and output.
# ---------------------------------------------------------------------------

def encrypt_file(in_path: str, out_path: str, passphrase_env: str = "EDGE_PASSPHRASE") -> bool:
    if not os.environ.get(passphrase_env):
        return False
    cmd = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
           "-pass", f"env:{passphrase_env}", "-in", in_path, "-out", out_path]
    subprocess.run(cmd, check=True)
    return True


def write_results(N: int, datasets: list, universe: list, survivors: list, precandidates: list,
                   placebo_counts: list, meta: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    placebo_mean = float(np.mean(placebo_counts)) if placebo_counts else 0.0
    placebo_sd = float(np.std(placebo_counts, ddof=1)) if len(placebo_counts) > 1 else 0.0
    real_n = len(survivors)
    decision = "EDGE FOUND" if real_n > placebo_mean + placebo_sd else "NO EVIDENCE OF EDGE"

    by_edition_n, by_edition_survivors, by_edition_pre = {}, {}, {}
    for ds in datasets:
        by_edition_n[ds["edition"]] = by_edition_n.get(ds["edition"], 0) + len(universe)
    for s in survivors:
        by_edition_survivors[s["edition"]] = by_edition_survivors.get(s["edition"], 0) + 1
    for ds, uid, r in precandidates:
        by_edition_pre[ds["edition"]] = by_edition_pre.get(ds["edition"], 0) + 1

    # ---- private (encrypted) — full identity, only if EDGE_PASSPHRASE is set ----
    private_written = False
    if os.environ.get("EDGE_PASSPHRASE"):
        payload = {"meta": meta, "survivors": survivors}
        fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=OUT_DIR)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            private_written = encrypt_file(tmp_path, os.path.join(OUT_DIR, "survivors.json.enc"))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # ---- public — aggregate counts only, per CRITERIA.md E4 ----
    lines = [
        f"# Edge research program — results ({meta['run_date']})", "",
        "See `CRITERIA.md` for the full pre-registered rule. This file reports only aggregate "
        "counts, per that pre-registration — no strategy id, parameter, or asset name is ever "
        "attached to a survivor here.", "",
        f"**N tested** (strategy variant x asset x timeframe combinations): {N}",
        f"**Universe**: {len(universe)} strategy variants "
        f"({meta['n_registry_variants']} from `registry.py`, {meta['n_extra_grid']} extra-grid-"
        f"only, defined solely in `run_edge.py`) x {len(datasets)} asset/timeframe series.",
        f"**Pre-candidates** (passed hit-rate + median-PF + trade-count, before the cross-asset/"
        f"neighbour filters): {len(precandidates)}",
        f"**Survivors** (passed every CRITERIA.md E3(a)-(e) filter): {real_n}", "",
        "| edition | N tested | pre-candidates | survivors |", "|---|---|---|---|",
    ]
    for ed in sorted(by_edition_n):
        lines.append(f"| {ed} | {by_edition_n[ed]} | {by_edition_pre.get(ed, 0)} | "
                     f"{by_edition_survivors.get(ed, 0)} |")
    lines += [
        "",
        f"**Placebo** (block-bootstrap-by-month noise floor, {len(placebo_counts)} shuffles): "
        f"mean {placebo_mean:.2f}, sd {placebo_sd:.2f}, per-shuffle counts {placebo_counts}.",
        "", f"## Decision: {decision}", "",
    ]
    band = placebo_mean + placebo_sd
    if decision == "EDGE FOUND":
        lines.append(f"{real_n} real survivor(s) exceeds the placebo noise floor "
                     f"(mean {placebo_mean:.2f} + 1 sd {placebo_sd:.2f} = {band:.2f}). Full "
                     f"identity kept private — see below.")
    else:
        lines.append(f"{real_n} real survivor(s) does not exceed the placebo noise floor "
                     f"(mean {placebo_mean:.2f} + 1 sd {placebo_sd:.2f} = {band:.2f}) — this many "
                     f"'survivors' would be expected by chance alone from testing this many "
                     f"combinations, even with no real edge.")
    lines += [
        "", "## Private record",
        ("`results/survivors.json.enc` written (AES-256-CBC via openssl, `EDGE_PASSPHRASE`) — see "
         "README \"Edge research program\" for `decrypt.sh` usage." if private_written else
         "**Not written this run** — `EDGE_PASSPHRASE` was unset, so no private file was produced "
         "(CRITERIA.md E4: nothing private is ever written without it)."),
        "", "## Parameters (fixed, CRITERIA.md)",
        f"- windows: up to {N_WINDOWS_TARGET} x {WINDOW_MONTHS}-month (min {MIN_WINDOWS} available "
        f"to be eligible)",
        f"- hit rate >= {HIT_RATE_MIN:.3f} | median PF >= {MEDIAN_PF_MIN} | trades >= {MIN_TOTAL_TRADES}",
        f"- cross-asset: >= {CROSS_ASSET_MIN_OTHERS} other assets @ hit rate >= {CROSS_ASSET_HIT_RATE_MIN}",
        f"- neighbour hit-rate fraction >= {NEIGHBOUR_FRACTION_MIN:.3f}",
        f"- placebo: {meta.get('shuffles', N_SHUFFLES)} shuffles, seed {meta.get('seed', SEED)}", "",
    ]
    with open(os.path.join(OUT_DIR, "SUMMARY.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump({
            "meta": meta, "N": N, "n_universe": len(universe), "n_datasets": len(datasets),
            "n_precandidates": len(precandidates), "n_survivors": real_n,
            "by_edition": {"n_tested": by_edition_n, "precandidates": by_edition_pre,
                          "survivors": by_edition_survivors},
            "placebo_counts": placebo_counts, "placebo_mean": placebo_mean, "placebo_sd": placebo_sd,
            "decision": decision, "private_written": private_written,
        }, f, indent=2)

    return decision, private_written


# ---------------------------------------------------------------------------
# Self-test (CI gate: `python run_edge.py --synthetic`, no data/ needed). The thorough E5 test
# suite (planted-edge survival, noise non-survival over 3 seeds, truncation, encryption
# round-trip) lives in tests_edge.py instead — this is a lighter, fast sanity pass over the same
# building blocks, mirroring research/attention's and research/demand's own --synthetic pattern.
# ---------------------------------------------------------------------------

def run_synthetic():
    print("[edge] self-test: building universe ...")
    universe = build_universe()
    n_registry = registry.count_variants() + registry.count_popular_combo_variants()
    assert len(universe) == n_registry + sum(len(v) for v in EXTRA_GRID.values())
    print(f"[edge] self-test: universe = {len(universe)} variants ({n_registry} registry + "
          f"{len(universe) - n_registry} extra grid)")

    rng = np.random.default_rng(0)
    idx = pd.date_range("2016-01-01", periods=365 * 8, freq="D")
    n = len(idx)
    drift = np.linspace(0, 3.0, n)
    noise = rng.normal(0, 0.01, n).cumsum() * 0.05
    close = 100.0 * np.exp(drift + noise)
    open_ = np.roll(close, 1)
    df_trend = pd.DataFrame({"date": idx, "open": open_, "high": close * 1.01, "low": close * 0.99,
                              "close": close, "volume": 1.0})
    df_trend.loc[0, "open"] = df_trend.loc[0, "close"]

    trend_variant = next(v for v in universe if v["uid"] == "sma_cross:5-20")
    windows = build_windows(df_trend)
    assert len(windows) >= 6, "expected several walk-forward windows on an 8y synthetic series"
    for w1, w2 in zip(windows, windows[1:]):
        assert w1["end"] <= w2["start"], "windows overlap"

    wres = run_signal_on_windows(trend_variant["type"], compute_signal(trend_variant, df_trend),
                                  trend_variant["hold_n"], df_trend, windows, cost=0.0005)
    agg = aggregate(wres)
    assert agg["hit_rate"] >= 0.5, f"trend-follower hit rate too low on a clean uptrend: {agg}"
    print(f"[edge] self-test: trend hit_rate={agg['hit_rate']:.2f} median_pf={agg['median_pf']}")

    # truncation / no-lookahead: a window built on a truncated copy must never extend past that
    # copy's own last bar.
    cut = df_trend.iloc[: n - 200].reset_index(drop=True)
    for w in build_windows(cut):
        assert w["end"] <= cut["date"].iloc[-1], "window extends past the truncated series' own end"

    # block bootstrap: shape, date alignment, finiteness, internal OHLC sanity
    synth = block_bootstrap_ohlc(df_trend, np.random.default_rng(1))
    assert len(synth) == len(df_trend)
    assert (synth["date"].to_numpy() == df_trend["date"].to_numpy()).all()
    assert np.isfinite(synth[["open", "high", "low", "close"]].to_numpy()).all()
    assert (synth["high"] >= synth["low"]).all()
    print("[edge] self-test: block bootstrap OK")

    ok = _encryption_self_test()
    print(f"[edge] self-test: encryption round-trip {'OK' if ok else 'SKIPPED (no openssl found)'}")

    print("[edge] self-test OK")


def _encryption_self_test() -> bool:
    import shutil
    if shutil.which("openssl") is None:
        return False
    with tempfile.TemporaryDirectory() as d:
        plain, enc, dec = (os.path.join(d, n) for n in ("p.json", "p.json.enc", "p.dec.json"))
        payload = {"hello": "edge", "n": 42}
        with open(plain, "w") as f:
            json.dump(payload, f)
        os.environ["EDGE_PASSPHRASE"] = "self-test-passphrase-not-secret"
        try:
            assert encrypt_file(plain, enc)
            subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-salt",
                             "-pass", "env:EDGE_PASSPHRASE", "-in", enc, "-out", dec], check=True)
            with open(dec) as f:
                got = json.load(f)
            assert got == payload
        finally:
            os.environ.pop("EDGE_PASSPHRASE", None)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="self-test, no data/ needed")
    ap.add_argument("--max-assets", type=int, default=None,
                     help="sample this many asset/timeframe files (fixed seed) instead of the "
                          "full universe — a safety valve if a run approaches the runtime budget")
    ap.add_argument("--shuffles", type=int, default=N_SHUFFLES, help="placebo shuffle count")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    if args.synthetic:
        run_synthetic()
        return 0

    t0 = time.time()
    universe = build_universe()
    n_registry = registry.count_variants() + registry.count_popular_combo_variants()
    print(f"[edge] universe: {len(universe)} variants ({n_registry} registry + "
          f"{len(universe) - n_registry} extra grid)")

    datasets = discover_datasets(max_assets=args.max_assets, seed=args.seed)
    if not datasets:
        print("[edge] no data/*.csv files found for any known edition asset — nothing to do "
              "(run fetch_data.py first). Not treated as a failure.")
        return 0
    asset_list = ", ".join(f"{d['asset']}-{d['tf']}" for d in datasets)
    print(f"[edge] datasets: {len(datasets)} asset/timeframe series ({asset_list})")

    all_results, windows_by_key = evaluate_universe(datasets, universe)
    N = len(universe) * len(datasets)
    survivors, precandidates = find_survivors(all_results, windows_by_key, datasets, universe)
    print(f"[edge] main pass done in {time.time() - t0:.0f}s: N={N} "
          f"pre-candidates={len(precandidates)} survivors={len(survivors)}")

    t1 = time.time()
    placebo_counts = run_placebo(datasets, universe, n_shuffles=args.shuffles, seed=args.seed,
                                  verbose=True)
    print(f"[edge] placebo done in {time.time() - t1:.0f}s: counts={placebo_counts}")

    meta = {
        "run_date": str(date.today()), "n_registry_variants": n_registry,
        "n_extra_grid": len(universe) - n_registry, "seed": args.seed,
        "max_assets": args.max_assets, "shuffles": args.shuffles,
        "runtime_seconds": round(time.time() - t0, 1),
        "assets": [f"{d['asset']}-{d['tf']}" for d in datasets],
    }
    decision, private_written = write_results(N, datasets, universe, survivors, precandidates,
                                              placebo_counts, meta)
    print(f"[edge] decision: {decision} (private survivors file written: {private_written})")
    print(f"[edge] total runtime: {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
