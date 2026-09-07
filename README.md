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

# 1. Fetch data. Bitstamp's public API (www.bitstamp.net) is network-blocked in this dev
#    environment, so use --offline, which copies in the local Bitstamp-sourced v1 CSVs as a
#    stand-in for BTCUSD (see "Local vs. production data" below). ETHUSD has no local source
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
`--offline`, real Bitstamp BTCUSD/ETHUSD) → `tests.py` → `run_weekly.py` → commit & push
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

This dev environment blocks `www.bitstamp.net` and every other exchange/finance API, so
`fetch_data.py`'s real Bitstamp path is **written but never exercised here** — only its
`--offline` path has been run, using `data/btc_1d.csv` and `btc_4h.csv` (Bitstamp
BTC/USD, v1's original source, last-bar-2026-09-06-dropped) copied in as `BTCUSD_1d.csv` /
`BTCUSD_4h.csv`. In real weekly operation this becomes actual Bitstamp BTCUSD/ETHUSD klines —
a different venue, a genuinely different (if closely correlated) price series, not merely a
renamed copy. `ETHUSD` has no local stand-in at all in this environment and is skipped with a
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

- **44 verdicted rows**: 22 strategy variants × 1 asset (BTCUSD; ETHUSD skipped) × 2 timeframes
  (1d, 4h). In production, with both assets available, this is 88 rows (spec_v2 §6's number).
- **4 reference rows**: `buy_and_hold` + `dca_weekly`, × 2 timeframes (no verdict badge).
- **Verdict tally** (this offline BTC-only run, `as_of` = 2026-09-05): `ALIVE 8, FADING 17,
  DEAD 12, TOO FEW TRADES 7`.
- `docs/index.html` renders all 48 rows across 2 asset/timeframe sections, `docs/methodology.html`
  documents the rules above, `results/latest.json` / `results/history/2026-09-05.json` /
  `docs/latest.json` all carry the same payload.

### Suspiciously good results (spec_v2 §6: "OOS PF > 5 또는 샤프 > 4는 버그로 간주")

One flagged row: **`dip_pct` (BTCUSD, 4h): OOS PF = 13.64** (Sharpe = 1.03, not flagged on that
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
data/BTCUSD_1d.csv, data/BTCUSD_4h.csv   (offline stand-ins; ETHUSD absent in this environment)
```

## Korean edition (Upbit KRW-BTC/KRW-ETH)

A second edition of the exact same weekly pipeline, added additively (no existing module was
rewritten; `engine.py`, `strategies.py`, `indicators.py`, `metrics.py`, `verdict.py`, and
`registry.py` are byte-for-byte unchanged, and the English edition's assets, costs, thresholds,
and output files are untouched).

- **Data source**: Upbit's public candle REST API (no key), `KRW-BTC`/`KRW-ETH`, 1d/4h — see
  `fetch_data.py`'s Upbit section (`fetch_ohlc_upbit`, `_parse_upbit_page`,
  `_drop_still_forming_upbit`). Paginates backwards via the `to` parameter until
  `config.DATA_START_BY_ASSET` (2017-10-01) is passed, sleeping `0.15s` between requests. Like
  Bitstamp, this is network-blocked in this dev environment — `fetch_data.py --offline` skips both
  Upbit assets with a console warning (there is no local Upbit stand-in file anywhere in this
  environment), and any Upbit HTTP error (4xx/5xx) in the online path is caught per-asset and
  turned into a warning, never a hard failure — the English/Bitstamp edition's fetch is unaffected
  either way. The Upbit response parser is unit-tested against a hand-written fake payload in
  `tests.py` (`test_upbit_parser`), since it can't be exercised against the live API here.
- **Cost**: `config.COST["KRW-BTC"]` / `["KRW-ETH"]` = 0.10% one-way (Upbit fee 0.05% + slippage
  0.05% — the same figure already used for BTCUSD/ETHUSD), added as new dict entries, not a change
  to the existing two.
- **Editions**: `config.EDITIONS = {"en": {...}, "ko": {...}}`. `run_weekly.py`'s existing
  English-edition code path is untouched and still writes `results/latest.json`,
  `results/history/<as_of>.json`, `docs/index.html`, `docs/methodology.html`, `docs/latest.json`.
  A new `_run_ko_edition()` function additionally writes `results/latest_ko.json`,
  `results/history/ko_<as_of>.json`, `docs/ko/index.html`, `docs/ko/methodology.html`,
  `docs/ko/latest.json` — **a separate JSON file per edition**, chosen over merging into
  `results/latest.json` to avoid changing the existing file's row count/shape for any downstream
  consumer. As a belt-and-braces measure every row in *both* editions' JSON also carries a new
  `"edition"` key (`"en"` or `"ko"`), so a future consumer that wants a single merged feed can
  still get one.
- **Korean page** (`docs/ko/index.html`, `docs/ko/methodology.html`): same layout/columns as the
  English page (`build_site.py`'s `build_index_ko`/`build_methodology_ko`, reusing `BASE_CSS`,
  `VERDICT_COLORS`, and the existing `_fmt_*` number helpers as-is), all UI text in Korean, verdict
  badges shown as 생존/약화/사망/표본 부족 with the English word kept in small text alongside,
  strategy names as "Korean (English)" (`build_site.STRATEGY_NAME_KO`), and last-bar close prices
  formatted as whole-won KRW (`₩163,000,000`-style, `_fmt_krw`). The mandatory disclaimer
  (`config.LEGAL_DISCLAIMER_KO`, verbatim) is rendered at both the top and bottom of every Korean
  page; `tests.py`'s `test_korean_page_disclaimer_and_banned_words` asserts it appears exactly
  twice and that none of 추천/수익 보장/확실/필승 appear anywhere on the page (checked against both
  a synthetic full-data page and the no-data page below). A small language-switch link
  (`한국어`/`English`) was added to both editions' headers/footers.
- **No Upbit data this week**: if neither `KRW-BTC` nor `KRW-ETH` has a local data file (as in
  this dev environment, and possibly in CI if Upbit 4xx's), `_run_ko_edition()` calls
  `build_site.build_empty_edition_page_ko()` instead of failing — it renders the disclaimer
  top/bottom plus an explicit "이번 주 데이터 없음" ("no data this week") notice and a link back to
  the English edition, rather than an empty or missing page. Verified: `python3 fetch_data.py
  --offline && python3 tests.py && python3 run_weekly.py --offline` all exit 0, and
  `docs/ko/index.html` is produced showing that notice, while the English edition's 48-row output
  is unaffected (same verdict tally as before this change: `ALIVE 8, FADING 17, DEAD 12, TOO FEW
  TRADES 7`).
- **Workflow**: `.github/workflows/weekly.yml` needed no changes — its existing `python
  fetch_data.py` step now also fetches Upbit data (with the same `continue-on-error` /
  failure-reporting steps still covering it), and its existing `python run_weekly.py` step now
  also builds the Korean pages.
- **Uncertain / unverifiable in this environment**: Upbit's real API (`api.upbit.com`) is
  network-blocked here exactly like Bitstamp's, so the online fetch path (`fetch_ohlc_upbit`,
  `update_symbol_online_upbit`) is written and reviewed against Upbit's public documentation but
  has only ever been exercised through its pure, unit-tested parsing/partial-candle-drop helpers
  — never against a live response. In particular: the exact accepted string format for the `to`
  query parameter on a second and later page (this code sends back the previous page's own
  `candle_date_time_utc` string verbatim, which matches Upbit's documented convention but was not
  confirmed against a real multi-page response), and whether Upbit ever includes the still-forming
  candle in a response with no `to` parameter at all (this code defensively drops the last row
  whenever its bar hasn't fully closed as of "now", which is correct either way but may drop one
  extra already-closed bar if Upbit in fact never returns a partial one — a one-bar discrepancy at
  most, self-correcting next week).

## Macro edition (gold, silver, oil, EUR/USD, USD/JPY)

A fourth edition of the same weekly pipeline, English-only, added additively on top of the
crypto (en/ko) and stocks editions — no existing module was rewritten.

- **Assets**: `GLD` (gold ETF), `SLV` (silver ETF), `USO` (oil ETF), `EURUSD` (EUR/USD),
  `USDJPY` (USD/JPY), daily only. File-safe asset ids (`config.MACRO_ASSETS`) map to Yahoo's own
  chart symbols (`config.MACRO_YAHOO_SYMBOL`, e.g. `EURUSD` &rarr; `EURUSD=X`) — Yahoo's FX
  spelling contains a literal `=`, not a safe bare filename component, which is why the two are
  kept distinct rather than using the Yahoo ticker as the asset id directly.
- **Data source**: the existing Yahoo chart JSON fetcher (`fetch_data.py`'s `fetch_ohlc_yahoo`,
  already generic on the symbol string passed in — reused verbatim, not duplicated), no Stooq leg
  (Stooq does not carry FX pairs). `DATA_START` is 2007-01-01 — GLD (listed 2004-11-18), SLV
  (2006-04-28), USO (2006-04-10), and both FX pairs' Yahoo history all predate that floor, so one
  shared date covers every asset. Network-blocked in this dev environment exactly like
  Bitstamp/Upbit/Stooq, so this path is exercised here only through the already-unit-tested
  `_parse_yahoo_chart_json` parser (`tests.py`'s `test_yahoo_parser`, including an FX-shaped fake
  payload with `volume=0` on every bar — a real Yahoo convention for spot FX, not fetch-side
  padding). Nothing in `engine.py`/`strategies.py`/`indicators.py`/`metrics.py` ever reads the
  volume column, so an all-zero volume series is inert for every strategy in the registry
  (`tests.py`'s `test_macro_edition_fx_volume_zero_is_inert` checks this directly).
- **Cost**: ETFs (`GLD`/`SLV`/`USO`) 0.02% one-way, FX (`EURUSD`/`USDJPY`) 0.01% one-way — new
  `config.COST` entries, none of the existing ones changed.
- **bars_per_year**: 252 for every macro asset (`config.MACRO_BARS_PER_YEAR`), the same convention
  the stocks edition uses. FX technically trades roughly 260 days/year (no weekend close) rather
  than an ETF's 252, but a single shared convention keeps the FX and ETF rows in this edition's own
  table comparable; the &lt;3% difference is called out on `docs/macro/methodology.html` rather
  than split into two conventions.
- **Same registry, thresholds, robustness map, badges, Decay Index, digest, feeds, and API** as
  every other edition — `docs/api/v1/macro/`, `docs/macro/s/` SEO pages, `docs/macro/badges/…`
  (via `badges.py`'s already-generic `write_badges_for_payload`), sitemap entries, and
  language/edition switch links everywhere (crypto &harr; stocks &harr; macro &harr; 한국어). No new
  strategy was added, so `registry_ledger.json` is untouched; `docs/registry.html`/
  `docs/ko/registry.html` gained an "Editions covered" section instead, listing all four editions.
- **Page copy**: the macro index page's `<title>` tag reads "Dead or Alive — gold, oil, FX: popular
  trading strategies re-tested weekly after fees" (`config.PROJECT_TITLE_MACRO`), separate from its
  `<h1>` (still "Dead or Alive", like every other edition). Its SEO strategy pages
  (`docs/macro/s/…`) lead with the matching high-demand phrase where one applies — e.g. "Gold
  trading strategy: Supertrend (10, 3) on GLD — does it still work in 2026? ..." for GLD, "EUR/USD
  trading strategy: ..." for EURUSD — via the same lead-phrase mapping described in "SEO title
  tuning" below. Both the results page and the methodology page carry a short note that GLD/SLV/USO
  prices are not adjusted for dividends/distributions and that the FX pairs have no interest-rate
  carry or rollover cost modelled.
- **No Yahoo data this week**: if none of the five macro assets has a local data file (as in this
  dev environment, and possibly in CI if Yahoo rate-limits or 4xx's), `_run_macro_edition()` calls
  `build_site.build_empty_edition_page_en()` — a new, English-language counterpart of the Korean
  edition's own `build_empty_edition_page_ko()` — instead of failing: it renders an explicit
  "No macro data this week" notice and links back to the other editions, exactly like the Korean
  "no data this week" page does, rather than a missing or blank page. Verified:
  `python3 fetch_data.py --offline && python3 tests.py && python3 run_weekly.py --offline` all
  exit 0 and `docs/macro/index.html` shows that notice (there is no local Yahoo stand-in for any
  macro asset in this environment), while every other edition's output is unaffected.
- **Uncertain / unverifiable in this environment**: Yahoo's real chart API
  (`query1.finance.yahoo.com`) is network-blocked here exactly like Stooq/Bitstamp/Upbit, so the
  online fetch path (`update_symbol_online_macro`) is written and reviewed against the same request
  shape the stocks edition's already-working Yahoo fallback uses, but has never been exercised
  against a live response for these five specific symbols — in particular whether Yahoo's FX
  tickers (`EURUSD=X`, `USDJPY=X`) behave identically to its equity tickers under the
  `period1`/`period2` explicit-window request this code sends (the stocks edition's own code
  comment notes `range=max` silently returns monthly bars from Yahoo; the same risk could in
  principle apply to FX, unconfirmed here) and whether the robustness map's runtime stays well
  under the ~10-minute CI budget once nine asset/timeframe combinations (up from four) run through
  it every week — see "Verification results" for the local BTC-only measurement this still relies
  on as its budget check.

## SEO title tuning from the demand study

`seo_pages.py`'s `ASSET_LEAD_EN`/`STRATEGY_LEAD_EN` maps (and `_seo_lead_phrase`, which picks
between them, asset match winning over strategy match) make a strategy page's `<title>`/`<h1>`
lead with the exact high-demand search phrase from `research/demand/keywords.yml`'s keyword pool
where one is known to match, instead of opening with the bare strategy name: `QQQ` pages lead
"QQQ strategy: …", gold (`GLD`) pages lead "Gold trading strategy: …", `EURUSD` pages lead
"EUR/USD trading strategy: …", and any Bollinger-Bands-family page (`bb_mr`/`bb_breakout`/
`bb_squeeze`, on any asset) leads "Bollinger Bands strategy: …", `ichimoku_cloud` pages lead
"Ichimoku strategy: …". Everything after the lead phrase (the "on &lt;asset&gt; — does it still
work in &lt;year&gt;? ..." tail) is unchanged. Deliberately conservative: only phrases this repo
already has keyword-pool evidence for are mapped — "grid bot" / "pionex" (the `bot_templates`
keyword group) are not strategies this site tests yet and are intentionally left unmapped, per
this task's own instruction. `research/demand/results/RESULTS.md` (the qualification study itself,
maintained by a separate agent) had not yet been produced at the time this mapping was written;
the mapping above follows this task's own explicit phrase list, which matches
`research/demand/keywords.yml`'s existing keyword pool verbatim — re-check
`research/demand/results/RESULTS.md` once it exists and extend the two maps if it surfaces further
qualifying phrases this site already covers.

## Places this engine is published to

`docs/places.html` (English) and `docs/ko/places.html` (Korean) are the always-current, generated
version of this section — this is a short summary. Every channel here is additive: none of it
changes a number, a threshold, or an existing page's content, and none of it is required for the
core weekly pipeline (`fetch_data.py` → `tests.py` → `run_weekly.py`) to run.

**Live now — no setup needed:**
- The site itself: English/crypto (`docs/index.html`), Korean/Upbit (`docs/ko/index.html`),
  Stocks/SPY+QQQ (`docs/stocks/index.html`), Macro/gold+silver+oil+FX (`docs/macro/index.html`).
- The pre-registration ledger (`docs/registry.html`, `docs/ko/registry.html`) — see "Pre-registration
  ledger" below.
- The machine-readable JSON API (`docs/api/v1/<edition>/latest.json`, spec_v3 §B).
- The "Check my strategy" GitHub Issues bot (spec_v3 §E).
- One static page per (strategy variant, asset) for search (`docs/s/`, `docs/ko/s/`,
  `docs/stocks/s/`, `docs/macro/s/`), plus `docs/sitemap.xml` and `docs/robots.txt` (this task's
  §S1).
- RSS (`docs/feed.xml`, `docs/ko/feed.xml`) and JSON Feed (`docs/feed.json`) of the weekly digest
  (`digest.py`, §S2), one item per weekly run.

**Enabled when a GitHub repo secret is set** (repo → Settings → Secrets and variables → Actions;
`publish.py`, §S3 — every one of these is independently optional and skips itself, printing
`skipped: <VAR> not set` and exiting 0, when its secret is absent):

| Channel | Secret name(s) |
|---|---|
| Email newsletter (Buttondown) | `BUTTONDOWN_API_KEY` (+ `BUTTONDOWN_SEND_KO=1` for a Korean copy) |
| Bluesky post | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` |
| Mastodon post | `MASTODON_INSTANCE`, `MASTODON_TOKEN` |
| Kaggle dataset | `KAGGLE_USERNAME`, `KAGGLE_KEY` |
| Hugging Face dataset | `HF_TOKEN`, `HF_DATASET_REPO` |

**One-time manual registration** (outside this repo, by a human, once): Google Search Console and
Naver Search Advisor (verify the site, submit `docs/sitemap.xml`), a RapidAPI listing over
`docs/api/v1/<edition>/latest.json`, and dev.to's "Import from RSS" pointed at `docs/feed.xml`.

**Unverified in this dev environment**: `api.buttondown.com`, `bsky.social`, the Mastodon/Kaggle/
Hugging Face APIs, and every search-engine/RapidAPI/dev.to registration above are all
network-blocked here exactly like Bitstamp/Upbit/Stooq/Yahoo already are — `publish.py` is written
against each provider's own documented request format and unit-tested against a monkeypatched
`requests.post` (`tests.py`'s `test_publish_payload_shapes_fake_http`), but has never been
exercised against a live endpoint.

## Pre-registration ledger

`REGISTRY.md` is the rulebook: every strategy variant enters with its exact rule text, fixed
parameters, registration date, and the git commit that added it — the same idea as pre-registering
a clinical trial, so results can't be curve-fit after the fact. Once an entry exists it never
changes; a correction is always a new, separately dated entry.

- `registry_ledger.json` (repo root): one entry per tradeable registry variant (the same universe
  `verdict.py` ever badges — REFERENCE rows excluded), built once by `ledger.py` from
  `registry.py` + `git log --diff-filter=A -- registry.py` (both of registry.py's two commits
  added one whole group each — `REGISTRY`+`REFERENCE` on 2026-09-06, `POPULAR_COMBOS` on
  2026-09-07 — so there's no date ambiguity to resolve here). `ledger.py` is a one-time/append-only
  generator (`python3 ledger.py`), never run automatically by the weekly pipeline — a ledger entry
  must not shift just because `run_weekly.py` ran again.
- `registry_ledger.snapshot.json`: a checked-in copy of the ledger frozen at last review.
  `tests.py`'s `test_ledger_immutable_against_snapshot` fails the whole suite (which blocks every
  weekly run) if any entry present in the snapshot differs from the live ledger — new entries are
  fine, changed old ones are not. `test_ledger_matches_registry_bijection` checks the ledger and
  `registry.py` never drift apart (every variant has exactly one entry, and vice versa).
- `docs/registry.html` / `docs/ko/registry.html`: the ledger rendered as a table (sorted by
  registration date), REGISTRY.md's rules at the top, and how to embed a verdict badge (see
  "Verdict badges" below). Linked from every page's footer and from methodology's "How strategies
  get in" section.
- `.github/ISSUE_TEMPLATE/propose-strategy.yml`: a queue (not automated) for readers to propose a
  strategy — rule text, params, source. An accepted proposal is registered, dated, before any
  backtest runs for it, exactly like every other entry.

## Strategy Decay Index

`decay.py` turns each edition's already-computed `results/history/<...>.json` `tally` field (no
recomputation — `run_weekly.py`'s own `vd.tally(...)` call already counts exactly this) into a
weekly time series per edition: `alive`/`fading`/`dead`/`too_few` as shares of that week's textbook
rows, plus a scalar **Decay Index** = share DEAD among rows with &ge;10 OOS trades
(`dead / (alive+fading+dead)`, `None` when that denominator is 0). Written to
`docs/api/v1/<edition>/index_history.json` every run. `docs/index-history.html` (en, covering the
`en`+`stocks`+`macro` editions) and `docs/ko/index-history.html` (ko) render it as a small
inline-SVG line chart (`charts.py`, no external JS — the same "no external resource loads" rule as
the rest of this site) plus a table, with an explicit note that the chart's value only ever grows
week by week and can't be reconstructed retroactively by re-running anything. `charts.py`'s
sparkline primitive is also used on every SEO strategy page (`seo_pages.py`) to show OOS profit
factor over time, once &ge;3 history points exist for that (strategy, params, asset, timeframe).

## Verdict badges

`badges.py` writes one flat, shields.io-style SVG badge per (strategy variant, asset, timeframe)
non-reference row — `docs/badges/<edition>/<strategy_id>-<params-slug>-<asset>-<tf>.svg`, e.g.
`docs/badges/en/sma_cross-10-50-BTCUSD-1d.svg` — reading "Dead or Alive | FADING (PF 2.35)" in
verdict colours, plus one edition-wide `docs/badges/<edition>/summary.svg` reading "Dead or Alive |
8 alive / 44". Colours are a small local hex palette (`badges.VERDICT_BADGE_COLORS`), deliberately
**not** `build_site.VERDICT_COLORS` — that palette is `var(--alive-fg)`-style CSS custom-property
references that only resolve inside this site's own stylesheet, and a badge is served and embedded
on its own (a README, a third-party page) with no access to it. A badge states only that week's
automated, out-of-sample, net-of-cost verdict — never an endorsement or advice language; see
`docs/registry.html`'s "Embed a badge" section for the markdown embed snippet shown to readers.

## Edge research program

`research/edge/` is a separate, one-time-per-run question from everything above: not "does this
week's OOS still look OK?" (`verdict.py`, re-run every week) but "is there any strategy x asset x
timeframe combination in this whole codebase that is robustly profitable after fees *across
years* of walk-forward windows, and not by luck once the number of combinations tested is honestly
accounted for?" `research/edge/CRITERIA.md` is the full pre-registered rule (written before
`run_edge.py` existed, same discipline as `REGISTRY.md`/`registry_ledger.json` above) — read that
file for the exact thresholds; this section only covers the shape of the program and how to use
its output.

- **Universe**: every `registry.py` variant (`REGISTRY` + `POPULAR_COMBOS`, unchanged, reused via
  `registry.iter_variants()`/`iter_popular_combo_variants()`) plus an expanded parameter grid
  defined *only* inside `run_edge.py` itself (`EXTRA_GRID` — sma/ema cross, Donchian, vol-breakout
  k, tsmom, above-SMA, RSI-MR exit) — **never** added to the public `registry.py`, so this program
  can explore far more parameter points than this site ever publishes a verdict for. ~54 strategy
  variants total, times every `data/<SYMBOL>_<TF>.csv` this run finds (crypto, KRW pairs, stocks,
  macro — every asset every edition already covers).
- **Method**: 12 non-overlapping 6-month walk-forward windows over the most recent 6 years per
  asset, reusing `engine.py`/`metrics.py`/`strategies.py` completely unchanged (this program adds
  no new simulation logic — only the walk-forward loop and the selection rule). A combination
  survives only if it clears hit-rate, median-PF, and trade-count bars *and* the identical
  parameters also work on 2+ *other* assets *and* a small parameter neighbourhood around it also
  works (reusing `robustness.py`'s own neighbour-grid idea, walked forward through the same
  windows instead of a single OOS check) — see CRITERIA.md E3 for the exact numbers.
- **The honest part**: the identical selection process is re-run 20 times against a block-
  bootstrap-by-month reshuffling of each asset's own returns (an intentionally simple placebo,
  documented simplifications and a measured limitation both spelled out in CRITERIA.md E3) — this
  is the *noise floor*: how many "survivors" this exact procedure finds by chance alone, with no
  real edge, purely from testing many combinations. The real survivor count is only reported as
  **EDGE FOUND** if it exceeds that noise floor's mean + 1 standard deviation; otherwise the
  program reports **NO EVIDENCE OF EDGE**, explicitly.
- **Public output** — `research/edge/results/SUMMARY.md`: N tested, survivor counts (overall and
  by edition — en/ko/stocks/macro — never by individual asset or strategy), the placebo mean/sd,
  and the decision. It never names a strategy, a parameter, or an asset for a specific survivor.
- **Private output** — `research/edge/results/survivors.json.enc`: the full survivor detail
  (strategy id, params, asset, timeframe, per-window numbers), AES-256-CBC-encrypted with
  `EDGE_PASSPHRASE` via `openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:EDGE_PASSPHRASE`. If
  `EDGE_PASSPHRASE` is unset when `run_edge.py` runs, nothing private is written and `SUMMARY.md`
  says so explicitly rather than silently omitting the file. To read it back:
  ```
  EDGE_PASSPHRASE=... research/edge/decrypt.sh                       # -> ./survivors.json
  EDGE_PASSPHRASE=... research/edge/decrypt.sh path/to/survivors.json.enc /tmp/out.json
  ```
  The passphrase lives only as the GitHub Actions secret `EDGE_PASSPHRASE` and in Cay's own
  keeping — it is never written anywhere in this repo.
- **Workflow** — `.github/workflows/research-edge.yml` (`workflow_dispatch`, 180-minute timeout,
  optional `max_assets`/`shuffles` inputs): checkout, install `pandas`/`numpy`, `fetch_data.py`
  (same step `weekly.yml` already runs — `data/*.csv` is gitignored, so a fresh checkout has none
  without this), self-test (`run_edge.py --synthetic`), the real run, commit `results/`.
- **Runtime**: measured on this dev box (2 asset/timeframe files, the full 54-variant universe,
  a 2-shuffle placebo) at roughly 5 seconds for the main pass and ~3 seconds per placebo shuffle.
  Scaled to the full ~15-asset/timeframe production universe with the default 20 shuffles, that
  extrapolates to well under 10 minutes total — nowhere near the 150-minute soft budget this
  task set, so `--max-assets` (fixed-seed sampling of asset/timeframe files) exists as a
  documented safety valve but is not expected to be needed.
- **Tests** — `research/edge/tests_edge.py` (run by the agent that built this program, not wired
  into `tests.py` or any workflow): a planted persistent edge (across several independent
  synthetic assets, so cross-asset consistency can be satisfied) survives the full selection; pure
  noise does not, over multiple seeds; walk-forward windows never read past their own end
  (truncation test) and a strategy's signal is unchanged by truncation; an encryption round-trip.
  See that file's own module docstring and CRITERIA.md's placebo section for a documented finding
  from building it: a same-bar breakout check (`vol_breakout`) is sensitive to exactly how
  synthetic OHLC bars are fabricated in a way close-driven strategies are not — confirmed against
  real BTCUSD data, and the reason the test suite's own small synthetic universe sticks to
  "state"-type variants.
