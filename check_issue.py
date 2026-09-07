"""
check_issue.py — spec_v3 §E: "Check my strategy" via GitHub Issues.

No server, no eval. A GitHub Issue Form (.github/ISSUE_TEMPLATE/check-strategy.yml) collects
edition/asset/timeframe/strategy/params from a user; a workflow (.github/workflows/check.yml)
runs THIS script against the issue body and either posts one scorecard comment (valid input) or
one "here's what's wrong" comment (invalid input), then closes the issue. This script never talks
to GitHub except in --post mode, and never calls eval()/exec() on any user-supplied text — every
field is parsed with a strict, fully-anchored regex and only ever used as a dict lookup key or fed
through float()/int() after that regex has already validated its shape.

    python3 check_issue.py --body sample.md --dry-run     # print the comment markdown, exit 0
    python3 check_issue.py --body sample.md --post \\
        --repo owner/repo --issue-number 123              # also post + close via the REST API

Reuses run_weekly.py's own `_build_variant_row` for the scorecard (byte-for-byte the same metrics/
verdict/fee-drag/robustness logic the weekly page uses — this script adds no new backtest math),
and registry.py/robustness.py for "which strategy id needs which numeric params, computed how".

Paid "extended check" (this task's T2, additive on top of spec_v3 §E): the issue form's optional
"License key" field, when present, is verified against Gumroad's license API
(`POST https://api.gumroad.com/v2/licenses/verify`, `product_id` from env `GUMROAD_PRODUCT_ID`).
A verified key runs the requested strategy on every asset/timeframe of the chosen edition instead
of just one, with the full OOS trade list, a 5x5 robustness grid, and an OOS monthly-returns
table (`run_extended_check`/`render_extended_comment`). An absent/invalid/refunded key — or
`GUMROAD_PRODUCT_ID` simply not being set yet — silently falls back to the ordinary free check
plus one explanatory line; the license key itself is never printed or logged anywhere in this
module.
"""
from __future__ import annotations
import argparse
import itertools
import json
import math
import os
import re
import sys
import time

import numpy as np
import pandas as pd

import config
import registry as reg
import robustness as rb
from data_loader import load_generic, rolling_is_oos_window
from metrics import compute_metrics
from run_weekly import _build_variant_row, _data_path, _run_variant

# ---------------------------------------------------------------------------
# Known editions / assets / timeframes (spec_v3 §E: "asset ... a single dropdown listing all
# assets with the edition prefix" — so the issue form's Asset field value is always
# "<edition>:<ASSET>", e.g. "ko:KRW-BTC"; this sidesteps GitHub Issue Forms having no
# conditional-dropdown support and lets validation catch an edition/asset mismatch directly).
# ---------------------------------------------------------------------------
EDITION_ASSETS = {
    "en": list(config.ASSETS),
    "ko": list(config.UPBIT_ASSETS),
    "stocks": list(config.STOCKS_ASSETS),
}
EDITION_TIMEFRAMES = {
    "en": list(config.TIMEFRAMES),
    "ko": list(config.TIMEFRAMES),
    "stocks": list(config.STOCKS_TIMEFRAMES),
}
EDITION_MIN_DATE = {
    # en/ko look up per-asset (Upbit assets are floored later than Bitstamp's); stocks is fixed.
    "stocks": config.STOCKS_DATA_START,
}
EDITION_METHODOLOGY_PATH = {
    "en": "/methodology.html",
    "ko": "/ko/methodology.html",
    "stocks": "/stocks/methodology.html",
}

# strategy id -> registry entry, searched across REGISTRY + POPULAR_COMBOS, excluding REFERENCE
# (spec_v3 §E: "registry ids incl. POPULAR_COMBOS, ex-reference") — built once from the single
# source of truth in registry.py rather than hand-duplicated here, so this list can never drift
# out of sync with the real registry.
_ALL_ENTRIES = {e["id"]: e for e in reg.REGISTRY} | {e["id"]: e for e in reg.POPULAR_COMBOS}
KNOWN_STRATEGY_IDS = sorted(_ALL_ENTRIES.keys())


def _entry_for(sid: str) -> dict | None:
    return _ALL_ENTRIES.get(sid)


def _expected_param_names(entry: dict) -> frozenset:
    """Every variant of one strategy id shares the same param *names* (only the values differ),
    so the first variant's keys are the full answer — e.g. sma_cross's 3 registered variants are
    all {n_fast, n_slow}, dip_pct's one variant is {threshold, n_hold}, and a fixed strategy's one
    variant is {}."""
    return frozenset(entry["variants"][0]["params"].keys())


# ---------------------------------------------------------------------------
# Per-parameter-name sanity bounds (spec_v3 §E: "within sane bounds: window lengths 2..500, k
# 0.1..2.0, fast<slow"). Window-length names and their range are the spec's own words; the "exit"
# (RSI mean-reversion exit level) and "threshold" (dip_pct's negative bar-return trigger) bounds
# are NOT named by the spec and are a documented judgment call, chosen to keep both parameters on
# their natural scale (an RSI level is 0-100; a "dip" threshold is a small negative fraction).
# ---------------------------------------------------------------------------
_WINDOW_PARAMS = {"n", "n_fast", "n_slow", "n_high", "n_low", "n_lookback", "n_confirm",
                   "n_ema", "n_sma", "sma_n", "n_hold"}
