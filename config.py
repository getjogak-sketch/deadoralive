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
# Binance BTCUSDT feed. There is no local ETHUSDT source anywhere — that asset is skipped with a
# warning by fetch_data.py, per spec_v2 §1, not treated as a failure.
OFFLINE_SOURCES = {
    ("BTCUSDT", "1d"): "/home/claude/data/btc_1d.csv",
    ("BTCUSDT", "4h"): "/home/claude/data/btc_4h.csv",
}
# The local BTC files' last row is the partial (not-yet-closed) bar dated 2026-09-06 (spec.md §1);
# fetch_data.py drops it, the same way it would drop Binance's still-forming current candle.
OFFLINE_PARTIAL_BAR_DATE = "2026-09-06"

# ---------------------------------------------------------------------------
# Universe (spec_v2 §1/§3)
# ---------------------------------------------------------------------------
ASSETS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAMES = ["1d", "4h"]

# Production Binance data starts at listing (2017-08-17); the offline Bitstamp stand-in file
# starts in 2012 (thin-liquidity years that distort IS metrics). Bars before this date are
# dropped for every asset/timeframe before any indicator is computed, so offline and production
# runs see the same history window.
DATA_START = "2017-08-17"

# Binance symbol listing dates, used by fetch_data.py's online pagination start point.
LISTING_DATE = {
    "BTCUSDT": "2017-08-17",
    "ETHUSDT": "2017-08-17",
}

# ---------------------------------------------------------------------------
# Costs — one-way, applied as a fraction of fill price (v1 spec.md §3, extended to ETH).
# BTC value is v1's Upbit-fee(0.05%) + slippage(0.05%) figure carried over verbatim.
# ETH uses the same figure: no ETH-specific cost was specified anywhere; treating it like BTC
# (comparable-liquidity major-crypto spot trading) is a documented simplifying assumption
# (see README "Simplifying assumptions"), not a new number invented ad hoc.
# ---------------------------------------------------------------------------
COST = {
    "BTCUSDT": 0.0010,
    "ETHUSDT": 0.0010,
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
