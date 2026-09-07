# Edge research program — pre-registered criteria (fixed before any results are seen, 2026-09-07)

This file is written *before* `run_edge.py` exists. It is the only place the survival rule lives;
`run_edge.py` implements it mechanically and must never add, drop, or retune a threshold outside
this file (same discipline as `robustness.py`'s own "diagnostic only, nothing here changes a
verdict" rule, and `registry.py`'s "pre-registered, never curve-fit after the fact" rule).

## Question

Every edition of this site already asks, per strategy: "does it still work out-of-sample, this
week, after fees?" (`verdict.py`). This program asks a harder, one-time question instead: across
the **whole** universe of strategy × asset × timeframe combinations this codebase can run, is
there **any** combination that is robustly profitable after fees *across time* — not one week's
OOS window, but many non-overlapping windows spanning years — and not by luck, once the number of
combinations tested is honestly accounted for?

Two failure modes this program is built to avoid:
1. Testing enough combinations that a few clear the bar by chance alone (multiple-testing bias).
2. Reporting a survivor whose "edge" only exists at its exact registered parameters (curve-fit),
   or only on the one asset it happened to be found on (overfit to one market's idiosyncratic
   history).

**Survivors are not published.** This program either says "no evidence of edge" (public,
specific) or "edge found" (public, with the count only — never which strategy/asset/params) plus
an encrypted private record of which combinations survived, for Cay's own eyes.

## E1. Universe

**Strategies** — every variant already in `registry.py` (`REGISTRY` + `POPULAR_COMBOS`, ~29
variants, unchanged, imported via `registry.iter_variants()` / `iter_popular_combo_variants()`)
**plus** an expanded parameter grid defined *only* in `run_edge.py` (never added to the public
registry — this file is the only place these extra points are pre-registered):

| strategy id | extra grid points (this program only) |
|---|---|
| `sma_cross`  | (5,20), (10,30), (20,50), (30,150), (100,300) |
| `ema_cross`  | (8,21), (20,55), (50,200) |
| `donchian`   | (10,5), (40,20), (100,50) |
| `vol_breakout` k | 0.3, 0.4, 0.6, 0.8, 1.0 |
| `tsmom` n    | 20, 60, 120, 250 |
| `above_sma` n | 20, 100, 150 |
| `rsi_mr` exit | 60, 80 |

None of these extra points overlap an already-registered `registry.py` value for the same id.
Total universe: ~54 strategy variants (registry + extra grid).

**Assets/timeframes** — every `data/<SYMBOL>_<TF>.csv` present at run time (crypto BTCUSD/ETHUSD
1d+4h, Korean KRW-BTC/KRW-ETH 1d+4h, stocks SPY/QQQ 1d, macro GLD/SLV/USO/EURUSD/USDJPY 1d — the
same files `fetch_data.py` writes for every edition in `config.EDITIONS`). Every asset/timeframe
is classified into its `config.EDITIONS` edition (`en`/`ko`/`stocks`/`macro`) for reporting only —
that grouping never affects the survival rule itself. Signal/engine functions
(`strategies.py`/`indicators.py`/`engine.py`/`metrics.py`) are reused completely unchanged.

## E2. Walk-forward evaluation

- **Windows**: for each asset/timeframe, 12 consecutive, non-overlapping 6-month windows covering
  the most recent 6 years of that asset's own data (the 12th window ends on the series' last bar;
  earlier windows step back 6 months at a time). Where an asset has less than 6 years of history,
  only the windows that fit entirely within the available data are used (fewer than 12) — a
  combination with fewer than 6 available windows is never eligible to survive (E3.a).
- Indicators/signals are computed on the **full** series first (existing warm-up behaviour,
  unchanged) and only *sliced* to each window's mask before simulation — exactly
  `engine.py`'s own `[start_idx, end_idx]` convention, reused as-is.