_WINDOW_MIN, _WINDOW_MAX = 2, 500
_K_MIN, _K_MAX = 0.1, 2.0
_EXIT_MIN, _EXIT_MAX = 1, 99          # judgment call: RSI exit level, exclusive of 0/100
_THRESHOLD_MIN, _THRESHOLD_MAX = -0.5, -0.001   # judgment call: dip_pct's negative bar-return %


class ValidationError(Exception):
    """Raised with a human-readable message that is safe to paste verbatim into a GitHub comment
    (never includes anything beyond the user's own already-regex-validated field values)."""


# ---------------------------------------------------------------------------
# Parsing — GitHub Issue Forms render each field as "### <Label>\n\n<value>\n\n", checkboxes as
# "- [x] <option label>". Fully anchored regexes only; never eval()/exec() on any of this text.
# ---------------------------------------------------------------------------
_FIELD_RE = re.compile(r"^###[ \t]+(?P<label>.+?)[ \t]*\n(?P<value>.*?)(?=\n###[ \t]|\Z)",
                        re.DOTALL | re.MULTILINE)
_PARAM_TOKEN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?(?:\d+\.\d*|\.\d+|\d+))\s*$")
_ASSET_FIELD_RE = re.compile(r"^([a-z]+):([A-Za-z0-9._-]+)$")

_LABEL_TO_KEY = {
    "edition": "edition",
    "asset": "asset",
    "timeframe": "timeframe",
    "strategy type": "strategy",
    "params": "params",
    "confirmation": "confirmation",
}
# Optional field (this task's T2): absent entirely from an issue body predating this field's
# addition to the template, or rendered as "_No response_" when left blank — either way, never
# part of the "missing required field" check below, and never logged anywhere in this module.
_OPTIONAL_LABEL_TO_KEY = {
    "license key (optional, for extended check)": "license",
}


def parse_issue_body(body: str) -> dict:
    """Extract the form fields from a rendered GitHub Issue Form body. Returns raw strings
    (edition/asset/timeframe/strategy/params/license) plus confirmation: bool. Never raises on
    malformed input beyond a plain ValidationError — a hand-edited or corrupted issue body is a
    validation failure, not a crash."""
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(body or ""):
        label = m.group("label").strip().lower()
        key = _LABEL_TO_KEY.get(label) or _OPTIONAL_LABEL_TO_KEY.get(label)
        if key is None:
            continue
        fields[key] = m.group("value").strip()

    missing = [lbl for lbl, key in _LABEL_TO_KEY.items() if key not in fields]
    if missing:
        raise ValidationError(
            f"Could not find the expected field(s) in the issue body: {', '.join(missing)}. "
            f"Please use the \"Check my strategy\" issue template.")

    for k in ("edition", "asset", "timeframe", "strategy", "params"):
        if fields[k] == "_No response_":
            fields[k] = ""
    fields.setdefault("license", "")
    if fields["license"] == "_No response_":
        fields["license"] = ""

    confirmation_raw = fields.pop("confirmation")
    fields["confirmation"] = bool(re.search(r"-\s*\[x\]", confirmation_raw, re.IGNORECASE))
    return fields


def parse_params(text: str) -> dict:
    """`name=value, name=value` -> {name: float}. Empty/whitespace-only text -> {}. Raises
    ValidationError on anything that doesn't match the strict per-token regex — no eval, no
    arithmetic, no operators beyond a single optional leading '-' and decimal point."""
    text = (text or "").strip()
    if not text:
        return {}
    out = {}
    for raw_token in text.split(","):
        token = raw_token.strip()
        if not token:
            continue
        m = _PARAM_TOKEN_RE.match(token)
        if not m:
            raise ValidationError(
                f"Could not parse params token '{token}' — expected the exact format "
                f"'name=value' (a bare number, optionally negative/decimal), separated by commas.")
        name, value = m.group(1), m.group(2)
        if name in out:
            raise ValidationError(f"Parameter '{name}' was given more than once.")
        out[name] = float(value)
    return out


def _check_param_bounds(name: str, value: float):
    if name in _WINDOW_PARAMS:
        if not float(value).is_integer():
            raise ValidationError(f"Parameter '{name}' must be a whole number, got {value}.")
        iv = int(value)
        if not (_WINDOW_MIN <= iv <= _WINDOW_MAX):
            raise ValidationError(
                f"Parameter '{name}={iv}' is out of range — window lengths must be between "
                f"{_WINDOW_MIN} and {_WINDOW_MAX}.")
    elif name == "k":
        if not (_K_MIN <= value <= _K_MAX):
            raise ValidationError(
                f"Parameter 'k={value}' is out of range — k must be between {_K_MIN} and {_K_MAX}.")
    elif name == "exit":
        if not (_EXIT_MIN <= value <= _EXIT_MAX):
            raise ValidationError(
                f"Parameter 'exit={value}' is out of range — an RSI exit level must be between "
                f"{_EXIT_MIN} and {_EXIT_MAX}.")
    elif name == "threshold":
        if not (_THRESHOLD_MIN <= value <= _THRESHOLD_MAX):
            raise ValidationError(
                f"Parameter 'threshold={value}' is out of range — must be a negative fraction "
                f"between {_THRESHOLD_MIN} and {_THRESHOLD_MAX} (e.g. -0.05 for a 5% dip).")
    else:
        raise ValidationError(f"Unrecognized parameter name '{name}'.")


