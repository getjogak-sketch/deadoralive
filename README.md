# Dead or Alive (v0)

Implements `spec_v2.md`, which extends `spec.md` (v1). v1's
principles (§0), cost model (§3), metric definitions (§5), and engine-honesty tests (§6) all
carry over unchanged; v1's code (`data_loader.py`, `strategies.py`, `engine.py`, `tests.py`,
`crosscheck_bt.py`) was copied in and **extended, not rewritten** — every v1 function keeps its
original signature and every v1 test still passes.

**Purpose**: every week, fetch fresh OHLCV data for BTC/ETH at 1d/4h, backtest every strategy in
a pre-registered registry net-of-cost, split into out-of-sample (trailing 2 years) vs. in-sample
(everything before that), assign a plain-English verdict badge, and publish a static page. This
is educational infrastructure for asking "does this popular retail strategy still work, after
fees, on real recent data?" — not a signal service and not investment advice.

`PROJECT_NAME` (currently "netcheck", a placeholder) lives in exactly one place: `config.py`.

## How to run

```bash
cd deadoralive

# 1. Fetch data. Binance's public API (api.binance.com) is network-blocked in this dev
#    environment, so use --offline, which copies in the local Bitstamp-sourced v1 CSVs as a
#    stand-in for BTCUSDT (see "Local vs. production data" below). ETHUSDT has no local source
#    and is skipped with a warning, not an error.
python3 fetch_data.py --offline

# 2. Tests — must all pass before anything downstream is trusted.
python3 tests.py

# 3. (optional, slower) v1's two-engine cross-check, re-run for sma_cross only.
python3 crosscheck_bt.py

# 4. Full weekly pipeline: re-runs tests.py as a hard gate, then writes results/ and docs/.
python3 run_weekly.py --offline
```

In production (`.github/workflows/weekly.yml`, cron Monday 00:30 UTC): `fetch_data.py` (no
`--offline`, real Binance BTCUSDT/ETHUSDT) → `tests.py` → `run_weekly.py` → commit & push
`data/`, `results/`, `docs/`.

## Methodology summary

See `docs/methodology.html` for the full writeup (cost model, IS/OOS rolling window, execution
rules, verdict thresholds, engine-honesty checks, the full strategy table). In brief:

