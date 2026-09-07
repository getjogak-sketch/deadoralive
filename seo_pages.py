"""
seo_pages.py — programmatic SEO pages, sitemap, robots.txt (this task's requirement S1).

Purely additive on top of the existing weekly pipeline: reads the same payload dicts
run_weekly.py already assembled for each edition (en/ko/stocks) and writes one static page per
(strategy variant, asset) under docs/s/, docs/ko/s/, docs/stocks/s/ — plus a sitemap.xml,
robots.txt, and an "all strategy pages" index per edition. It changes no existing file's numbers;
every figure shown on a page here is read verbatim from the row dict run_weekly.py already
computed (registry.py / engine.py / metrics.py / verdict.py / robustness.py are untouched).

Reference rows (buy_and_hold, dca_weekly) are never paged here — spec_v2 §4 already excludes them
from having a verdict, and this task's own wording ("strategy variant") matches that.
"""
from __future__ import annotations
import html
import json
import os
import re

import config
import registry as reg
import build_site as bs
import charts

# ---------------------------------------------------------------------------
# Per-edition output roots and URL bases (config.PAGES_URL is the one placeholder GitHub Pages
# base already used by the spec_v3 §B API docs — reused here rather than inventing a second one).
# ---------------------------------------------------------------------------
EDITION_OUT_DIR = {
    "en": config.DOCS_DIR,
    "ko": config.DOCS_DIR_KO,
    "stocks": config.DOCS_DIR_STOCKS,
    "macro": config.DOCS_DIR_MACRO,
}
EDITION_URL_PATH = {
    "en": "",
    "ko": "/ko",
    "stocks": "/stocks",
    "macro": "/macro",
}

ASSET_LABEL_EN = {
    "BTCUSD": "BTC", "ETHUSD": "ETH", "SPY": "SPY", "QQQ": "QQQ",
    "KRW-BTC": "BTC (KRW)", "KRW-ETH": "ETH (KRW)",
    "GLD": "GLD", "SLV": "SLV", "USO": "USO",
    "EURUSD": "EUR/USD", "USDJPY": "USD/JPY",
}
TF_LABEL_EN = {"1d": "daily (1d)", "4h": "4-hour (4h)"}

# Crypto assets that name "the same coin" across the Bitstamp (en) and Upbit (ko) editions —
# used only to decide which pages get an hreflang alternate link to each other (spec S1: "between
# en/ko pages of the same strategy where both exist"). Stocks assets have no ko counterpart at all
# (spec_v3 §A: "Not part of the Korean edition").
_CRYPTO_STD = {"BTCUSD": "BTC", "KRW-BTC": "BTC", "ETHUSD": "ETH", "KRW-ETH": "ETH"}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "x"


def strategy_page_filename(strategy_id: str, params: str, asset: str) -> str:
    return f"{_slug(strategy_id)}-{_slug(params)}-{_slug(asset)}.html"


def _registry_entries_by_id() -> dict:
    out = {}
    for e in reg.REGISTRY + reg.POPULAR_COMBOS:
        out[e["id"]] = e
    return out


_ENTRIES = _registry_entries_by_id()