def validate(fields: dict) -> dict:
    """Raises ValidationError with a human-readable message on any problem; otherwise returns a
    normalized dict: {edition, asset, timeframe, strategy_id, entry, params, params_str}."""
    if not fields.get("confirmation"):
        raise ValidationError(
            'The required confirmation checkbox ("I understand this is an automated educational '
            'backtest, not investment advice.") was not checked.')

    edition = fields["edition"].strip()
    if edition not in EDITION_ASSETS:
        raise ValidationError(
            f"Unknown edition '{edition}' — must be one of {sorted(EDITION_ASSETS)}.")

    asset_field = fields["asset"].strip()
    m = _ASSET_FIELD_RE.match(asset_field)
    if not m:
        raise ValidationError(
            f"Asset field '{asset_field}' is not in the expected 'edition:ASSET' form.")
    asset_edition, asset = m.group(1), m.group(2)
    if asset_edition != edition:
        raise ValidationError(
            f"Asset '{asset_field}' is prefixed for edition '{asset_edition}' but the Edition "
            f"field says '{edition}' — pick the asset that matches your chosen edition.")
    if asset not in EDITION_ASSETS[edition]:
        raise ValidationError(
            f"Asset '{asset}' is not part of the '{edition}' edition "
            f"(expected one of {EDITION_ASSETS[edition]}).")

    timeframe = fields["timeframe"].strip()
    if timeframe not in ("1d", "4h"):
        raise ValidationError(f"Unknown timeframe '{timeframe}' — must be '1d' or '4h'.")
    if timeframe not in EDITION_TIMEFRAMES[edition]:
        raise ValidationError(
            f"The '{edition}' edition does not have a '{timeframe}' timeframe "
            f"(available: {EDITION_TIMEFRAMES[edition]}).")

    sid = fields["strategy"].strip()
    entry = _entry_for(sid)
    if entry is None:
        raise ValidationError(
            f"Unknown strategy type '{sid}' — must be one of {KNOWN_STRATEGY_IDS}.")

    expected_names = _expected_param_names(entry)
    params = parse_params(fields["params"])

    if not expected_names:
        if params:
            raise ValidationError(
                f"Strategy '{sid}' has fixed parameters — the Params field must be left empty, "
                f"got '{fields['params']}'.")
    else:
        given_names = set(params.keys())
        missing_names = expected_names - given_names
        extra_names = given_names - expected_names
        if missing_names:
            raise ValidationError(
                f"Strategy '{sid}' needs parameter(s) {sorted(missing_names)} — "
                f"got '{fields['params']}'.")
        if extra_names:
            raise ValidationError(
                f"Strategy '{sid}' does not take parameter(s) {sorted(extra_names)} — "
                f"expected only {sorted(expected_names)}.")
        for name, value in params.items():
            _check_param_bounds(name, value)
        # Window-length params must be ints for the underlying pandas rolling-window calls (the
        # bounds check above already proved each is a whole number) — everything else (k, exit,
        # threshold) stays a float, matching what registry.py's own variants store.
        params = {name: (int(value) if name in _WINDOW_PARAMS else value)
                  for name, value in params.items()}
        if "n_fast" in params and "n_slow" in params and not (params["n_fast"] < params["n_slow"]):
            raise ValidationError(
                f"n_fast ({params['n_fast']}) must be less than n_slow ({params['n_slow']}).")

    params_str = ", ".join(f"{k}={v:g}" for k, v in sorted(params.items())) or "fixed"

    return {
        "edition": edition, "asset": asset, "timeframe": timeframe,
        "strategy_id": sid, "entry": entry, "params": params, "params_str": params_str,
    }


# ---------------------------------------------------------------------------
# Paid "extended check" — Gumroad license verification (this task's T2).
#
# Request shape per Gumroad's own documented License Verification API
# (https://help.gumroad.com/article/76-license-keys#verify-a-license-key):
#   POST https://api.gumroad.com/v2/licenses/verify
#   form fields: product_id, license_key, increment_uses_count=true
# A verified purchase is HTTP 200 with JSON {"success": true, "purchase": {...}} where none of
# purchase.refunded / purchase.chargebacked / purchase.disputed is true. Everything else —
# HTTP != 200, success: false (bad key, wrong product), a malformed/non-JSON response, or a
# network failure — is treated as "not verified", never as a crash: `build_comment` always falls
# back to the ordinary free check on any of these. Never network-exercised in this dev
# environment (api.gumroad.com is blocked here like every other external API); written against
# Gumroad's own published request/response shape and unit-tested with a monkeypatched
# `requests.post` in tests.py.
# ---------------------------------------------------------------------------
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"