- Per window: net (after the asset's `config.COST`) profit factor, return, and trade count, via
  the same `engine.simulate_*` + `metrics.compute_metrics` used everywhere else in this repo. A
  window with zero trades has an undefined PF and does **not** count as a "hit" — sitting flat is
  not evidence of edge either way, and this is a conservative (not lenient) choice.
- **Per-combination aggregate**: number of windows with net PF ≥ 1.0 (**hit rate**), median PF
  across windows (a window's `inf` PF — all wins, no losses — is capped at a large finite number
  for the median only, so one lucky loss-free window can't make the median itself unmeasurable),
  worst window's PF, total trades summed across windows, and the **neighbour hit-rate fraction**:
  reusing `robustness.py`'s own parameter-neighbourhood idea (3×3 grid for a 2-numeric-param
  variant, 3 for one param, MA-pair fast<slow filter, same `neighbour_values()` rule) — but instead
  of `robustness.py`'s single-OOS-window PF check, each neighbour is walked forward through the
  *same* windows and given its own hit rate. A strategy with **zero** tunable numeric parameters
  (e.g. `macd`, `bb_mr`, `ichimoku_cloud`) has no neighbourhood to check; that criterion is treated
  as vacuously satisfied for those ids (documented judgment call, same precedent
  `robustness.py`'s own "no tunable parameter" diagnostic already sets).
  This is computed lazily — only for combinations that already pass E3(a)-(c) below — since it can
  never change a combination that already fails those into a survivor; a pure performance
  optimisation, not a rule change.
- **Cross-asset consistency** (E3.d) is read off this same full pass: the identical (strategy id +
  params) point is evaluated against *every* asset/timeframe in the universe already, so "hit rate
  ≥ 50% on ≥ 2 other assets" is a lookup, not a re-run. "Other assets" means distinct underlying
  **asset symbols** (BTCUSD, SPY, GLD, ... — not distinct timeframes of the same symbol; a 1d and
  a 4h series of the same coin are far too correlated to count as independent cross-market
  evidence). For an asset with more than one timeframe, its best (highest) hit rate across its own
  timeframes is what a *different* asset's combination is compared against.

## E3. Multiple-testing control

`N` = number of (strategy variant × asset × timeframe) combinations actually evaluated in E2 —
reported honestly, before any filter.

A combination **survives** only if **all** of:
- **(a)** hit rate ≥ 8/12 windows, or — for an asset with fewer than 12 available windows — ≥ 2/3
  of its available windows, with a floor of 6 available windows (8/12 and 2/3 are the same
  fraction, so this is one rule, not two).
- **(b)** median PF ≥ 1.2 across those windows.
- **(c)** ≥ 100 total trades summed across those windows.
- **(d)** the *same* strategy variant (identical id + params — not merely the same family) *also*
  clears hit rate ≥ 50% on at least 2 other asset symbols (cross-asset consistency; see E2).
- **(e)** neighbour hit-rate fraction ≥ 2/3 (or the criterion is vacuously satisfied — see E2 — for
  a strategy with no tunable numeric parameter).

**Placebo (the honest part)**: the identical selection process — E1 universe, E2 windows and
aggregates, every E3(a)-(e) filter, unchanged — is re-run against **20** independently reshuffled
versions of the data, each built as a **block bootstrap by calendar month on log returns**:

1. Split each asset's log close-to-close returns into blocks by calendar month (each block keeps
   its own bars' internal order — this preserves within-month serial correlation/vol clustering,
   only destroys correlation *across* months).
2. Resample blocks *with replacement* until there are enough to cover the series' full length, in
   a fresh random order per shuffle (seeded, reproducible), then truncate to the exact original
   length.
3. Rebuild a full synthetic OHLC path from the resampled return sequence: `close[0]` = the real
   first close; every later `close[t]` = `close[t-1] * exp(shuffled log return[t])`; `open[t]` =
   the *previous* synthetic close (per this task's own instruction); `high[t]`/`low[t]` = the
   synthetic `close[t]` offset by the **source bar's own high-low range, expressed as a fraction
   of that source bar's close** (not the source bar's raw dollar/₩ range — BTC's 2012 cent-level
   range applied as an absolute number to a 2021 five-figure close would be nonsensical; using the
   *relative* range and re-applying it around the new close level is the "scaled" in this task's
   own wording, made concrete). Dates are left exactly as the real series' dates — only the price
   path is synthetic — so the same window-splitting code runs unmodified. This is a documented
   simplification, not a claim that it reproduces every real market microstructure property
   (autocorrelation *within* a calendar month, and any relationship between a bar's true range and
   its own return, are both discarded).

   **A measured consequence, worth flagging explicitly rather than leaving implicit**: rebuilding
   `high`/`low` as `close ± range/2` makes a bar's own high sit a near-fixed fraction above ITS OWN
   close regardless of that bar's open — real bars are not this generous to a same-bar breakout
   check (confirmed empirically: `vol_breakout` on real BTCUSD shows a walk-forward median OOS PF
   ~1.0, but on that same series run through the block bootstrap it shows ~1.4-1.8). This inflates
   the placebo's own baseline for *one-bar* (`vol_breakout`, `vol_breakout_trend`) variants
   specifically, in the *conservative* direction: it raises the placebo noise floor those variants
   are compared against, making a genuine one-bar edge *harder*, not easier, to clear the E3 "EDGE
   FOUND" bar — and E3(d)'s cross-asset filter (needing the same inflation to independently line up
   on 2+ *other*, differently-shuffled assets for the exact same params) is a further structural
   check against this manufacturing a false "EDGE FOUND". This does not apply to "state"-type
   variants (driven by close alone — `sma_cross`, `tsmom`, ... — the large majority of the
   universe), which never read the synthetic high/low at all.