- **Cost**: one-way, applied to the fill price. BTC/ETH both use 0.10% (Upbit fee 0.05% +
  slippage 0.05%, v1's figure, extended to ETH — see "Simplifying assumptions").
- **IS/OOS**: `as_of` = the last fully-closed bar's date. OOS = `[as_of - 730 days, as_of]`. IS =
  everything before that. This rolls forward one week each run. Indicator warm-up always uses the
  full price history.
- **Registry** (`registry.py`, spec_v2 §3, verbatim — 22 tradeable variants + 2 reference rows):
  `sma_cross`(3), `ema_cross`(1), `above_sma`(2), `donchian`(2), `vol_breakout`(2),
  `vol_breakout_trend`(1), `rsi_mr`(2), `rsi2_connors`(1), `bb_mr`(1), `bb_breakout`(1),
  `macd`(1), `supertrend`(1), `tsmom`(2), `dip_3down`(1), `dip_pct`(1); plus reference-only
  `buy_and_hold`, `dca_weekly`. Nothing beyond this table is ever run.
- **Verdict badges** (OOS, net of cost): `ALIVE` (PF≥1.2, trades≥30, MDD < B&H MDD), `FADING`
  (PF≥1.0, trades≥10, not ALIVE), `DEAD` (PF<1.0, trades≥10), `TOO FEW TRADES` (trades<10).
  Thresholds live only in `config.VERDICT_THRESHOLDS`.

## Local vs. production data

This dev environment blocks `api.binance.com` and every other exchange/finance API, so
`fetch_data.py`'s real Binance path is **written but never exercised here** — only its
`--offline` path has been run, using `data/btc_1d.csv` and `btc_4h.csv` (Bitstamp
BTC/USD, v1's original source, last-bar-2026-09-06-dropped) copied in as `BTCUSDT_1d.csv` /
`BTCUSDT_4h.csv`. In real weekly operation this becomes actual Binance BTCUSDT/ETHUSDT klines —
a different venue, a genuinely different (if closely correlated) price series, not merely a
renamed copy. `ETHUSDT` has no local stand-in at all in this environment and is skipped with a
console warning by both `fetch_data.py` and `run_weekly.py`, exactly per spec_v2 §1 ("데이터
파일이 없는 자산은 경고만 남기고 건너뛴다") — not treated as a failure.

## Verification results (spec_v2 §6)

### tests.py — ALL 332 CHECKS PASSED

- v1's original 3 test groups (`test_no_lookahead_ma_cross`, `test_no_lookahead_vol_breakout`,
  `test_sanity_cost`) — unchanged, still pass.
- **`test_no_lookahead_registry`** (new): every one of the 22 registry variants, run against v1's
  own three (asset, timeframe) series (BTC 1d/4h, SPY 1d — chosen because they're guaranteed
  present regardless of whether `fetch_data.py` has run yet), at 3 truncation points each past a
  260-bar warm-up floor. Values before the truncation point are required to be bit-identical
  whether or not later data exists.
- **`test_no_lookahead_reference_rows`** (new): `dca_weekly`'s weekly buy-week flags are
  calendar-only and were checked for the same truncation-invariance property.
- **`test_indicator_spot_checks`** (new): RSI(3) against a hand-derived Wilder-formula worked
  example (61.5385 / 77.2727 at two consecutive bars — exact fractions, see the test's own
  comments for the arithmetic); RSI(14) analytic edge cases (strictly increasing → 100, strictly
  decreasing → 0, flat → 50); EMA(2) hand-derived values; MACD ≡ 0 on a constant series;
  Bollinger bands collapsing to the price on a constant series; a hand-traced Donchian example;
  Supertrend warm-up/output-domain sanity.

Run `python3 tests.py` — final line: `ALL TESTS PASSED`.

### crosscheck_bt.py — 8/9 within tolerance (same result as v1, reproduced verbatim)

Re-run unmodified for `sma_cross` only, same settings as v1 (`cash=1e9`, `commission=cost`,
`exclusive_orders=True`, `trade_on_close=False`, `finalize_trades=True`). All 9 (asset, tf,
params) combinations match `n_trades` exactly; 8 of 9 match `total_return` within the 2% bound.
The one exception — SPY (50,200), 6.65% relative error — is the same documented, understood
edge-of-history artifact from v1's README: SPY's SMA(200) first becomes valid right at the tested
window's start, and `backtesting.py`'s internal "one bar after the indicator's first valid value"
warm-up convention shifts its first entry one day later than the pandas engine's, landing on a
day with an unusually large SPY move (the Oct 2000 sell-off) — a one-bar timing artifact, not a
bug in either engine. (This script still writes to v1's own `../bt/results/
crosscheck.csv`, deliberately left unchanged, since it is a v1 file whose location the spec's own
§8 layout defines and this task did not ask to relocate.)

### run_weekly.py --offline — full pipeline, 48 rows

`python3 run_weekly.py --offline` re-runs `tests.py` as a hard gate, then produces:

- **44 verdicted rows**: 22 strategy variants × 1 asset (BTCUSDT; ETHUSDT skipped) × 2 timeframes
  (1d, 4h). In production, with both assets available, this is 88 rows (spec_v2 §6's number).
- **4 reference rows**: `buy_and_hold` + `dca_weekly`, × 2 timeframes (no verdict badge).
- **Verdict tally** (this offline BTC-only run, `as_of` = 2026-09-05): `ALIVE 8, FADING 17,
  DEAD 12, TOO FEW TRADES 7`.
- `docs/index.html` renders all 48 rows across 2 asset/timeframe sections, `docs/methodology.html`
  documents the rules above, `results/latest.json` / `results/history/2026-09-05.json` /
  `docs/latest.json` all carry the same payload.

### Suspiciously good results (spec_v2 §6: "OOS PF > 5 또는 샤프 > 4는 버그로 간주")

One flagged row: **`dip_pct` (BTCUSDT, 4h): OOS PF = 13.64** (Sharpe = 1.03, not flagged on that
axis). Investigated, not a bug: this row has only **3 completed OOS trades** — the verdict engine
correctly assigns it `TOO FEW TRADES` (< 10), so it is never presented as evidence of an edge.
Its in-sample sibling (222 trades, IS PF = 1.60, IS Sharpe = 0.62) shows ordinary performance at
real sample size, and `test_sanity_cost`-style reasoning holds throughout the engine — the
inflated ratio is exactly the small-sample profit-factor artifact v1's own README already
documented for SPY ma_cross(20,100)/(50,200) OOS (2 trades, PF = inf), reproduced here in a new
strategy for the same structural reason: a short 2-year OOS window plus a strategy that only
fires a handful of times on that window. No other row in the 48 crosses either threshold.

## Simplifying assumptions

- **ETH cost = BTC cost (0.10%)**: neither spec gives an ETH-specific figure; `config.COST`
  applies the same Upbit-fee-plus-slippage assumption used for BTC to ETH as a comparable-
  liquidity major-crypto spot instrument, rather than inventing a new unsupported number.
- **`vol_breakout_trend` and other "one-bar"/"state" reuse of v1's exact simulators**: per
  spec_v2 §3's own instruction, every state-type strategy (`sma_cross`, `ema_cross`, `above_sma`,
  `donchian`, `rsi_mr`, `rsi2_connors`, `bb_mr`, `bb_breakout`, `macd`, `supertrend`, `tsmom`,
  `dip_3down`) is executed by v1's unmodified `engine.simulate_ma_cross`, and both one-bar
  strategies (`vol_breakout`, `vol_breakout_trend`) by v1's unmodified
  `engine.simulate_vol_breakout` — only the *signal* function differs per strategy. Only two new
  engine primitives were added: `simulate_hold_n_bars` (for `dip_pct`) and `simulate_dca_weekly`
  (for the DCA reference row).
- **Donchian/RSI-MR/Connors-RSI/Bollinger/dip_3down "hysteresis" state machine**
  (`strategies._stateful_target`): these strategies' entry and exit conditions are not
  complements of one comparison (unlike `fast_sma > slow_sma`), so a small explicit forward loop
  is used instead of pure boolean-Series algebra: the position persists until the *opposite*
  trigger fires. Both triggers going into it are themselves already causal, so this only adds
  persistence, not lookahead (verified per-strategy by `test_no_lookahead_registry`).
- **`dip_pct` overlapping triggers**: while already holding a position, new -5% triggers are
  ignored (long-only, single position, no stacking) — consistent with every other strategy here
  being "fully invested or fully flat."
- **DCA reference metric definition**: since DCA keeps adding capital over time, there is no
  single "equity curve starting at 1.0" the way the other strategies have. `simulate_dca_weekly`
  instead tracks `ratio[t] = (units held) * close[t] / (total notional invested so far)` — 1.0
  exactly at each purchase's own fill price, drifting with the market between and after
  purchases — and reports its final value minus 1 as `total_return`, and its max drawdown as
  `mdd`. This is a documented convention for this reference row, not a claim of a canonical "DCA
  equity curve" definition, and matches spec_v2 §3's instruction that DCA only reports
  return/MDD.
- **DCA "week"**: the first bar (by ISO calendar year+week) present in the period, for both 1d
  and 4h data — i.e. the same weekly cadence regardless of timeframe granularity.
- **Fee drag** is computed by re-running the exact same OOS-period simulation at `cost=0` and
  differencing against the net OOS return — the only place a cost-free number is ever computed or
  shown, and it is labeled "before fees, for illustration" on the page per spec_v2 §5.
- **RSI**: implemented with Wilder's *classic* seeding (a simple mean of the first n gain/loss
  values, then the recursive `(prev*(n-1)+x)/n` update) rather than `pandas.ewm(alpha=1/n)`,
  which seeds differently and would silently disagree with textbook RSI tables at the start of
  a series (see `indicators.rsi_wilder`'s docstring).
- **Bollinger Bands use population standard deviation** (`ddof=0`), matching Bollinger's own
  original n-divisor definition — not pandas' rolling `.std()` default of sample std (`ddof=1`).
- **EMA uses `pandas.ewm(adjust=False)`** (the pure recursive/causal definition used by most
  charting platforms), not the default `adjust=True` weighted-average-of-all-history variant.
- **Supertrend** uses the standard ATR(n)-Wilder-smoothed, ±multiplier×ATR band-flip definition
  (the common TradingView-style formulation); the very first ATR-valid bar's direction is seeded
  by comparing price to its own band (no prior direction exists yet) — this affects exactly one
  warm-up bar and never recurs.
- **Contiguous period masks**: `data_loader.rolling_is_oos_window`'s IS/OOS windows are, like
  v1's fixed windows, assumed (and, via `engine._period_bounds`, enforced) to be contiguous
  blocks of the sorted CSV — true for every data file used here.

## Legal

Educational / informational content only. This is not investment advice, and nothing here is a
recommendation to buy or sell any asset. Past backtested performance, especially net-of-cost
historical simulation, does not guarantee future results.

## File layout

Matches spec_v2 §5:

```
config.py, fetch_data.py, indicators.py, registry.py, strategies.py, engine.py, metrics.py,
verdict.py, run_weekly.py, build_site.py, tests.py, crosscheck_bt.py, data_loader.py
.github/workflows/weekly.yml
docs/index.html, docs/methodology.html, docs/latest.json
results/latest.json, results/history/<as_of>.json
data/BTCUSDT_1d.csv, data/BTCUSDT_4h.csv   (offline stand-ins; ETHUSDT absent in this environment)
```