def verify_gumroad_license(license_key: str) -> tuple[bool, str]:
    """Returns (verified, reason). NEVER prints/logs `license_key` — the caller must not either.
    If GUMROAD_PRODUCT_ID is unset, every key is treated as unverified with a clear, fixed reason
    (spec's own wording) and no HTTP call is made at all (there is nothing to verify against)."""
    product_id = os.environ.get("GUMROAD_PRODUCT_ID")
    if not product_id:
        return False, "extended checks are not enabled yet"
    if not license_key:
        return False, "no license key was provided"

    import requests
    try:
        resp = requests.post(
            GUMROAD_VERIFY_URL,
            # Gumroad accepts either the API `product_id` (long, may contain '=') or the short
            # `product_permalink` from the product URL (e.g. "itilsr"). Send whichever we were given.
            data={("product_id" if (len(product_id) > 20 or "=" in product_id) else "product_permalink"): product_id,
                  "license_key": license_key,
                  "increment_uses_count": "true"},
            timeout=20,
        )
    except requests.RequestException:
        return False, "license key could not be verified"

    if resp.status_code != 200:
        return False, "license key could not be verified"
    try:
        payload = resp.json()
    except ValueError:
        return False, "license key could not be verified"

    if not payload.get("success"):
        return False, "license key could not be verified"
    purchase = payload.get("purchase") or {}
    if purchase.get("refunded") or purchase.get("chargebacked") or purchase.get("disputed"):
        return False, "license key could not be verified"
    return True, "license key verified"


def _license_note_line(reason: str) -> str:
    """The single explanatory line appended to a free-check comment when a license key was
    supplied but did not verify (spec's own wording, extended with the "not enabled yet" case)."""
    text = reason[0].upper() + reason[1:] if reason else "License key could not be verified"
    line = f"{text} — ran the free check instead."
    gumroad_url = os.environ.get("GUMROAD_URL", "").strip()
    if gumroad_url:
        line += f" Keys are sold at {gumroad_url}."
    return line


# ---------------------------------------------------------------------------
# Running the single requested variant, reusing run_weekly._build_variant_row so the scorecard is
# computed with the EXACT SAME engine/cost/threshold code the weekly pipeline uses — this module
# adds no new backtest math of its own.
# ---------------------------------------------------------------------------

def _make_signal_fn(sid: str, entry: dict, params: dict):
    expected_names = _expected_param_names(entry)
    if not expected_names:
        # Fixed strategy: the registry's own single variant already computes its signal from no
        # user input at all — reuse it verbatim.
        return entry["variants"][0]["signal_fn"]
    if sid not in rb._RAW_SIGNAL:
        raise ValidationError(
            f"Strategy '{sid}' has numeric parameters but no raw-signal function is registered "
            f"for it — this is a bug in check_issue.py, not a problem with your issue.")
    return lambda df: rb._RAW_SIGNAL[sid](df, params)


def load_asset_df(edition: str, asset: str, timeframe: str) -> pd.DataFrame:
    path = _data_path(asset, timeframe)
    if not os.path.exists(path):
        raise ValidationError(
            f"No committed data file for {asset} {timeframe} ({os.path.basename(path)}) — this "
            f"asset/timeframe has no local data to check against right now. Try again after the "
            f"next weekly run, or pick a different asset.")
    if edition == "stocks":
        min_date = config.STOCKS_DATA_START
    else:
        min_date = config.DATA_START_BY_ASSET.get(asset, config.DATA_START)
    df = load_generic(path, min_date=min_date)
    if len(df) < 260:
        raise ValidationError(
            f"{asset} {timeframe} only has {len(df)} committed bars — not enough history to "
            f"check this strategy yet.")
    return df


def _build_variant(sid: str, entry: dict, params: dict, params_str: str) -> dict:
    variant = {
        "params": params if params else {},
        "params_str": params_str,
        "signal_fn": _make_signal_fn(sid, entry, params),
    }
    if entry["type"] == "holdN":
        variant["hold_n"] = int(params.get("n_hold", entry["variants"][0].get("hold_n")))
    return variant


def run_check(normalized: dict) -> dict:
    """Runs the one requested variant and returns the exact row dict run_weekly.py would have
    produced for it (verdict, is/oos metrics incl. fee_drag, robustness grid) — see
    _build_variant_row's own docstring for the row shape."""
    edition, asset, tf = normalized["edition"], normalized["asset"], normalized["timeframe"]
    sid, entry, params = normalized["strategy_id"], normalized["entry"], normalized["params"]

    df = load_asset_df(edition, asset, tf)
    as_of, is_mask, oos_mask = rolling_is_oos_window(df, config.OOS_DAYS)

    cost = config.COST[asset]
    bars_per_year = (config.STOCKS_BARS_PER_YEAR if edition == "stocks"
                      else config.BARS_PER_YEAR[tf])

    variant = _build_variant(sid, entry, params, normalized["params_str"])

    row, _suspicious = _build_variant_row(
        sid, entry["name"], entry["type"], variant, df, is_mask, oos_mask, cost, bars_per_year,
        as_of, asset, tf)
    row["edition"] = edition
    return row