# ---------------------------------------------------------------------------
# Human-friendly "(params)" display strings — one entry per (strategy_id, params_str) that
# actually occurs in registry.py/REGISTRY+POPULAR_COMBOS (spot-checked against both tables above).
# An unmapped combination (should not occur; defensive only) falls back to the raw params string.
# ---------------------------------------------------------------------------
PARAMS_DISPLAY_EN = {
    ("sma_cross", "10-50"): "(10, 50)", ("sma_cross", "20-100"): "(20, 100)",
    ("sma_cross", "50-200"): "(50, 200)",
    ("ema_cross", "12-26"): "(12, 26)",
    ("above_sma", "n200"): "(200)", ("above_sma", "n50"): "(50)",
    ("donchian", "20-10"): "(20, 10)", ("donchian", "55-20"): "(55, 20)",
    ("vol_breakout", "k0.5"): "(k=0.5)", ("vol_breakout", "k0.7"): "(k=0.7)",
    ("vol_breakout_trend", "k0.5-sma20"): "(k=0.5, SMA20 filter)",
    ("rsi_mr", "exit50"): "(exit 50)", ("rsi_mr", "exit70"): "(exit 70)",
    ("rsi2_connors", "fixed"): "",
    ("bb_mr", "20-2"): "(20, 2)",
    ("bb_breakout", "20-2"): "(20, 2)",
    ("macd", "12-26-9"): "(12, 26, 9)",
    ("supertrend", "10-3"): "(10, 3)",
    ("tsmom", "n30"): "(30)", ("tsmom", "n90"): "(90)",
    ("dip_3down", "fixed"): "",
    ("dip_pct", "-5pct-5bar"): "(-5%, 5-bar hold)",
    ("ema_9_21", "9-21"): "(9, 21)",
    ("ema200_macd", "ema200-macd12.26.9"): "(EMA200 + MACD 12,26,9)",
    ("rsi_uptrend", "rsi14-sma200"): "(RSI14, SMA200)",
    ("bb_squeeze", "bb20.2-look120-conf5"): "(BB 20,2; 120-bar lookback; 5-bar confirm)",
    ("ichimoku_cloud", "9-26-52"): "(9, 26, 52)",
    ("heikin_ashi_trend", "conf2"): "(2-bar confirm)",
    ("supertrend_ema200", "st10.3-ema200"): "(10, 3; EMA200 filter)",
}
PARAMS_DISPLAY_KO = {
    ("sma_cross", "10-50"): "(10, 50)", ("sma_cross", "20-100"): "(20, 100)",
    ("sma_cross", "50-200"): "(50, 200)",
    ("ema_cross", "12-26"): "(12, 26)",
    ("above_sma", "n200"): "(200)", ("above_sma", "n50"): "(50)",
    ("donchian", "20-10"): "(20, 10)", ("donchian", "55-20"): "(55, 20)",
    ("vol_breakout", "k0.5"): "(k=0.5)", ("vol_breakout", "k0.7"): "(k=0.7)",
    ("vol_breakout_trend", "k0.5-sma20"): "(k=0.5, 20봉 이동평균 필터)",
    ("rsi_mr", "exit50"): "(청산 50)", ("rsi_mr", "exit70"): "(청산 70)",
    ("rsi2_connors", "fixed"): "",
    ("bb_mr", "20-2"): "(20, 2)",
    ("bb_breakout", "20-2"): "(20, 2)",
    ("macd", "12-26-9"): "(12, 26, 9)",
    ("supertrend", "10-3"): "(10, 3)",
    ("tsmom", "n30"): "(30)", ("tsmom", "n90"): "(90)",
    ("dip_3down", "fixed"): "",
    ("dip_pct", "-5pct-5bar"): "(-5%, 5봉 보유)",
    ("ema_9_21", "9-21"): "(9, 21)",
    ("ema200_macd", "ema200-macd12.26.9"): "(EMA200 + MACD 12,26,9)",
    ("rsi_uptrend", "rsi14-sma200"): "(RSI14, SMA200)",
    ("bb_squeeze", "bb20.2-look120-conf5"): "(볼린저 20,2; 120봉 기준; 5봉 확인)",
    ("ichimoku_cloud", "9-26-52"): "(9, 26, 52)",
    ("heikin_ashi_trend", "conf2"): "(2봉 확인)",
    ("supertrend_ema200", "st10.3-ema200"): "(10, 3; EMA200 필터)",
}


def _params_display(strategy_id: str, params: str, lang: str) -> str:
    table = PARAMS_DISPLAY_KO if lang == "ko" else PARAMS_DISPLAY_EN
    return table.get((strategy_id, params), f"({params})" if params not in ("fixed", "-") else "")


# ---------------------------------------------------------------------------
# SEO title lead phrases (M1's macro-asset examples + M2's demand-study tuning). research/demand's
# keyword pool (research/demand/keywords.yml, read-only from this module) already contains, in the
# exact search-phrase spelling used below: "qqq strategy" (stocks_etf group), "bollinger bands
# strategy" (anchor_crypto group), "ichimoku strategy" (indicator_combos group), and
# "gold trading strategy" / "eurusd strategy" (forex_commodities group, matched here as
# "EUR/USD trading strategy" per M1's own page-copy instruction). Where a page's asset or strategy
# id matches, its <title>/<h1> leads with that phrase instead of opening with the bare strategy
# name — e.g. "QQQ strategy: SMA crossover (50, 200) on QQQ — does it still work in 2026? ..."
# instead of "SMA crossover (50, 200) on QQQ — does it still work in 2026? ...". Everything else
# about the title (the "on <asset> — does it still work in <year>? ..." tail) is unchanged.
#
# Deliberately conservative: only phrases this repo has actual keyword-pool evidence for are
# mapped here (see research/demand/results/RESULTS.md, produced by a separate study, once it has
# run) — "grid bot" / "pionex" (bot_templates) are not strategies this site tests yet and are
# intentionally absent, per this task's own instruction to skip them.
# ---------------------------------------------------------------------------
ASSET_LEAD_EN = {
    "QQQ": "QQQ strategy",
    "GLD": "Gold trading strategy",
    "EURUSD": "EUR/USD trading strategy",
}
STRATEGY_LEAD_EN = {
    "bb_mr": "Bollinger Bands strategy",
    "bb_breakout": "Bollinger Bands strategy",
    "bb_squeeze": "Bollinger Bands strategy",
    "ichimoku_cloud": "Ichimoku strategy",
}


