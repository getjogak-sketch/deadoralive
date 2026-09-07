"""
run_weekly.py — spec_v2 §0/§5 weekly pipeline entry point.

    fetch (external step, fetch_data.py) -> [this script] -> results/latest.json,
    results/history/<as_of>.json, docs/index.html, docs/methodology.html, docs/latest.json

Like v1's run_all.py, this refuses to write any results if tests.py fails (spec.md §6:
"실패하면 결과를 출력하지 말고 원인을 보고") — see main()'s hard gate below.

For every (asset, timeframe) in config.ASSETS x config.TIMEFRAMES with a local data file, runs
every registry.py variant net-of-cost over the rolling IS/OOS window (data_loader.rolling_is_oos_
window), assigns a verdict.py badge from OOS metrics, and additionally re-runs each variant's OOS
window at zero cost solely to compute the "fee drag" column (gross OOS return - net OOS return;
spec_v2 §5 — the ONLY place a gross/cost-free number is ever surfaced, and it is explicitly
labeled as such on the page). Assets with no local data file (ETHUSD, in this environment) are
skipped with a warning, per spec_v2 §1 — not a failure.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import registry as reg
import robustness as rb
import verdict as vd
from data_loader import load_generic, rolling_is_oos_window
from engine import (
    simulate_ma_cross, simulate_vol_breakout, simulate_hold_n_bars, simulate_dca_weekly,
    buy_and_hold,
)
from metrics import compute_metrics


def _data_path(symbol: str, tf: str) -> str:
    return os.path.join(config.DATA_DIR, f"{symbol}_{tf}.csv")


def _write_api_v1(edition_key: str, payload: dict):
    """spec_v3 §B: mirror one edition's just-written payload into the static API tree
    docs/api/v1/<edition>/{latest.json, history/<as_of>.json, history/index.json}. Purely
    additive/read-only bookkeeping on top of data this module already computed and already wrote
    to results/latest*.json and docs/*/latest.json above — this function changes no number, it
    only copies. history/index.json is read back and merged (not overwritten) so snapshots from
    earlier weekly runs accumulate rather than being replaced each week."""
    api_dir = os.path.join(config.DOCS_DIR, "api", "v1", edition_key)
    hist_dir = os.path.join(api_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)

    as_of = payload["as_of"]

    with open(os.path.join(api_dir, "latest.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)

    hist_filename = f"{as_of}.json"
    with open(os.path.join(hist_dir, hist_filename), "w") as f:
        json.dump(payload, f, indent=2, default=str)

    index_path = os.path.join(hist_dir, "index.json")
    entries = []
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                entries = json.load(f).get("history", [])
        except (json.JSONDecodeError, OSError):
            entries = []
    entries = [e for e in entries if e.get("as_of") != as_of]
    entries.append({"as_of": as_of, "file": f"history/{hist_filename}"})
    entries.sort(key=lambda e: e["as_of"], reverse=True)
    with open(index_path, "w") as f:
        json.dump({"edition": edition_key, "history": entries}, f, indent=2)


def _run_variant(stype, signal_out, hold_n, df, mask, cost):
    """Dispatch one registry variant's simulation for one period mask; returns (trades, eq_df)."""
    if stype == "state":
        return simulate_ma_cross(df, signal_out, mask, cost)
    elif stype == "onebar":
        target, triggered = signal_out
        return simulate_vol_breakout(df, target, triggered, mask, cost)
    elif stype == "holdN":
        return simulate_hold_n_bars(df, signal_out, hold_n, mask, cost)
    else:
        raise AssertionError(f"unknown strategy type {stype}")


def _metrics_for_period(stype, signal_out, hold_n, df, mask, cost, bars_per_year):
    trades, eq = _run_variant(stype, signal_out, hold_n, df, mask, cost)
    bh_return, bh_mdd, _bh_eq = buy_and_hold(df, mask, cost)
    m = compute_metrics(trades, eq, bh_return, bh_mdd, bars_per_year, int(mask.sum()))
    return m


def _gross_oos_return(stype, signal_out, hold_n, df, oos_mask):
    """Zero-cost OOS total_return, used ONLY for the fee-drag column (spec_v2 §5)."""
    trades, eq = _run_variant(stype, signal_out, hold_n, df, oos_mask, 0.0)
    return float(eq["equity"].iloc[-1] - 1.0) if len(eq) else np.nan


def _clean_metrics(m: dict) -> dict:
    """NaN -> None, +inf -> the string "inf" (JSON has no native inf/NaN) — used for every
    strategy row's is/oos metric dict, both the main registry (spec_v2) and the popular-combos
    group (spec_v3 §D)."""
    return {k: (None if isinstance(v, float) and np.isnan(v) else
                ("inf" if isinstance(v, float) and np.isinf(v) else v))
            for k, v in m.items()}


def _build_variant_row(sid, sname, stype, variant, df, is_mask, oos_mask, cost, bars_per_year,
                        as_of, symbol, tf, group: str | None = None):
    """One (strategy variant, asset, timeframe) row: IS/OOS metrics, verdict, fee drag, the
    spec_v3 §C robustness grid, and the spec_v2 §6 "suspiciously good" flag. Shared by the main
    registry loop (_build_rows_for_asset_tf_impl) and the popular-combos loop
    (_build_popular_combo_rows_impl, spec_v3 §D) — the row shape and every metric definition are
    identical between the two groups; only which registry table a variant came from differs.
    Returns (row: dict, suspicious_entry: dict | None). `group` is an additive, optional tag
    (spec_v3 §D rows get "popular_combos"; the original registry's rows pass None so their JSON
    shape is byte-for-byte what it was before spec_v3 §D — no new key on those rows)."""
    signal_out = variant["signal_fn"](df)
    hold_n = variant.get("hold_n")

    m_is = _metrics_for_period(stype, signal_out, hold_n, df, is_mask, cost, bars_per_year)
    m_oos = _metrics_for_period(stype, signal_out, hold_n, df, oos_mask, cost, bars_per_year)
    gross_oos_return = _gross_oos_return(stype, signal_out, hold_n, df, oos_mask)
    fee_drag = gross_oos_return - m_oos["total_return"]

    verd = vd.assign_verdict(m_oos["profit_factor"], m_oos["n_trades"], m_oos["mdd"], m_oos["bh_mdd"])

    is_suspicious = (
        (isinstance(m_oos["profit_factor"], float) and np.isfinite(m_oos["profit_factor"])
         and m_oos["profit_factor"] > config.SUSPICIOUS_OOS_PF)
        or (m_oos["sharpe"] > config.SUSPICIOUS_OOS_SHARPE)
    )
    suspicious_entry = None
    if is_suspicious:
        suspicious_entry = {
            "strategy_id": sid, "params": variant["params_str"], "asset": symbol, "tf": tf,
            "oos_pf": m_oos["profit_factor"], "oos_sharpe": m_oos["sharpe"],
        }

    # spec_v3 §C: diagnostic-only robustness map, computed from the same OOS window/engine —
    # never read by verdict.assign_verdict above, which already ran on the registered params.
    robustness = rb.compute_robustness(sid, variant["params"], stype, hold_n, df, oos_mask, cost)

    row = {
        "strategy_id": sid, "strategy_name": sname, "type": stype,
        "params": variant["params_str"], "asset": symbol, "timeframe": tf,
        "as_of": str(as_of.date()),
        "verdict": verd,
        "is": _clean_metrics(m_is),
        "oos": {**_clean_metrics(m_oos), "fee_drag": None if np.isnan(fee_drag) else fee_drag},
        "suspicious": bool(is_suspicious),
        "robustness": robustness,
    }
    if group is not None:
        row["group"] = group
    return row, suspicious_entry


def build_rows_for_asset_tf(symbol: str, tf: str, df: pd.DataFrame):
    """Public entry point, kept byte-for-byte equivalent to spec_v2's original behavior: looks up
    cost/bars_per_year from the module-level config dicts (keyed by symbol / by timeframe) and
    delegates to _build_rows_for_asset_tf_impl. Existing callers (the English/Korean editions)
    keep this exact signature and get exactly the same numbers as before spec_v3."""
    cost = config.COST[symbol]
    bars_per_year = config.BARS_PER_YEAR[tf]
    return _build_rows_for_asset_tf_impl(symbol, tf, df, cost, bars_per_year)


def _build_rows_for_asset_tf_impl(symbol: str, tf: str, df: pd.DataFrame, cost: float,
                                   bars_per_year: float):
    """spec_v3 §A extension (additive): the body of build_rows_for_asset_tf above, factored out
    so a new edition whose cost/bars_per_year don't come from the module-level config.COST[symbol]
    / config.BARS_PER_YEAR[tf] dicts (the stocks edition uses 252 trading days/year, not the
    365-calendar-day convention keyed by timeframe alone) can reuse the exact same row-building
    logic without duplicating it. Nothing in this function's body differs from the original
    build_rows_for_asset_tf — it is the same code, just parameterized on cost/bars_per_year
    instead of looking them up itself."""
    as_of, is_mask, oos_mask = rolling_is_oos_window(df, config.OOS_DAYS)

    rows = []
    suspicious = []

    # Shared buy & hold reference for this (asset, tf) — same value for every strategy row's
    # bh_return/bh_mdd columns, and doubles as the standalone `buy_and_hold` reference row.
    bh_is_return, bh_is_mdd, _ = buy_and_hold(df, is_mask, cost)
    bh_oos_return, bh_oos_mdd, _ = buy_and_hold(df, oos_mask, cost)
    bh_oos_return_gross, _bh_oos_mdd_g, _ = buy_and_hold(df, oos_mask, 0.0)

    for sid, sname, stype, variant in reg.iter_variants():
        row, suspicious_entry = _build_variant_row(
            sid, sname, stype, variant, df, is_mask, oos_mask, cost, bars_per_year,
            as_of, symbol, tf)
        rows.append(row)
        if suspicious_entry is not None:
            suspicious.append(suspicious_entry)

    # --- reference rows: buy_and_hold, dca_weekly (no verdict; return/MDD only) ---
    rows.append({
        "strategy_id": "buy_and_hold", "strategy_name": "Buy & hold (reference)", "type": "reference",
        "params": "-", "asset": symbol, "timeframe": tf, "as_of": str(as_of.date()),
        "verdict": None,
        "is": {"total_return": bh_is_return, "mdd": bh_is_mdd},
        "oos": {"total_return": bh_oos_return, "mdd": bh_oos_mdd,
                "fee_drag": bh_oos_return_gross - bh_oos_return},
        "suspicious": False,
    })

    dca_is_return, dca_is_mdd, _ = simulate_dca_weekly(df, is_mask, cost, config.DCA_WEEKLY_AMOUNT)
    dca_oos_return, dca_oos_mdd, _ = simulate_dca_weekly(df, oos_mask, cost, config.DCA_WEEKLY_AMOUNT)
    dca_oos_return_gross, _dca_oos_mdd_g, _ = simulate_dca_weekly(df, oos_mask, 0.0, config.DCA_WEEKLY_AMOUNT)
    rows.append({
        "strategy_id": "dca_weekly", "strategy_name": "Weekly DCA (reference)", "type": "reference",
        "params": "-", "asset": symbol, "timeframe": tf, "as_of": str(as_of.date()),
        "verdict": None,
        "is": {"total_return": _nan_to_none(dca_is_return), "mdd": dca_is_mdd},
        "oos": {"total_return": _nan_to_none(dca_oos_return), "mdd": dca_oos_mdd,
                "fee_drag": None if (np.isnan(dca_oos_return) or np.isnan(dca_oos_return_gross))
                            else dca_oos_return_gross - dca_oos_return},
        "suspicious": False,
    })

    return as_of, rows, suspicious


def build_popular_combo_rows_for_asset_tf(symbol: str, tf: str, df: pd.DataFrame):
    """spec_v3 §D: the "Popular combos" registry group's rows for one (asset, timeframe), for the
    crypto editions (looks up cost/bars_per_year the same way build_rows_for_asset_tf does).
    Delegates to _build_popular_combo_rows_impl; see that function for the row-building details."""
    cost = config.COST[symbol]
    bars_per_year = config.BARS_PER_YEAR[tf]
    return _build_popular_combo_rows_impl(symbol, tf, df, cost, bars_per_year)


def _build_popular_combo_rows_impl(symbol: str, tf: str, df: pd.DataFrame, cost: float,
                                    bars_per_year: float):
    """spec_v3 §D: the "Popular combos" registry group's rows for one (asset, timeframe) —
    exactly the same row shape, metrics, verdict rule, and robustness diagnostic as
    _build_rows_for_asset_tf_impl above, over registry.iter_popular_combo_variants() instead of
    registry.iter_variants(). Parameterized on cost/bars_per_year (rather than looking them up
    from config.COST[symbol]/config.BARS_PER_YEAR[tf] itself) for the same reason
    _build_rows_for_asset_tf_impl is: the stocks edition's 252-trading-day convention. Deliberately
    a SEPARATE function (not merged into the main registry's row-building) because spec_v3 §D
    requires this group be rendered as its own separate table on every page, and because it has no
    reference rows (buy & hold / DCA only make sense once per asset/tf, already produced by the
    main registry's rows). Returns (as_of, rows, suspicious) — same shape as build_rows_for_asset_tf."""
    as_of, is_mask, oos_mask = rolling_is_oos_window(df, config.OOS_DAYS)

    rows = []
    suspicious = []
    for sid, sname, stype, variant in reg.iter_popular_combo_variants():
        row, suspicious_entry = _build_variant_row(
            sid, sname, stype, variant, df, is_mask, oos_mask, cost, bars_per_year,
            as_of, symbol, tf, group="popular_combos")
        rows.append(row)
        if suspicious_entry is not None:
            suspicious.append(suspicious_entry)

    return as_of, rows, suspicious


def _nan_to_none(x):
    return None if (isinstance(x, float) and np.isnan(x)) else x


def main():
    parser = argparse.ArgumentParser(description="netcheck weekly pipeline (spec_v2)")
    parser.add_argument("--offline", action="store_true",
                         help="Informational only here (data is read from config.DATA_DIR either "
                              "way) — kept so `run_weekly.py --offline` matches fetch_data.py's "
                              "flag and the documented offline workflow.")
    parser.add_argument("--skip-tests", action="store_true",
                         help="Skip the hard tests.py gate (debugging only, never for real runs).")
    args = parser.parse_args()

    if not args.skip_tests:
        print("Running tests.py as a hard gate before producing results...")
        proc = subprocess.run([sys.executable, os.path.join(config.BASE_DIR, "tests.py")],
                               capture_output=True, text=True)
        print(proc.stdout[-2000:])
        if proc.returncode != 0:
            print(proc.stderr[-2000:])
            print("tests.py FAILED — aborting. No results written.")
            sys.exit(1)

    all_rows = []
    all_suspicious = []
    all_combo_rows = []
    run_as_of = None

    for symbol in config.ASSETS:
        for tf in config.TIMEFRAMES:
            path = _data_path(symbol, tf)
            if not os.path.exists(path):
                print(f"[run_weekly] WARNING: no data file for {symbol} {tf} ({path}) — skipping "
                      f"(not a failure; run fetch_data.py first).")
                continue
            df = load_generic(path, min_date=config.DATA_START)
            if len(df) < 260:
                print(f"[run_weekly] WARNING: {symbol} {tf} has only {len(df)} bars — skipping "
                      f"(not enough history for the longest-lookback strategies).")
                continue

            as_of, rows, suspicious = build_rows_for_asset_tf(symbol, tf, df)
            all_rows.extend(rows)
            all_suspicious.extend(suspicious)
            _, combo_rows, combo_suspicious = build_popular_combo_rows_for_asset_tf(symbol, tf, df)
            all_combo_rows.extend(combo_rows)
            all_suspicious.extend(combo_suspicious)
            run_as_of = as_of if run_as_of is None else max(run_as_of, as_of)
            print(f"[run_weekly] {symbol} {tf}: as_of={as_of.date()}, {len(rows)} rows "
                  f"({reg.count_variants()} strategy variants + 2 reference), "
                  f"{len(combo_rows)} popular-combo rows")

    if not all_rows:
        print("[run_weekly] No data available for any asset/timeframe — nothing to write.")
        sys.exit(1)

    # Korean-edition addition (requirement 3): tag every row with which edition produced it.
    # Purely additive (a new dict key) — no existing field is touched.
    for r in all_rows:
        r["edition"] = "en"
    for r in all_combo_rows:
        r["edition"] = "en"

    tally = vd.tally([r["verdict"] for r in all_rows if r["verdict"] is not None])

    payload = {
        "project_name": config.PROJECT_NAME,
        "tagline": config.TAGLINE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": str(run_as_of.date()),
        "oos_days": config.OOS_DAYS,
        "tally": tally,
        "rows": all_rows,
        "popular_combos": all_combo_rows,
        "suspicious": all_suspicious,
        "legal_disclaimer": config.LEGAL_DISCLAIMER,
        "repo_url": config.REPO_URL,
        "signup_url": config.SIGNUP_URL,
    }

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    os.makedirs(config.DOCS_DIR, exist_ok=True)

    latest_path = os.path.join(config.RESULTS_DIR, "latest.json")
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    history_path = os.path.join(config.HISTORY_DIR, f"{run_as_of.date()}.json")
    with open(history_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    docs_json_path = os.path.join(config.DOCS_DIR, "latest.json")
    with open(docs_json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nWrote {len(all_rows)} rows to {latest_path}")
    print(f"Wrote {history_path}")
    print(f"Wrote {docs_json_path}")
    print(f"Verdict tally: {tally}")
    if all_suspicious:
        print(f"\nSUSPICIOUS RESULTS (OOS PF > {config.SUSPICIOUS_OOS_PF} or Sharpe > "
              f"{config.SUSPICIOUS_OOS_SHARPE}) — treat as a possible bug, per spec_v2 §6:")
        for s in all_suspicious:
            print(f"  - {s}")

    import build_site
    build_site.build_index(payload)
    build_site.build_methodology()
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'index.html')}")
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'methodology.html')}")

    _write_api_v1("en", payload)
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'v1', 'en', 'latest.json')} (spec_v3 §B)")

    # -------------------------------------------------------------------------------------------
    # Korean edition (Upbit KRW-BTC/KRW-ETH) — additive extension, requirement 3. Nothing above
    # this line (the English edition's results/latest.json, docs/index.html, docs/methodology.html
    # pipeline) is changed by this block. Runs through the exact same build_rows_for_asset_tf /
    # verdict.assign_verdict / metrics.compute_metrics machinery as the English edition above —
    # only the asset list, costs (config.COST["KRW-*"]), and output paths/language differ. See
    # _run_ko_edition below and config.py's EDITIONS/DATA_START_BY_ASSET for the rest.
    # -------------------------------------------------------------------------------------------
    payload_ko = _run_ko_edition()

    # -------------------------------------------------------------------------------------------
    # Stocks edition (SPY/QQQ, daily) — spec_v3 §A, additive extension. Nothing above this line
    # (English/Korean crypto editions) is changed by this block.
    # -------------------------------------------------------------------------------------------
    payload_stocks = _run_stocks_edition()

    # -------------------------------------------------------------------------------------------
    # API docs (spec_v3 §B) — human-readable description of the docs/api/v1/ JSON tree written by
    # each edition's _write_api_v1() call above. Written last so it can be generated even if an
    # edition above this point wrote nothing new this run (it only describes the schema/endpoints,
    # not any one run's numbers).
    # -------------------------------------------------------------------------------------------
    build_site.build_api_readme()
    build_site.build_api_index_html()
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'v1', 'README.md')}")
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'index.html')}")

    # -------------------------------------------------------------------------------------------
    # Programmatic SEO pages, sitemap, robots.txt (this task's §S1) — additive, read-only over the
    # payloads already assembled above. `payload` here is the English edition's own payload dict,
    # already in scope from this function's top half.
    # -------------------------------------------------------------------------------------------
    import seo_pages
    edition_payloads = {"en": payload, "ko": payload_ko, "stocks": payload_stocks}
    seo_manifest = seo_pages.build_all(edition_payloads)
    n_en = len(seo_manifest.get("en", []))
    n_ko = len(seo_manifest.get("ko", []))
    n_stocks = len(seo_manifest.get("stocks", []))
    print(f"Wrote {n_en} /s/ pages (en), {n_ko} /s/ pages (ko), {n_stocks} /s/ pages (stocks)")

    extra_urls = [
        (config.PAGES_URL.rstrip("/") + "/index.html", payload["as_of"]),
        (config.PAGES_URL.rstrip("/") + "/methodology.html", payload["as_of"]),
        (config.PAGES_URL.rstrip("/") + "/api/index.html", payload["as_of"]),
    ]
    if payload_ko:
        extra_urls.append((config.PAGES_URL.rstrip("/") + "/ko/index.html", payload_ko["as_of"]))
        extra_urls.append((config.PAGES_URL.rstrip("/") + "/ko/methodology.html", payload_ko["as_of"]))
    if payload_stocks:
        extra_urls.append((config.PAGES_URL.rstrip("/") + "/stocks/index.html", payload_stocks["as_of"]))
        extra_urls.append((config.PAGES_URL.rstrip("/") + "/stocks/methodology.html", payload_stocks["as_of"]))
    seo_pages.build_sitemap(seo_manifest, extra_urls)
    seo_pages.build_robots()
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'sitemap.xml')}")
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'robots.txt')}")

    return 0


def _run_ko_edition():
    """Korean edition pipeline (spec: "extend the weekly pipeline with a Korean edition").
    Mirrors main()'s en-edition block above, parameterized by config.EDITIONS["ko"], with two
    differences: it tracks each (asset, tf)'s last close (for the Korean page's KRW price
    display), and if NO Upbit data is available at all this week (network-blocked dev env, or a
    4xx from Upbit in CI), it writes an explicit Korean "no data this week" page instead of
    failing — the English edition above must never be affected by Upbit being unavailable."""
    import build_site

    edition = config.EDITIONS["ko"]
    edition_key = "ko"
    assets = edition["assets"]
    out_dir = edition["out"]

    all_rows = []
    all_suspicious = []
    all_combo_rows = []
    last_price = {}
    run_as_of = None

    for symbol in assets:
        for tf in config.TIMEFRAMES:
            path = _data_path(symbol, tf)
            if not os.path.exists(path):
                print(f"[run_weekly] [ko] WARNING: no data file for {symbol} {tf} ({path}) — "
                      f"skipping (not a failure; Upbit may be unavailable this run).")
                continue
            min_date = config.DATA_START_BY_ASSET.get(symbol, config.DATA_START)
            df = load_generic(path, min_date=min_date)
            if len(df) < 260:
                print(f"[run_weekly] [ko] WARNING: {symbol} {tf} has only {len(df)} bars — "
                      f"skipping (not enough history for the longest-lookback strategies).")
                continue

            as_of, rows, suspicious = build_rows_for_asset_tf(symbol, tf, df)
            for r in rows:
                r["edition"] = edition_key
            all_rows.extend(rows)
            all_suspicious.extend(suspicious)
            _, combo_rows, combo_suspicious = build_popular_combo_rows_for_asset_tf(symbol, tf, df)
            for r in combo_rows:
                r["edition"] = edition_key
            all_combo_rows.extend(combo_rows)
            all_suspicious.extend(combo_suspicious)
            last_price[f"{symbol}_{tf}"] = float(df["close"].iloc[-1])
            run_as_of = as_of if run_as_of is None else max(run_as_of, as_of)
            print(f"[run_weekly] [ko] {symbol} {tf}: as_of={as_of.date()}, {len(rows)} rows "
                  f"({reg.count_variants()} strategy variants + 2 reference), "
                  f"{len(combo_rows)} popular-combo rows")

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    if not all_rows:
        print("[run_weekly] [ko] No Upbit data available for any asset/timeframe this week — "
              "writing a Korean 'no data this week' page (not a failure for the English edition).")
        as_of_str = datetime.now(timezone.utc).date().isoformat()
        empty_payload = {
            "project_name": config.PROJECT_NAME, "edition": edition_key, "lang": "ko",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": as_of_str, "oos_days": config.OOS_DAYS,
            "tally": vd.tally([]), "rows": [], "popular_combos": [], "suspicious": [], "last_price": {},
            "legal_disclaimer_ko": config.LEGAL_DISCLAIMER_KO,
            "repo_url": config.REPO_URL, "signup_url": config.SIGNUP_URL,
        }
        with open(os.path.join(config.RESULTS_DIR, f"latest_{edition_key}.json"), "w") as f:
            json.dump(empty_payload, f, indent=2, default=str)
        with open(os.path.join(config.HISTORY_DIR, f"{edition_key}_{as_of_str}.json"), "w") as f:
            json.dump(empty_payload, f, indent=2, default=str)
        with open(os.path.join(out_dir, "latest.json"), "w") as f:
            json.dump(empty_payload, f, indent=2, default=str)
        build_site.build_empty_edition_page_ko(os.path.join(out_dir, "index.html"), as_of_str)
        build_site.build_methodology_ko(os.path.join(out_dir, "methodology.html"))
        print(f"Wrote {os.path.join(out_dir, 'index.html')} (no-data notice)")
        print(f"Wrote {os.path.join(out_dir, 'methodology.html')}")
        _write_api_v1(edition_key, empty_payload)
        print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'v1', edition_key, 'latest.json')} (spec_v3 §B)")
        return

    tally = vd.tally([r["verdict"] for r in all_rows if r["verdict"] is not None])
    payload = {
        "project_name": config.PROJECT_NAME, "edition": edition_key, "lang": "ko",
        "tagline": config.TAGLINE_KO,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": str(run_as_of.date()), "oos_days": config.OOS_DAYS,
        "tally": tally, "rows": all_rows, "popular_combos": all_combo_rows,
        "suspicious": all_suspicious,
        "last_price": last_price,
        "legal_disclaimer": config.LEGAL_DISCLAIMER,
        "legal_disclaimer_ko": config.LEGAL_DISCLAIMER_KO,
        "repo_url": config.REPO_URL, "signup_url": config.SIGNUP_URL,
    }

    latest_path = os.path.join(config.RESULTS_DIR, f"latest_{edition_key}.json")
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    history_path = os.path.join(config.HISTORY_DIR, f"{edition_key}_{run_as_of.date()}.json")
    with open(history_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    docs_json_path = os.path.join(out_dir, "latest.json")
    with open(docs_json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\n[ko] Wrote {len(all_rows)} rows to {latest_path}")
    print(f"Wrote {history_path}")
    print(f"Wrote {docs_json_path}")
    print(f"[ko] Verdict tally: {tally}")
    if all_suspicious:
        print(f"\n[ko] SUSPICIOUS RESULTS (OOS PF > {config.SUSPICIOUS_OOS_PF} or Sharpe > "
              f"{config.SUSPICIOUS_OOS_SHARPE}) — treat as a possible bug, per spec_v2 §6:")
        for s in all_suspicious:
            print(f"  - {s}")

    build_site.build_index_ko(payload, os.path.join(out_dir, "index.html"))
    build_site.build_methodology_ko(os.path.join(out_dir, "methodology.html"))
    print(f"Wrote {os.path.join(out_dir, 'index.html')}")
    print(f"Wrote {os.path.join(out_dir, 'methodology.html')}")

    _write_api_v1(edition_key, payload)
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'v1', edition_key, 'latest.json')} (spec_v3 §B)")

    return payload


def _run_stocks_edition():
    """Stocks edition (SPY/QQQ, daily) — spec_v3 §A. English-only, reuses the exact same
    registry/engine/verdict machinery as the crypto editions above, via
    _build_rows_for_asset_tf_impl (see its docstring for why the plain build_rows_for_asset_tf
    isn't reused directly: this edition's bars_per_year convention, config.STOCKS_BARS_PER_YEAR =
    252 trading days/year, differs from the calendar-day convention config.BARS_PER_YEAR[tf]
    encodes for the crypto editions). If neither SPY nor QQQ has a local data file this run
    (Stooq/Yahoo both blocked or both failing), nothing is written for this edition and a warning
    is printed — never a hard failure for the crypto editions, matching the Korean edition's
    "no data this week" tolerance, except here (since English text has no separate
    "no-data" page requirement in spec_v3 §A) we simply skip writing this edition's pages."""
    import build_site

    edition = config.EDITIONS["stocks"]
    assets = edition["assets"]
    timeframes = edition["timeframes"]
    out_dir = edition["out"]

    all_rows = []
    all_suspicious = []
    all_combo_rows = []
    run_as_of = None

    for symbol in assets:
        for tf in timeframes:
            path = _data_path(symbol, tf)
            if not os.path.exists(path):
                print(f"[run_weekly] [stocks] WARNING: no data file for {symbol} {tf} ({path}) — "
                      f"skipping (not a failure; Stooq/Yahoo may be unavailable this run).")
                continue
            df = load_generic(path, min_date=config.STOCKS_DATA_START)
            if len(df) < 260:
                print(f"[run_weekly] [stocks] WARNING: {symbol} {tf} has only {len(df)} bars — "
                      f"skipping (not enough history for the longest-lookback strategies).")
                continue

            cost = config.COST[symbol]
            as_of, rows, suspicious = _build_rows_for_asset_tf_impl(
                symbol, tf, df, cost, config.STOCKS_BARS_PER_YEAR)
            for r in rows:
                r["edition"] = "stocks"
            all_rows.extend(rows)
            all_suspicious.extend(suspicious)
            _, combo_rows, combo_suspicious = _build_popular_combo_rows_impl(
                symbol, tf, df, cost, config.STOCKS_BARS_PER_YEAR)
            for r in combo_rows:
                r["edition"] = "stocks"
            all_combo_rows.extend(combo_rows)
            all_suspicious.extend(combo_suspicious)
            run_as_of = as_of if run_as_of is None else max(run_as_of, as_of)
            print(f"[run_weekly] [stocks] {symbol} {tf}: as_of={as_of.date()}, {len(rows)} rows "
                  f"({reg.count_variants()} strategy variants + 2 reference), "
                  f"{len(combo_rows)} popular-combo rows")

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    if not all_rows:
        print("[run_weekly] [stocks] No SPY/QQQ data available this week — skipping the stocks "
              "edition's pages (not a failure for the crypto editions).")
        return None

    tally = vd.tally([r["verdict"] for r in all_rows if r["verdict"] is not None])
    payload = {
        "project_name": config.PROJECT_NAME, "edition": "stocks", "lang": "en",
        "tagline": config.TAGLINE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": str(run_as_of.date()), "oos_days": config.OOS_DAYS,
        "tally": tally, "rows": all_rows, "popular_combos": all_combo_rows,
        "suspicious": all_suspicious,
        "legal_disclaimer": config.LEGAL_DISCLAIMER,
        "repo_url": config.REPO_URL, "signup_url": config.SIGNUP_URL,
    }

    latest_path = os.path.join(config.RESULTS_DIR, "latest_stocks.json")
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    history_path = os.path.join(config.HISTORY_DIR, f"stocks_{run_as_of.date()}.json")
    with open(history_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    docs_json_path = os.path.join(out_dir, "latest.json")
    with open(docs_json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\n[stocks] Wrote {len(all_rows)} rows to {latest_path}")
    print(f"Wrote {history_path}")
    print(f"Wrote {docs_json_path}")
    print(f"[stocks] Verdict tally: {tally}")
    if all_suspicious:
        print(f"\n[stocks] SUSPICIOUS RESULTS (OOS PF > {config.SUSPICIOUS_OOS_PF} or Sharpe > "
              f"{config.SUSPICIOUS_OOS_SHARPE}) — treat as a possible bug, per spec_v2 §6:")
        for s in all_suspicious:
            print(f"  - {s}")

    build_site.build_index(payload, out_path=os.path.join(out_dir, "index.html"),
                            assets=assets, timeframes=timeframes,
                            lang_links=('<a href="../index.html">English (crypto)</a> &middot; '
                                        '<a href="../ko/index.html">한국어</a>'))
    build_site.build_methodology(out_path=os.path.join(out_dir, "methodology.html"),
                                  assets=assets,
                                  lang_links=('<a href="../methodology.html">English (crypto)</a> '
                                              '&middot; <a href="../ko/methodology.html">한국어</a>'))
    print(f"Wrote {os.path.join(out_dir, 'index.html')}")
    print(f"Wrote {os.path.join(out_dir, 'methodology.html')}")

    _write_api_v1("stocks", payload)
    print(f"Wrote {os.path.join(config.DOCS_DIR, 'api', 'v1', 'stocks', 'latest.json')} (spec_v3 §B)")

    return payload


if __name__ == "__main__":
    sys.exit(main())