# ---------------------------------------------------------------------------
# Extended check (licensed, this task's T2) — runs the SAME requested variant across every
# asset/timeframe of the chosen edition (rather than the one the issue form asked for), with a
# larger 5x5 robustness grid (+/-25% and +/-50% per numeric parameter, instead of the weekly
# page's 3x3), the full OOS trade list, and an OOS monthly-returns table. Reuses run_weekly's own
# `_run_variant` dispatcher and `metrics.compute_metrics` — no new simulation math, exactly like
# robustness.py's own 3x3 grid.
# ---------------------------------------------------------------------------
EXTENDED_TIME_BUDGET_SECONDS = 8 * 60  # spec: "skip 4h assets if total > 8 minutes, say so"


def _neighbour_values_5x5(name: str, value):
    """Same three cases as robustness.neighbour_values, widened to 5 points spanning +/-25% and
    +/-50% (spec's own wording) instead of a single +/-25% step either side."""
    if name == "k":
        deltas = (-0.2, -0.1, 0.0, 0.1, 0.2)
        cands = [max(round(value + d, 4), 0.1) for d in deltas]
        return sorted(set(cands))
    if isinstance(value, float) and not rb._is_wholenumber(value):
        sign = 1.0 if value >= 0 else -1.0
        mags = [abs(value) * f for f in (0.5, 0.75, 1.0, 1.25, 1.5)]
        cands = [sign * max(m, 0.001) for m in mags]
        return sorted(set(round(c, 6) for c in cands))
    v = int(round(value))
    cands = [max(int(round(v * f)), 2) for f in (0.5, 0.75, 1.0, 1.25, 1.5)]
    return sorted(set(cands))


def evaluate_variant_grid_5x5(sid: str, base_params: dict, stype: str, hold_n,
                               df: pd.DataFrame, oos_mask: pd.Series, cost: float) -> list:
    """The 5x5-widened equivalent of robustness.evaluate_variant_grid — same dedup/MA-pair-filter
    logic and the same {"params", "oos_pf", "oos_n_trades"} row shape, up to 25 grid points for a
    2-numeric-parameter strategy (5 for a 1-parameter one), diagnostic only, never touching a
    verdict."""
    numeric_names = [k for k, v in base_params.items()
                      if isinstance(v, (int, float, np.integer, np.floating))
                      and not isinstance(v, bool)]
    if not numeric_names or sid not in rb._RAW_SIGNAL:
        return []

    candidate_lists = [_neighbour_values_5x5(name, base_params[name]) for name in numeric_names]
    is_ma_pair = set(numeric_names) == {"n_fast", "n_slow"}

    results, seen = [], set()
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

        signal_out = rb._RAW_SIGNAL[sid](df, params)
        this_hold_n = params.get("n_hold", hold_n)
        trades, eq = _run_variant(stype, signal_out, this_hold_n, df, oos_mask, cost)
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


def _monthly_returns_table(equity_df: pd.DataFrame) -> list:
    """OOS equity curve -> [{"month": "YYYY-MM", "return": float}], one row per calendar month
    present, each month's return measured against the prior month's own last equity value (1.0
    for the very first month, matching equity's own reset-to-1.0-at-period-start convention)."""
    if equity_df is None or equity_df.empty:
        return []
    d = equity_df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["ym"] = d["date"].dt.strftime("%Y-%m")
    last_per_month = d.groupby("ym", sort=True)["equity"].last()
    out, prev = [], 1.0
    for ym, val in last_per_month.items():
        out.append({"month": ym, "return": float(val / prev - 1.0)})
        prev = float(val)
    return out