Report the mean and standard deviation of the placebo's survivor count over the 20 shuffles — this
is the **noise floor**: how many "survivors" this exact selection process finds by construction,
in data with no real edge, purely from testing many combinations.

**Decision**: if the real survivor count ≤ placebo mean + 1 standard deviation, the program
reports **"NO EVIDENCE OF EDGE"** — the real result is not distinguishable from what the same
procedure finds in known-noise data. Only a real count *above* that band is reported as **"EDGE
FOUND"**, and even then only as a count, per E4.

## E4. Output & secrecy

- **Public** — `research/edge/results/SUMMARY.md`: `N` tested, survivor count (overall and broken
  down by `config.EDITIONS` edition — en/ko/stocks/macro — never by individual asset or strategy),
  placebo mean ± sd, the decision, and edition-level aggregate stats (combinations tested,
  pre-candidates before the cross-asset/neighbour filters, etc.). **Never** a strategy id,
  parameter value, or asset name attached to a specific survivor.
- **Private** — `research/edge/results/survivors.json.enc`: the full survivor list (strategy id,
  params, asset, timeframe, window-by-window numbers) encrypted with
  `openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:EDGE_PASSPHRASE`. If `EDGE_PASSPHRASE` is
  unset, nothing private is written, and `SUMMARY.md` says so explicitly rather than silently
  omitting the file. `research/edge/decrypt.sh` documents how to read it back (see README).
- **Workflow** — `.github/workflows/research-edge.yml` (`workflow_dispatch`, 180-minute timeout):
  checkout, install `pandas`/`numpy`, fetch data (`fetch_data.py`, the same step `weekly.yml`
  already runs — this program's data/ files are gitignored, so a fresh checkout has none without
  this step), self-test (`run_edge.py --synthetic`), run the real study, commit `results/`.
  `EDGE_PASSPHRASE` is passed from `secrets.EDGE_PASSPHRASE`.
- **Performance**: `--max-assets N` samples `N` asset/timeframe files (fixed seed) instead of the
  full universe, for use only if a run approaches the 150-minute soft budget (see README for this
  program's actual measured/estimated runtime).

## E5. Tests (`research/edge/tests_edge.py`, run by this agent, not `tests.py`)

- A synthetic series with a **planted, persistent** edge (a clear, repeatable trend the walk-
  forward windows can find every time) must survive the full E3 selection.
- Pure-noise synthetic series must **not** survive, checked over 3 different random seeds.
- Walk-forward windows are checked to be non-overlapping and never use data beyond their own end
  date (truncation test: detection on a truncated series matches the full series on the overlap,
  same style as `research/attention/run_study.py`'s own lookahead check).
- Encryption round-trip: write a small JSON, encrypt it with a throwaway passphrase, decrypt it
  back (`openssl enc -d ...`), and check the content matches byte-for-byte.
