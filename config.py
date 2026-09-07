"""
config.py — single source of truth for netcheck v0 (spec_v2).

Hard rule (spec_v2 §7 / "하지 말 것"): all verdict thresholds and all costs live ONLY here.
No other module may hard-code a cost, a badge threshold, or a strategy/param addition.
"""
from __future__ import annotations
import os

# ---------------------------------------------------------------------------
# Identity (spec_v2 header: "최종 이름은 Cay가 정함 — 코드에서 이름은 config.py의
# PROJECT_NAME 한 곳에만 둔다")
# ---------------------------------------------------------------------------
PROJECT_NAME = "Dead or Alive"
TAGLINE = "Popular trading strategies, re-tested every week — after fees, out-of-sample."
REPO_URL = "https://github.com/getjogak-sketch/deadoralive"     # placeholder — fill in once the repo has a public home
SIGNUP_URL = ""   # placeholder — empty means the signup link is hidden on the page

# Privacy-friendly visit counter (GoatCounter: no cookies, no personal data). Public snippet —
# it only tells the browser where to send an anonymous page-view ping. Empty string disables it.
ANALYTICS_SNIPPET = ('<script data-goatcounter="https://jogak.goatcounter.com/count" '
                     'async src="//gc.zgo.at/count.js"></script>')
# Placeholder GitHub Pages URL (spec_v3 §B needs a concrete base URL for the API docs' `requests`
# example) — the conventional <owner>.github.io/<repo> address for REPO_URL above. Fill in once
# the repo has a public home, same as REPO_URL itself.
PAGES_URL = "https://getjogak-sketch.github.io/deadoralive"
LEGAL_DISCLAIMER = (
    "Educational / informational content only. This is not investment advice, and nothing here "
    "is a recommendation to buy or sell any asset. Past backtested performance, especially "
    "net-of-cost historical simulation, does not guarantee future results."
)

# ---------------------------------------------------------------------------
# Paths (relative to this file, so the same code runs locally and in CI)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
HISTORY_DIR = os.path.join(RESULTS_DIR, "history")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

# Offline mode data sources (spec_v2 §1): this dev environment has api.binance.com blocked, so
# fetch_data.py --offline copies these local Bitstamp-sourced v1 files in as a stand-in for the
# Bitstamp BTCUSD feed. There is no local ETHUSD source anywhere — that asset is skipped with a
# warning by fetch_data.py, per spec_v2 §1, not treated as a failure.
OFFLINE_SOURCES = {
    ("BTCUSD", "1d"): "/home/claude/data/btc_1d.csv",
    ("BTCUSD", "4h"): "/home/claude/data/btc_4h.csv",
}
# The local BTC files' last row is the partial (not-yet-closed) bar dated 2026-09-06 (spec.md §1);
# fetch_data.py drops it, the same way it would drop the exchange's still-forming current candle.
OFFLINE_PARTIAL_BAR_DATE = "2026-09-06"

# ---------------------------------------------------------------------------
# Universe (spec_v2 §1/§3)
# ---------------------------------------------------------------------------
ASSETS = ["BTCUSD", "ETHUSD"]
TIMEFRAMES = ["1d", "4h"]

# Production data is floored at (2017-08-17); the offline Bitstamp stand-in file
# starts in 2012 (thin-liquidity years that distort IS metrics). Bars before this date are
# dropped for every asset/timeframe before any indicator is computed, so offline and production
# runs see the same history window.
DATA_START = "2017-08-17"

# Data source: Bitstamp public OHLC API (no key, no geo-block — Binance returns HTTP 451 from
# GitHub's US-based runners). Bitstamp market symbols are lowercase: btcusd, ethusd.
DATA_SOURCE = "bitstamp"
BITSTAMP_SYMBOL = {"BTCUSD": "btcusd", "ETHUSD": "ethusd"}

# Pagination start dates (Bitstamp), used by fetch_data.py's online pagination start point.
LISTING_DATE = {
    "BTCUSD": "2017-08-17",
    "ETHUSD": "2017-08-17",
}

# ---------------------------------------------------------------------------
# Costs — one-way, applied as a fraction of fill price (v1 spec.md §3, extended to ETH).
# BTC value is v1's Upbit-fee(0.05%) + slippage(0.05%) figure carried over verbatim.
# ETH uses the same figure: no ETH-specific cost was specified anywhere; treating it like BTC
# (comparable-liquidity major-crypto spot trading) is a documented simplifying assumption
# (see README "Simplifying assumptions"), not a new number invented ad hoc.
# ---------------------------------------------------------------------------
COST = {
    "BTCUSD": 0.0010,
    "ETHUSD": 0.0010,
}

# bars_per_year for Sharpe annualization (v1 spec.md §5 convention, keyed by timeframe here since
# it does not depend on the asset).
BARS_PER_YEAR = {
    "1d": 365,
    "4h": 365 * 6,
}

# ---------------------------------------------------------------------------
# Rolling IS/OOS window (spec_v2 §2)
# ---------------------------------------------------------------------------
OOS_DAYS = 730

# ---------------------------------------------------------------------------
# Verdict badge thresholds (spec_v2 §4) — fixed before results are seen; do not tune post hoc.
# ---------------------------------------------------------------------------
VERDICT_THRESHOLDS = {
    "ALIVE_MIN_OOS_PF": 1.2,
    "ALIVE_MIN_OOS_TRADES": 30,
    "FADING_MIN_OOS_PF": 1.0,
    "FADING_MIN_OOS_TRADES": 10,
    "DEAD_MAX_OOS_PF": 1.0,
    "TOO_FEW_MIN_OOS_TRADES": 10,
}