def run_extended_check(normalized: dict) -> dict:
    """Runs the requested strategy on EVERY (asset, timeframe) of the chosen edition (not just
    the one the issue form asked for — spec's own wording), skipping any asset/timeframe with no
    committed data file or too little history exactly like the free check's load_asset_df does,
    but per-asset (one missing file never aborts the whole extended request). 1d assets are
    processed before 4h ones; once elapsed time crosses EXTENDED_TIME_BUDGET_SECONDS, any
    remaining 4h asset is skipped with a note rather than run (spec: "skip 4h assets if total >
    8 minutes, say so") — stocks has no 4h timeframe at all, so this only ever applies to en/ko."""
    edition = normalized["edition"]
    sid, entry, params = normalized["strategy_id"], normalized["entry"], normalized["params"]
    assets = EDITION_ASSETS[edition]
    ordered_tfs = [tf for tf in ("1d", "4h") if tf in EDITION_TIMEFRAMES[edition]]

    t_start = time.monotonic()
    rows, skipped = [], []
    runtime_capped = False

    for tf in ordered_tfs:
        if tf == "4h" and (time.monotonic() - t_start) > EXTENDED_TIME_BUDGET_SECONDS:
            runtime_capped = True
            for asset in assets:
                skipped.append({"asset": asset, "timeframe": tf,
                                 "reason": "skipped — total runtime exceeded the 8-minute budget"})
            continue

        for asset in assets:
            path = _data_path(asset, tf)
            if not os.path.exists(path):
                skipped.append({"asset": asset, "timeframe": tf,
                                 "reason": "no committed data file for this asset/timeframe"})
                continue
            try:
                min_date = (config.STOCKS_DATA_START if edition == "stocks"
                            else config.DATA_START_BY_ASSET.get(asset, config.DATA_START))
                df = load_generic(path, min_date=min_date)
                if len(df) < 260:
                    skipped.append({"asset": asset, "timeframe": tf,
                                     "reason": f"only {len(df)} committed bars — not enough history"})
                    continue

                as_of, is_mask, oos_mask = rolling_is_oos_window(df, config.OOS_DAYS)
                cost = config.COST[asset]
                bars_per_year = (config.STOCKS_BARS_PER_YEAR if edition == "stocks"
                                  else config.BARS_PER_YEAR[tf])
                variant = _build_variant(sid, entry, params, normalized["params_str"])

                row, _sus = _build_variant_row(
                    sid, entry["name"], entry["type"], variant, df, is_mask, oos_mask, cost,
                    bars_per_year, as_of, asset, tf)

                oos_trades, oos_eq = _run_variant(
                    entry["type"], variant["signal_fn"](df), variant.get("hold_n"), df, oos_mask,
                    cost)
                grid5 = evaluate_variant_grid_5x5(
                    sid, params if params else {}, entry["type"], variant.get("hold_n"), df,
                    oos_mask, cost)
                monthly = _monthly_returns_table(oos_eq)

                rows.append({"asset": asset, "timeframe": tf, "row": row,
                             "oos_trades": oos_trades, "grid5": grid5,
                             "monthly_returns": monthly})
            except Exception as e:  # one asset's failure must never sink the whole extended check
                skipped.append({"asset": asset, "timeframe": tf, "reason": f"error: {e}"})

        if (time.monotonic() - t_start) > EXTENDED_TIME_BUDGET_SECONDS:
            runtime_capped = True

    return {
        "edition": edition, "strategy_id": sid, "strategy_name": entry["name"],
        "params_str": normalized["params_str"], "rows": rows, "skipped": skipped,
        "runtime_capped": runtime_capped,
    }


# ---------------------------------------------------------------------------
# Rendering — plain GitHub-flavoured markdown, no HTML needed for an issue comment.
# ---------------------------------------------------------------------------

def _fmt_pct(x, dp=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x * 100:.{dp}f}%"


def _fmt_pct_already(x, dp=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:.{dp}f}%"


def _fmt_num(x, dp=2):
    if x is None:
        return "-"
    if x == "inf":
        return "infinite"
    if isinstance(x, float) and np.isnan(x):
        return "-"
    return f"{x:.{dp}f}"


def render_error_comment(message: str) -> str:
    return (
        "## Check my strategy — could not run this request\n\n"
        f"**Problem:** {message}\n\n"
        "No backtest was run. Please open a new issue with the \"Check my strategy\" template "
        "and correct the field above.\n\n"
        "<!-- check-issue-status: invalid -->\n"
    )


def render_scorecard_comment(row: dict, normalized: dict) -> str:
    is_, oos = row["is"], row["oos"]
    robustness = row.get("robustness") or {}
    edition = normalized["edition"]
    methodology_url = config.PAGES_URL + EDITION_METHODOLOGY_PATH[edition]
    disclaimer = config.LEGAL_DISCLAIMER_KO if edition == "ko" else config.LEGAL_DISCLAIMER

    lines = []
    lines.append(f"## Check result: `{row['strategy_id']}` on {row['asset']} {row['timeframe']} "
                 f"({edition} edition)")
    lines.append("")
    lines.append(f"**Params:** `{row['params']}`  ")
    lines.append(f"**As of:** {row['as_of']}  ")
    verdict = row["verdict"] or "n/a"
    lines.append(f"**Verdict:** **{verdict}**")
    lines.append("")
    lines.append("| Period | Total return | CAGR | Profit factor | Trades | Win rate | MDD | Sharpe |")
    lines.append("|---|---|---|---|---|---|---|---|")
    lines.append(f"| In-sample | {_fmt_pct(is_.get('total_return'))} | {_fmt_pct(is_.get('cagr'))} "
                 f"| {_fmt_num(is_.get('profit_factor'))} | {is_.get('n_trades', '-')} "
                 f"| {_fmt_pct_already(is_.get('win_rate'))} | {_fmt_pct_already(is_.get('mdd'))} "
                 f"| {_fmt_num(is_.get('sharpe'))} |")
    lines.append(f"| Out-of-sample | {_fmt_pct(oos.get('total_return'))} | {_fmt_pct(oos.get('cagr'))} "
                 f"| {_fmt_num(oos.get('profit_factor'))} | {oos.get('n_trades', '-')} "
                 f"| {_fmt_pct_already(oos.get('win_rate'))} | {_fmt_pct_already(oos.get('mdd'))} "
                 f"| {_fmt_num(oos.get('sharpe'))} |")
    lines.append("")
    lines.append(f"**Fee drag (OOS):** {_fmt_pct(oos.get('fee_drag'))} — the OOS return you would "
                 f"have gotten with zero trading cost, minus the net return shown above.")
    lines.append("")
    lines.append("### Robustness map (diagnostic only)")
    if robustness.get("grid"):
        share_txt = f"{robustness['n_pass']}/{robustness['n_total']}"
        lines.append(f"{share_txt} nearby parameter settings also had OOS profit factor >= 1.0 "
                     f"(with >= 10 OOS trades). **This never changes the verdict above** — it "
                     f"only shows whether nearby settings would also have looked reasonable.")
        lines.append("")
        lines.append("| Nearby params | OOS profit factor | OOS trades |")
        lines.append("|---|---|---|")
        for g in robustness["grid"]:
            lines.append(f"| {g['params']} | {_fmt_num(g['oos_pf'])} | {g['oos_n_trades']} |")
    else:
        lines.append("This strategy has no tunable numeric parameter, so there is no "
                     "neighbourhood to map.")
    lines.append("")
    lines.append(f"Methodology, costs, and badge thresholds: {methodology_url}")
    lines.append("")
    lines.append("---")
    lines.append(disclaimer)
    lines.append("")
    lines.append("<!-- check-issue-status: valid -->")
    return "\n".join(lines) + "\n"