def _seo_lead_phrase(strategy_id: str, asset: str) -> str | None:
    """The high-demand lead phrase for this (strategy, asset) SEO page, or None if neither matches
    one of the mapped phrases above. An asset-level match (e.g. every GLD page, whichever strategy)
    takes precedence over a strategy-level one (e.g. every bb_* page, whichever asset) when both
    would apply, since "people search for this asset" is at least as strong a signal as "people
    search for this strategy" and the two would otherwise collide on, say, a hypothetical future
    bb_* variant on QQQ."""
    return ASSET_LEAD_EN.get(asset) or STRATEGY_LEAD_EN.get(strategy_id)


def _ko_short_name(strategy_id: str) -> str:
    full = bs.STRATEGY_NAME_KO.get(strategy_id, strategy_id)
    return full.split(" (")[0]


def _ko_asset_short(asset: str) -> str:
    label = bs.ASSET_LABEL_KO.get(asset, asset)
    if "(" in label:
        return label.split("(", 1)[1].rstrip(")")
    return label


# ---------------------------------------------------------------------------
# Grouping: one page per (strategy_id, params, asset), covering every timeframe present.
# ---------------------------------------------------------------------------

def _all_variant_rows(payload: dict) -> list:
    return [r for r in ((payload.get("rows") or []) + (payload.get("popular_combos") or []))
            if r.get("type") != "reference"]


def _group_by_strategy_asset(payload: dict) -> tuple[list, dict]:
    order = []
    groups = {}
    for r in _all_variant_rows(payload):
        key = (r["strategy_id"], r["params"], r["asset"])
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key][r["timeframe"]] = r
    return order, groups


# ---------------------------------------------------------------------------
# History (verdict per as_of) — read straight from results/history/*.json, the same files
# run_weekly.py already writes every run. Read-only: this module writes nothing there.
# ---------------------------------------------------------------------------
_HIST_FILE_RE = {
    "en": re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$"),
    "ko": re.compile(r"^ko_(\d{4}-\d{2}-\d{2})\.json$"),
    "stocks": re.compile(r"^stocks_(\d{4}-\d{2}-\d{2})\.json$"),
    "macro": re.compile(r"^macro_(\d{4}-\d{2}-\d{2})\.json$"),
}