# DCA reference strategy: fixed notional bought each week (arbitrary currency unit — only ratios
# to invested capital are reported, so the unit itself is inconsequential).
DCA_WEEKLY_AMOUNT = 1.0

# Params considered "suspiciously good" per spec_v2 §6 — flagged in README/console, not filtered.
SUSPICIOUS_OOS_PF = 5.0
SUSPICIOUS_OOS_SHARPE = 4.0

# ---------------------------------------------------------------------------
# Korean edition (Upbit KRW spot market) — additive extension, nothing above this line is
# modified: the English edition's ASSETS/COST/VERDICT_THRESHOLDS/OOS_DAYS/registry are untouched,
# and this edition is run through the exact same engine.py/strategies.py/verdict.py code paths.
# ---------------------------------------------------------------------------

# Upbit market codes double as our internal asset ids (unlike Bitstamp, which needed a lowercase
# symbol-mapping table) — "KRW-BTC" is both the Upbit `market` query param and our asset id.
UPBIT_ASSETS = ["KRW-BTC", "KRW-ETH"]

# Upbit KRW-BTC was listed 2017-09/10; data is floored a little after listing for thin-liquidity
# safety, per this task's own instruction (distinct from Bitstamp's DATA_START, which is keyed to
# Bitstamp's own 2017-08-17 BTCUSD/ETHUSD listing date).
DATA_START_UPBIT = "2017-10-01"

# Per-asset override of DATA_START, so fetch_data.py/run_weekly.py can look up the right floor
# date for any asset without an if/else on asset naming convention. Assets not listed here (the
# Bitstamp ones) fall back to the module-level DATA_START.
DATA_START_BY_ASSET = {
    "KRW-BTC": DATA_START_UPBIT,
    "KRW-ETH": DATA_START_UPBIT,
}

# Upbit fee (0.05%) + slippage (0.05%) = 0.10% one-way — the same figure already used for BTCUSD/
# ETHUSD (see COST above and README "Simplifying assumptions"), added here per Upbit asset rather
# than changing the meaning of the existing COST dict's two entries.
COST["KRW-BTC"] = 0.0010
COST["KRW-ETH"] = 0.0010

# Korean-language site copy (verbatim strings required by this task; kept alongside the English
# PROJECT_NAME/TAGLINE/LEGAL_DISCLAIMER above rather than overloading those constants).
PROJECT_TITLE_KO = "Dead or Alive — 인기 매매 전략, 수수료 떼고 매주 다시 검사"
TAGLINE_KO = "사람들이 많이 쓰는 매매 전략 22개를 수수료를 뗀 조건으로 매주 다시 검사해서, 최근 2년에도 통했는지 보여줍니다."
LEGAL_DISCLAIMER_KO = (
    "본 페이지는 무료로 제공되는 정보·교육 목적의 자료이며, 투자 자문이나 특정 자산의 매수·매도 "
    "권유가 아닙니다. 과거 백테스트 결과는 미래 수익을 보장하지 않으며, 모든 투자 판단과 책임은 "
    "이용자 본인에게 있습니다. 운영자는 유사투자자문업자가 아니며 어떠한 수익도 보장하지 않습니다."
)

# ---------------------------------------------------------------------------
# Editions (this task's requirement 3): which assets render onto which output page, in which
# language. run_weekly.py loops this dict. "en" keeps writing to the exact same paths it always
# has (results/latest.json, docs/index.html, docs/methodology.html, docs/latest.json) for zero
# behavior change to the existing edition; "ko" writes results/latest_ko.json,
# results/history/ko_<as_of>.json, and docs/ko/{index,methodology,latest.json}. Every row in every
# edition's payload also carries an "edition" key (documented in README) — belt-and-braces on top
# of the separate-file choice, in case anything downstream ever concatenates both editions' rows.
# ---------------------------------------------------------------------------
DOCS_DIR_KO = os.path.join(DOCS_DIR, "ko")

EDITIONS = {
    "en": {"assets": ASSETS, "lang": "en", "out": DOCS_DIR},
    "ko": {"assets": UPBIT_ASSETS, "lang": "ko", "out": DOCS_DIR_KO},
}

# ---------------------------------------------------------------------------
# Stocks edition (spec_v3 §A) — SPY/QQQ daily, English only. Additive: nothing above this line
# (crypto en/ko editions, ASSETS/COST/BARS_PER_YEAR/VERDICT_THRESHOLDS/registry) is modified. This
# edition is run through the exact same engine.py/strategies.py/verdict.py/registry.py machinery
# as the crypto editions — only the asset list, one-way cost, bars_per_year convention (252
# trading days/year, not 365 calendar days — stocks markets are closed weekends/holidays), data
# floor date, and output paths/language-switch differ.
# ---------------------------------------------------------------------------
STOCKS_ASSETS = ["SPY", "QQQ"]
STOCKS_TIMEFRAMES = ["1d"]
STOCKS_DATA_START = "2000-01-01"
STOCKS_BARS_PER_YEAR = 252

# One-way cost for the stocks edition (spec_v3 §A: "Cost 0.02% one-way"), added as new COST dict
# entries — the existing BTCUSD/ETHUSD/KRW-* entries above are untouched.
COST["SPY"] = 0.0002
COST["QQQ"] = 0.0002

DOCS_DIR_STOCKS = os.path.join(DOCS_DIR, "stocks")
EDITIONS["stocks"] = {"assets": STOCKS_ASSETS, "lang": "en", "out": DOCS_DIR_STOCKS,
                       "timeframes": STOCKS_TIMEFRAMES}