def _render_trade_list(trades: list) -> list:
    out = [f"<details><summary>Full OOS trade list ({len(trades)} trades)</summary>", ""]
    if trades:
        out.append("| Entry date | Entry price | Exit date | Exit price | Return |")
        out.append("|---|---|---|---|---|")
        for tr in trades:
            out.append(f"| {pd.Timestamp(tr['entry_date']).date()} | {_fmt_num(tr['entry_price'])} "
                       f"| {pd.Timestamp(tr['exit_date']).date()} | {_fmt_num(tr['exit_price'])} "
                       f"| {_fmt_pct(tr['return'])} |")
    else:
        out.append("No completed OOS trades in this period.")
    out += ["", "</details>"]
    return out


def _render_monthly_returns(monthly: list) -> list:
    out = ["<details><summary>OOS monthly returns</summary>", ""]
    if monthly:
        out.append("| Month | Return |")
        out.append("|---|---|")
        for m in monthly:
            out.append(f"| {m['month']} | {_fmt_pct(m['return'])} |")
    else:
        out.append("No OOS bars in this period.")
    out += ["", "</details>"]
    return out


def _render_grid5(grid5: list) -> list:
    out = ["**Robustness map (5x5, diagnostic only)**", ""]
    if grid5:
        def _pf_ge_1(pf):
            return pf == "inf" or (isinstance(pf, (int, float)) and pf >= 1.0)
        n_pass = sum(1 for g in grid5 if _pf_ge_1(g["oos_pf"]) and g["oos_n_trades"] >= 10)
        out.append(f"{n_pass}/{len(grid5)} nearby parameter settings (+/-25%/+/-50% per parameter) "
                   f"also had OOS profit factor >= 1.0 with >= 10 OOS trades. **This never changes "
                   f"the verdict above.**")
        out.append("")
        out.append("<details><summary>Grid detail</summary>")
        out.append("")
        out.append("| Nearby params | OOS profit factor | OOS trades |")
        out.append("|---|---|---|")
        for g in grid5:
            out.append(f"| {g['params']} | {_fmt_num(g['oos_pf'])} | {g['oos_n_trades']} |")
        out += ["", "</details>"]
    else:
        out.append("This strategy has no tunable numeric parameter, so there is no neighbourhood "
                   "to map.")
    return out


def render_extended_comment(result: dict, normalized: dict) -> str:
    """Extended (licensed) check comment — spec's own required top line, one section per
    (asset, timeframe) actually checked, a "Skipped" section for anything left out (no data, or
    the 8-minute runtime cap), and the same methodology link/disclaimer footer as the free check."""
    edition = result["edition"]
    methodology_url = config.PAGES_URL + EDITION_METHODOLOGY_PATH[edition]
    disclaimer = config.LEGAL_DISCLAIMER_KO if edition == "ko" else config.LEGAL_DISCLAIMER

    lines = ["## Extended check (licensed)", ""]
    lines.append(f"Strategy: `{result['strategy_id']}` ({result['strategy_name']}), params "
                 f"`{result['params_str']}`, **{edition}** edition — every asset and timeframe of "
                 f"this edition was checked (not just the one selected on the issue form).")
    lines.append("")
    if result["runtime_capped"]:
        lines.append("_Runtime cap reached (8 minutes) — some checks were skipped; see "
                     "\"Skipped\" below._")
        lines.append("")

    for entry in result["rows"]:
        row = entry["row"]
        is_, oos = row["is"], row["oos"]
        lines.append(f"### {entry['asset']} {entry['timeframe']}")
        lines.append("")
        verdict = row["verdict"] or "n/a"
        lines.append(f"**As of:** {row['as_of']}  ")
        lines.append(f"**Verdict:** **{verdict}**")
        lines.append("")
        lines.append("| Period | Total return | CAGR | Profit factor | Trades | Win rate | MDD | Sharpe |")
        lines.append("|---|---|---|---|---|---|---|---|")
        lines.append(f"| In-sample | {_fmt_pct(is_.get('total_return'))} | {_fmt_pct(is_.get('cagr'))} "
                     f"| {_fmt_num(is_.get('profit_factor'))} | {is_.get('n_trades', '-')} "
                     f"| {_fmt_pct_already(is_.get('win_rate'))} | {_fmt_pct_already(is_.get('mdd'))} "
                     f"| {_fmt_num(is_.get('sharpe'))} |")
        lines.append(f"| Out-of-sample | {_fmt_pct(oos.get('total_return'))} | {_fmt_pct(oos.get('cagr'))} "
                     f"| {_fmt_num(oos.get('profit_factor'))} | {oos.get('n_trades', '-')} "
                     f"| {_fmt_pct_already(oos.get('win_rate'))} | {_fmt_pct_already(oos.get('mdd'))} "
                     f"| {_fmt_num(oos.get('sharpe'))} |")
        lines.append("")
        lines.append(f"Fee drag (OOS): {_fmt_pct(oos.get('fee_drag'))}")
        lines.append("")
        lines += _render_grid5(entry["grid5"])
        lines.append("")
        lines += _render_monthly_returns(entry["monthly_returns"])
        lines.append("")
        lines += _render_trade_list(entry["oos_trades"])
        lines.append("")

    if result["skipped"]:
        lines.append("### Skipped")
        lines.append("")
        for s in result["skipped"]:
            lines.append(f"- {s['asset']} {s['timeframe']}: {s['reason']}")
        lines.append("")

    lines.append(f"Methodology, costs, and badge thresholds: {methodology_url}")
    lines.append("")
    lines.append("---")
    lines.append(disclaimer)
    lines.append("")
    lines.append("<!-- check-issue-status: valid -->")
    return "\n".join(lines) + "\n"