def _history_index(edition_key: str) -> dict:
    """(strategy_id, params, asset, timeframe) -> {as_of: verdict}, across every history file
    found for this edition. Recomputed fresh each call (cheap: a handful of small JSON files for
    a project this size) rather than cached across a whole run, since seo_pages.build_all() is
    invoked at most once per weekly run."""
    pat = _HIST_FILE_RE[edition_key]
    out: dict = {}
    if not os.path.isdir(config.HISTORY_DIR):
        return out
    for fn in sorted(os.listdir(config.HISTORY_DIR)):
        m = pat.match(fn)
        if not m:
            continue
        as_of = m.group(1)
        try:
            with open(os.path.join(config.HISTORY_DIR, fn), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for r in _all_variant_rows(snap):
            k = (r["strategy_id"], r["params"], r["asset"], r["timeframe"])
            out.setdefault(k, {})[as_of] = r.get("verdict")
    return out


def _history_line(hist_map: dict, strategy_id, params, asset, timeframe, lang: str) -> str:
    key = (strategy_id, params, asset, timeframe)
    entries = sorted((hist_map.get(key) or {}).items())
    if len(entries) <= 1:
        return ("First recorded verdict this week." if lang != "ko"
                else "이번 주가 이 조합의 첫 기록입니다.")
    parts = [f"{as_of}: {v}" for as_of, v in entries]
    prefix = "History" if lang != "ko" else "판정 이력"
    return f"{prefix} ({timeframe}): " + " &rarr; ".join(html.escape(p) for p in parts)


def _history_pf_index(edition_key: str) -> dict:
    """task R2: same walk as _history_index above, but capturing each as_of's OOS profit factor
    instead of its verdict — used only for the sparkline, never for anything that affects a
    verdict. A separate pass (rather than folding into _history_index) so that function's existing
    {as_of: verdict} value shape — already relied on by _history_line above — is untouched."""
    pat = _HIST_FILE_RE[edition_key]
    out: dict = {}
    if not os.path.isdir(config.HISTORY_DIR):
        return out
    for fn in sorted(os.listdir(config.HISTORY_DIR)):
        m = pat.match(fn)
        if not m:
            continue
        as_of = m.group(1)
        try:
            with open(os.path.join(config.HISTORY_DIR, fn), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for r in _all_variant_rows(snap):
            k = (r["strategy_id"], r["params"], r["asset"], r["timeframe"])
            pf = (r.get("oos") or {}).get("profit_factor")
            pf = pf if isinstance(pf, (int, float)) else None
            out.setdefault(k, {})[as_of] = pf
    return out


def _pf_sparkline_html(hist_pf_map: dict, strategy_id, params, asset, timeframe, lang: str) -> str:
    """task R2: a tiny inline-SVG sparkline of OOS profit factor over time, shown only once at
    least 3 history points exist (fewer than that isn't a trend, it's noise) — matches the task's
    own threshold. Never rendered at all below that, same "say nothing rather than something
    misleading" convention _history_line already follows for a first-week strategy."""
    key = (strategy_id, params, asset, timeframe)
    entries = sorted((hist_pf_map.get(key) or {}).items())
    if len(entries) < 3:
        return ""
    values = [v for _as_of, v in entries]
    svg = charts.sparkline_svg(values)
    if not svg:
        return ""
    label = "OOS PF trend" if lang != "ko" else "표본외 순손익비 추이"
    latest = values[-1]
    latest_str = bs._fmt_num(latest) if latest is not None else "n/a"
    return (f'<span class="sparkline" title="{html.escape(", ".join(f"{a}: {bs._fmt_num(v)}" for a, v in entries))}">'
            f'{label} {svg} ({latest_str})</span>')


# ---------------------------------------------------------------------------
# Small HTML fragments reused by every page.
# ---------------------------------------------------------------------------
EXTRA_CSS = """
.stratpage main { max-width: 860px; }
.scorecard { border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.25rem; font-size: 0.88rem; }
.scorecard th, .scorecard td { border: 1px solid var(--border); padding: 0.4rem 0.6rem; text-align: right; }
.scorecard th:first-child, .scorecard td:first-child { text-align: left; }
.scorecard thead th { background: var(--card-bg); }
.griddump { font-size: 0.8rem; color: var(--muted); }
.interp { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.1rem; margin: 1rem 0; }
.histline { font-size: 0.82rem; color: var(--muted); }
.tfblock { margin: 1.5rem 0; }
.sparkline { display: inline-flex; align-items: center; gap: 0.3rem; margin-left: 0.5rem; }
.sparkline svg { vertical-align: middle; }
"""


def _scorecard_html(row: dict, lang: str) -> str:
    is_, oos = row["is"], row["oos"]
    if lang == "ko":
        head = ("<tr><th>지표</th><th>표본내(IS)</th><th>표본외(OOS)</th></tr>")
        labels = [("total_return", "누적 수익률", "pct"), ("cagr", "연복리수익률(CAGR)", "pct"),
                  ("profit_factor", "순손익비(PF)", "num"), ("sharpe", "샤프비율", "num"),
                  ("mdd", "최대낙폭(MDD)", "pct_already"), ("n_trades", "거래 횟수", "int"),
                  ("win_rate", "승률", "pct_already"), ("avg_trade_return", "거래당 평균 수익률", "pct"),
                  ("exposure", "보유 비중", "pct_already")]
    else:
        head = ("<tr><th>Metric</th><th>In-sample (IS)</th><th>Out-of-sample (OOS)</th></tr>")
        labels = [("total_return", "Total return", "pct"), ("cagr", "CAGR", "pct"),
                  ("profit_factor", "Profit factor", "num"), ("sharpe", "Sharpe", "num"),
                  ("mdd", "Max drawdown", "pct_already"), ("n_trades", "Trades", "int"),
                  ("win_rate", "Win rate", "pct_already"), ("avg_trade_return", "Avg trade return", "pct"),
                  ("exposure", "Time in market", "pct_already")]

    def fmt(v, kind):
        if kind == "pct":
            return bs._fmt_pct(v)
        if kind == "pct_already":
            return bs._fmt_pct_already(v)
        if kind == "int":
            return bs._fmt_int(v)
        return bs._fmt_num(v)

    rows_html = "".join(
        f"<tr><td>{html.escape(lbl)}</td><td>{fmt(is_.get(key), kind)}</td>"
        f"<td>{fmt(oos.get(key), kind)}</td></tr>"
        for key, lbl, kind in labels
    )
    bh_label = "Buy &amp; hold (OOS)" if lang != "ko" else "단순 보유 (표본외)"
    fee_label = "Fee drag (OOS)" if lang != "ko" else "수수료로 사라진 수익 (표본외)"
    if lang == "ko":
        bh_text = f"수익률 {bs._fmt_pct(oos.get('bh_return'))}, 최대낙폭 {bs._fmt_pct_already(oos.get('bh_mdd'))}"
    else:
        bh_text = f"{bs._fmt_pct(oos.get('bh_return'))} return, {bs._fmt_pct_already(oos.get('bh_mdd'))} MDD"
    extra = (
        f"<tr><td>{bh_label}</td><td colspan='2'>{bh_text}</td></tr>"
        f"<tr><td>{fee_label}</td><td colspan='2'>{bs._fmt_pct(oos.get('fee_drag'))}</td></tr>"
    )
    return f"<table class='scorecard'><thead>{head}</thead><tbody>{rows_html}{extra}</tbody></table>"


def _robustness_html(row: dict, lang: str) -> str:
    rb = row.get("robustness")
    if not rb or not rb.get("grid"):
        return ""
    title = "Robustness (nearby parameter grid, diagnostic only)" if lang != "ko" else "주변 설정값 안정성 (참고용)"
    note = ("This grid never changes the verdict above and never selects parameters — it only "
            "shows whether nearby, never-registered parameter values would also have cleared "
            "OOS PF &ge; 1.0 with &ge; 10 trades.") if lang != "ko" else (
        "이 표는 위 판정을 바꾸지 않으며 파라미터를 새로 고르지도 않습니다 — 등록되지 않은 주변 값들도 "
        "표본외 순손익비 1.0 이상, 거래 10회 이상을 만족했는지만 참고용으로 보여줍니다.")
    badge = bs._robustness_badge_html(rb)
    lines = "".join(
        f"<li>{html.escape(g['params'])} &rarr; PF={bs._fmt_grid_pf(g['oos_pf'])} "
        f"(n={g['oos_n_trades']})</li>"
        for g in rb["grid"]
    )
    share_label = "cleared" if lang != "ko" else "통과"
    return (f"<h3>{title}</h3><p>{badge} {rb['n_pass']}/{rb['n_total']} {share_label} "
            f"&mdash; {note}</p><ul class='griddump'>{lines}</ul>")


def _interpretation_en(strategy_name, params_disp, asset_label, as_of, oos_days, tf_rows) -> str:
    sentences = []
    tf_order = [tf for tf in ("1d", "4h") if tf in tf_rows]
    for tf in tf_order:
        r = tf_rows[tf]
        oos = r["oos"]
        sentences.append(
            f"At {TF_LABEL_EN.get(tf, tf)}, the out-of-sample verdict as of {as_of} is "
            f"{r['verdict']}: profit factor {bs._fmt_num(oos.get('profit_factor'))} across "
            f"{bs._fmt_int(oos.get('n_trades'))} trades, total return "
            f"{bs._fmt_pct(oos.get('total_return'))}, max drawdown "
            f"{bs._fmt_pct_already(oos.get('mdd'))}, versus buy-and-hold's "
            f"{bs._fmt_pct(oos.get('bh_return'))} return and "
            f"{bs._fmt_pct_already(oos.get('bh_mdd'))} drawdown over the same window."
        )
    primary = tf_rows.get(tf_order[0]) if tf_order else None
    if primary:
        oos = primary["oos"]
        is_ = primary["is"]
        sentences.append(
            f"Trading cost reduced the {TF_LABEL_EN.get(tf_order[0], tf_order[0])} out-of-sample "
            f"return by {bs._fmt_pct(oos.get('fee_drag'))} compared with the same signal at zero "
            f"cost."
        )
        sentences.append(
            f"Over the longer in-sample history before that {oos_days}-day out-of-sample window, "
            f"the same rule's profit factor was {bs._fmt_num(is_.get('profit_factor'))} with a "
            f"maximum drawdown of {bs._fmt_pct_already(is_.get('mdd'))}."
        )
    return " ".join(sentences)


def _interpretation_ko(strategy_name_ko, params_disp, asset_label_ko, as_of, oos_days, tf_rows) -> str:
    sentences = []
    tf_order = [tf for tf in ("1d", "4h") if tf in tf_rows]
    tf_label_ko = {"1d": "일봉(1d)", "4h": "4시간봉(4h)"}
    verdict_ko = bs.VERDICT_LABELS_KO
    for tf in tf_order:
        r = tf_rows[tf]
        oos = r["oos"]
        sentences.append(
            f"{tf_label_ko.get(tf, tf)} 기준, {as_of} 표본외 판정은 "
            f"{verdict_ko.get(r['verdict'], r['verdict'])}입니다: 순손익비 "
            f"{bs._fmt_num(oos.get('profit_factor'))}, 거래 {bs._fmt_int(oos.get('n_trades'))}회, "
            f"누적 수익률 {bs._fmt_pct(oos.get('total_return'))}, 최대낙폭 "
            f"{bs._fmt_pct_already(oos.get('mdd'))}이며, 같은 구간 단순 보유는 수익률 "
            f"{bs._fmt_pct(oos.get('bh_return'))}, 최대낙폭 {bs._fmt_pct_already(oos.get('bh_mdd'))}"
            f"이었습니다."
        )
    primary = tf_rows.get(tf_order[0]) if tf_order else None
    if primary:
        oos = primary["oos"]
        is_ = primary["is"]
        sentences.append(
            f"수수료를 반영하면 {tf_label_ko.get(tf_order[0], tf_order[0])} 표본외 수익률이 무비용 "
            f"기준보다 {bs._fmt_pct(oos.get('fee_drag'))} 낮아졌습니다."
        )
        sentences.append(
            f"그 {oos_days}일 표본외 구간 이전의 더 긴 표본내(IS) 구간에서는 같은 규칙의 순손익비가 "
            f"{bs._fmt_num(is_.get('profit_factor'))}, 최대낙폭이 {bs._fmt_pct_already(is_.get('mdd'))}"
            f"였습니다."
        )
    return " ".join(sentences)


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def _page_head(title, description, canonical, alt_links, lang):
    alt_html = "".join(
        f'<link rel="alternate" hreflang="{hl}" href="{html.escape(href)}">'
        for hl, href in alt_links
    )
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{html.escape(canonical)}">
{alt_html}
<style>{bs.BASE_CSS}{EXTRA_CSS}</style>
</head>
"""


def _build_one_page(edition_key: str, lang: str, strategy_id: str, params: str, asset: str,
                     tf_rows: dict, payload: dict, hist_map: dict, alt_edition_url: str | None,
                     hist_pf_map: dict | None = None):
    entry = _ENTRIES.get(strategy_id, {})
    rule_en = entry.get("rule", "")
    out_dir = os.path.join(EDITION_OUT_DIR[edition_key], "s")
    filename = strategy_page_filename(strategy_id, params, asset)
    url_path = f"{EDITION_URL_PATH[edition_key]}/s/{filename}"
    canonical = config.PAGES_URL.rstrip("/") + url_path
    as_of = payload["as_of"]
    year = as_of[:4]
    oos_days = payload.get("oos_days", config.OOS_DAYS)

    tf_order = [tf for tf in ("1d", "4h") if tf in tf_rows]
    primary = tf_rows[tf_order[0]]

    if lang == "ko":
        strategy_name_disp = _ko_short_name(strategy_id)
        params_disp = _params_display(strategy_id, params, "ko")
        asset_label = _ko_asset_short(asset)
        rule_text = bs.RULE_KO.get(strategy_id, rule_en)
        title = (f"{strategy_name_disp}{params_disp} {asset_label} 전략, {year}년에도 통할까? — "
                 f"수수료 뗀 백테스트, 매주 갱신")
        description = (
            f"{strategy_name_disp}{params_disp} {asset_label} 전략은 {as_of} 기준 "
            f"{TF_LABEL_EN.get(tf_order[0], tf_order[0])} 표본외 구간에서 "
            f"{bs.VERDICT_LABELS_KO.get(primary['verdict'], primary['verdict'])} 판정입니다: "
            f"순손익비 {bs._fmt_num(primary['oos'].get('profit_factor'))}, 수익률 "
            f"{bs._fmt_pct(primary['oos'].get('total_return'))}, 최대낙폭 "
            f"{bs._fmt_pct_already(primary['oos'].get('mdd'))}."
        )
        interp = _interpretation_ko(strategy_name_disp, params_disp, asset_label, as_of, oos_days,
                                     tf_rows)
        back_index = "../index.html"
        back_meth = "../methodology.html"
        back_registry = "../registry.html"
        s_index = "index.html"
        check_link = bs._check_strategy_url()
        signup_html = ""
        if payload.get("signup_url"):
            signup_html = f' &middot; <a href="{html.escape(payload["signup_url"])}">매주 결과 받아보기</a>'
        disclaimer_top = bs._disclaimer_block_ko()
        disclaimer_bottom = bs._disclaimer_block_ko()
        rule_label = "규칙"
        verdict_label = "판정"
        tf_label_map = {"1d": "일봉(1d)", "4h": "4시간봉(4h)"}
        back_link_text = "전체 표로 돌아가기"
        meth_link_text = "방법론"
        check_link_text = "내 전략도 검사해 보기"
        interp_title = "숫자로만 정리하면"
    else:
        strategy_name_disp = entry.get("name", strategy_id)
        params_disp = _params_display(strategy_id, params, "en")
        asset_label = ASSET_LABEL_EN.get(asset, asset)
        rule_text = rule_en
        edition_word = " (stocks)" if edition_key == "stocks" else ""
        title = (f"{strategy_name_disp} {params_disp} on {asset_label}{edition_word} — does it "
                 f"still work in {year}? After-fees backtest, updated weekly").replace("  ", " ")
        lead = _seo_lead_phrase(strategy_id, asset)
        if lead:
            title = f"{lead}: {title}"
        description = (
            f"{strategy_name_disp} {params_disp} on {asset_label} is currently rated "
            f"{primary['verdict']} on the {TF_LABEL_EN.get(tf_order[0], tf_order[0])} "
            f"out-of-sample window (as of {as_of}): OOS profit factor "
            f"{bs._fmt_num(primary['oos'].get('profit_factor'))}, return "
            f"{bs._fmt_pct(primary['oos'].get('total_return'))}, max drawdown "
            f"{bs._fmt_pct_already(primary['oos'].get('mdd'))}."
        ).replace("  ", " ")
        interp = _interpretation_en(strategy_name_disp, params_disp, asset_label, as_of, oos_days,
                                     tf_rows)
        back_index = "../index.html"
        back_meth = "../methodology.html"
        back_registry = ("../../registry.html" if edition_key in ("stocks", "macro")
                         else "../registry.html")
        s_index = "index.html"
        check_link = bs._check_strategy_url()
        signup_html = ""
        if payload.get("signup_url"):
            signup_html = f' &middot; <a href="{html.escape(payload["signup_url"])}">Get weekly updates</a>'
        disclaimer_top = f'<div class="disclaimer-block">{html.escape(config.LEGAL_DISCLAIMER)}</div>'
        disclaimer_bottom = disclaimer_top
        rule_label = "Rule"
        verdict_label = "Verdict"
        tf_label_map = TF_LABEL_EN
        back_link_text = "Back to the full table"
        meth_link_text = "Methodology"
        check_link_text = "Check your own strategy"
        interp_title = "What the numbers say (no opinions, no advice)"

    alt_links = [("x-default", canonical), ("en" if lang != "ko" else "ko", canonical)]
    if alt_edition_url:
        alt_links.append(("ko" if lang != "ko" else "en", alt_edition_url))

    tf_sections = []
    for tf in tf_order:
        r = tf_rows[tf]
        badge = bs._badge_html_ko(r["verdict"]) if lang == "ko" else bs._badge_html(r["verdict"])
        hist = _history_line(hist_map, strategy_id, params, asset, tf, lang)
        sparkline = _pf_sparkline_html(hist_pf_map or {}, strategy_id, params, asset, tf, lang)
        tf_sections.append(f"""
<div class="tfblock">
  <h2>{html.escape(tf_label_map.get(tf, tf))} &mdash; {verdict_label}: {badge}</h2>
  {_scorecard_html(r, lang)}
  {_robustness_html(r, lang)}
  <p class="histline">{hist} {sparkline}</p>
</div>""")

    doc = _page_head(title, description, canonical, alt_links, lang) + f"""
<body class="stratpage">
<header class="top">
  <h1>{html.escape(title)}</h1>
  <p class="meta">as_of: <strong>{html.escape(as_of)}</strong> &middot;
     <a href="{back_index}">{back_link_text}</a></p>
</header>
<main>
  <p><strong>{rule_label}:</strong> {html.escape(rule_text)}</p>
  {disclaimer_top}
  {"".join(tf_sections)}
  <div class="interp">
    <h3>{interp_title}</h3>
    <p>{interp}</p>
  </div>
  <p>
    <a href="{back_index}">{back_link_text}</a> &middot;
    <a href="{back_meth}">{meth_link_text}</a> &middot;
    <a href="{back_registry}">{'전략 등록부' if lang == 'ko' else 'Strategy registry'}</a> &middot;
    <a href="{html.escape(check_link)}">{check_link_text}</a>{signup_html} &middot;
    <a href="{s_index}">{'전체 전략 페이지' if lang == 'ko' else 'All strategy pages'}</a>
  </p>
  {disclaimer_bottom}
</main>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
        f.write(doc)
    return {
        "filename": filename, "url": canonical, "title": title,
        "strategy_id": strategy_id, "strategy_name": strategy_name_disp,
        "as_of": as_of,
    }


def _s_index_page(edition_key: str, lang: str, entries: list, payload: dict | None):
    out_dir = os.path.join(EDITION_OUT_DIR[edition_key], "s")
    os.makedirs(out_dir, exist_ok=True)
    by_strategy: dict = {}
    order = []
    for e in entries:
        if e["strategy_id"] not in by_strategy:
            by_strategy[e["strategy_id"]] = []
            order.append(e["strategy_id"])
        by_strategy[e["strategy_id"]].append(e)

    if lang == "ko":
        title = f"{config.PROJECT_NAME} — 전략별 페이지 전체 목록"
        empty_note = "이번 주 이 에디션에는 표시할 데이터가 없습니다."
        heading = "전략별 페이지"
        back = '<a href="../index.html">전체 표로 돌아가기</a>'
    else:
        edition_word = {"en": "", "stocks": " (stocks)"}.get(edition_key, "")
        title = f"{config.PROJECT_NAME}{edition_word} — every strategy page"
        empty_note = "No data for this edition this week."
        heading = "All strategy pages"
        back = '<a href="../index.html">Back to the full table</a>'

    if not order:
        body = f"<p>{empty_note}</p>"
    else:
        groups_html = []
        for sid in order:
            name = _ENTRIES.get(sid, {}).get("name", sid)
            name = bs.STRATEGY_NAME_KO.get(sid, name) if lang == "ko" else name
            links = "".join(
                f'<li><a href="{html.escape(e["filename"])}">{html.escape(e["title"])}</a></li>'
                for e in by_strategy[sid]
            )
            groups_html.append(f"<h2>{html.escape(name)}</h2><ul>{links}</ul>")
        body = "".join(groups_html)

    doc = f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{bs.BASE_CSS}{EXTRA_CSS}</style>
</head>
<body class="stratpage">
<header class="top"><h1>{html.escape(heading)}</h1><p class="meta">{back}</p></header>
<main>{body}</main>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)


def build_all(payloads: dict) -> dict:
    """payloads: {"en": payload_or_None, "ko": payload_or_None, "stocks": payload_or_None}.
    Builds every edition's /s/ pages (with cross-linked en<->ko hreflang where the same crypto
    strategy+params+coin exists on both sides), each edition's /s/index.html, and returns a
    manifest {edition: [page dicts]} used by build_sitemap() and by the report at the end of this
    task. Editions with no data this week (payload is None or has zero rows) get an empty
    /s/index.html noting that, matching the rest of this pipeline's "skip, don't fail" convention.
    """
    # Pass 1: gather groups per edition.
    per_edition_groups = {}
    hist_maps = {}
    hist_pf_maps = {}
    for edition_key, payload in payloads.items():
        order, groups = ([], {})
        if payload and _all_variant_rows(payload):
            order, groups = _group_by_strategy_asset(payload)
        per_edition_groups[edition_key] = (order, groups)
        hist_maps[edition_key] = _history_index(edition_key)
        hist_pf_maps[edition_key] = _history_pf_index(edition_key)

    # Pass 2: build a std-key index so en/ko pages of "the same coin, same strategy+params" can
    # link to each other via hreflang (spec S1).
    std_index: dict = {}
    for edition_key in ("en", "ko"):
        if edition_key not in per_edition_groups:
            continue
        order, groups = per_edition_groups[edition_key]
        for (sid, params, asset) in order:
            std_asset = _CRYPTO_STD.get(asset)
            if std_asset is None:
                continue
            filename = strategy_page_filename(sid, params, asset)
            url_path = f"{EDITION_URL_PATH[edition_key]}/s/{filename}"
            std_index.setdefault((sid, params, std_asset), {})[edition_key] = (
                config.PAGES_URL.rstrip("/") + url_path)

    # Pass 3: render.
    manifest = {}
    for edition_key, payload in payloads.items():
        lang = "ko" if edition_key == "ko" else "en"
        order, groups = per_edition_groups[edition_key]
        page_entries = []
        for (sid, params, asset) in order:
            std_asset = _CRYPTO_STD.get(asset)
            alt_url = None
            if std_asset is not None:
                other_edition = "ko" if edition_key == "en" else ("en" if edition_key == "ko" else None)
                if other_edition:
                    alt_url = std_index.get((sid, params, std_asset), {}).get(other_edition)
            page = _build_one_page(edition_key, lang, sid, params, asset,
                                    groups[(sid, params, asset)], payload, hist_maps[edition_key],
                                    alt_url, hist_pf_maps[edition_key])
            page_entries.append(page)
        _s_index_page(edition_key, lang, page_entries, payload)
        manifest[edition_key] = page_entries
    return manifest


# ---------------------------------------------------------------------------
# sitemap.xml / robots.txt (spec S1). All URLs are absolute, under config.PAGES_URL.
# ---------------------------------------------------------------------------

def build_sitemap(manifest: dict, extra_urls: list):
    """extra_urls: list of (url_path, lastmod) for every non-/s/ page this pipeline writes this
    run (main index/methodology per edition, api docs, digest pages, places pages, ...)."""
    urls = list(extra_urls)
    for edition_key, pages in manifest.items():
        for p in pages:
            urls.append((p["url"] if p["url"].startswith("http") else
                         config.PAGES_URL.rstrip("/") + p["url"], p["as_of"]))

    entries = "\n".join(
        f"  <url><loc>{html.escape(u)}</loc><lastmod>{html.escape(lm)}</lastmod></url>"
        for u, lm in urls
    )
    doc = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n')
    path = os.path.join(config.DOCS_DIR, "sitemap.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def build_robots():
    doc = (f"User-agent: *\nAllow: /\nSitemap: {config.PAGES_URL.rstrip('/')}/sitemap.xml\n")
    path = os.path.join(config.DOCS_DIR, "robots.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path