def build_comment(body: str) -> tuple[str, bool]:
    """Returns (comment_markdown, is_valid). Never raises — any problem, expected or not, becomes
    an error comment rather than a crash, since a workflow run must always be able to post
    *something* and close the issue.

    License handling (this task's T2): when the optional "License key" field is empty, the output
    is byte-for-byte the same free-check comment this function has always produced — nothing below
    even looks at Gumroad. When a key IS present, it is verified (never logged) and either an
    extended check is run (verified) or the ordinary free check runs with one extra explanatory
    line appended (not verified, for any reason, including the license feature simply not being
    enabled yet)."""
    try:
        fields = parse_issue_body(body)
        normalized = validate(fields)
    except ValidationError as e:
        return render_error_comment(str(e)), False
    except Exception as e:  # pragma: no cover - defensive: never crash the workflow either way
        return render_error_comment(f"unexpected error while checking this strategy: {e}"), False

    license_key = (fields.get("license") or "").strip()
    verified, reason = False, ""
    if license_key:
        try:
            verified, reason = verify_gumroad_license(license_key)
        except Exception:  # a Gumroad-side surprise must fall back, never crash the workflow
            verified, reason = False, "license key could not be verified"

    try:
        if verified:
            result = run_extended_check(normalized)
            return render_extended_comment(result, normalized), True
        row = run_check(normalized)
        comment_md = render_scorecard_comment(row, normalized)
        if license_key:
            comment_md = comment_md.rstrip("\n") + "\n\n" + _license_note_line(reason) + "\n"
        return comment_md, True
    except ValidationError as e:
        return render_error_comment(str(e)), False
    except Exception as e:  # pragma: no cover - defensive: never crash the workflow either way
        return render_error_comment(f"unexpected error while checking this strategy: {e}"), False


# ---------------------------------------------------------------------------
# GitHub REST API posting (--post only). Uses `requests` directly (already a project dependency)
# rather than the `gh` CLI, so this path is exercised by ordinary Python — never invoked by tests
# or by --dry-run.
# ---------------------------------------------------------------------------

def post_to_github(repo: str, issue_number: int, token: str, comment_md: str, is_valid: bool):
    import requests
    api = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    requests.post(f"{api}/comments", headers=headers, json={"body": comment_md}, timeout=30)
    labels = ["done"] if is_valid else []
    requests.patch(api, headers=headers, json={"state": "closed", "labels": labels}, timeout=30)


def main():
    parser = argparse.ArgumentParser(description="spec_v3 §E: check-my-strategy issue handler")
    parser.add_argument("--body", required=True, help="path to a file containing the issue body")
    parser.add_argument("--dry-run", action="store_true",
                         help="print the comment markdown to stdout; never touch GitHub")
    parser.add_argument("--post", action="store_true",
                         help="also post the comment and close the issue via the GitHub REST API")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                         help="owner/repo, required with --post")
    parser.add_argument("--issue-number", type=int, default=None,
                         help="issue number to comment on/close, required with --post")
    args = parser.parse_args()

    with open(args.body, "r", encoding="utf-8") as f:
        body = f.read()

    comment_md, is_valid = build_comment(body)
    print(comment_md, end="")

    if args.post:
        token = os.environ.get("GITHUB_TOKEN")
        if not token or not args.repo or args.issue_number is None:
            print("--post requires GITHUB_TOKEN env var, --repo, and --issue-number",
                  file=sys.stderr)
            return 1
        post_to_github(args.repo, args.issue_number, token, comment_md, is_valid)

    return 0


if __name__ == "__main__":
    sys.exit(main())
