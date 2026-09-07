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
versions of the data, via **two** placebo constructions. The first is the PRIMARY floor the
decision is based on; the second is a sanity check only.

### Correction, 2026-09-07 (same day, after a production run)

The first implementation of the block-bootstrap placebo rebuilt `high[t]`/`low[t]` as
`close[t] ± range/2` — a band centered on the *synthetic* close, decoupled from that bar's own
open. A production run over the full universe found this **broke the placebo**: 87 real survivors
against a placebo mean of 155 ± 16 — the noise floor exceeded the real result, backwards from what
a noise floor is for. Root cause, confirmed against real BTCUSD: a bar's high is tautologically
&ge; its close, so anchoring high to the *rebuilt* close (rather than to that bar's own real open)
systematically favours a same-bar breakout check (`vol_breakout`) in a way real bars — whose
high/low relate to THEIR OWN open through genuine, varied intrabar price action, not a fixed
formula — do not (real BTCUSD: walk-forward median OOS PF ~1.0 for `vol_breakout`; the same series
through the broken placebo: ~1.4-1.8). This was flagged as a "measured consequence" in the first
version of this file, correctly identified as *conservative-direction*, but under-estimated: it
was large enough to invert the whole comparison, not just soften it. Fixed below — a corrected
entry, not a silent rewrite, same discipline as the "Pre-registration ledger" README section.

### Placebo #1 (primary, used for the decision): block bootstrap by month, whole bars

1. Split each asset's own bars into blocks by calendar month (every bar, not just returns — each
   block keeps its own bars' internal order, only the order of blocks is permuted).
2. Permute the blocks' order (a true permutation — every block used exactly once, seeded,
   reproducible per shuffle) and concatenate.
3. Rebuild the series from this new block order WITHOUT recomputing any bar: every bar keeps its
   own real `high/open`, `low/open`, and `close/open` ratios exactly as historically observed —
   genuine intrabar shape, untouched. Only `open[t]` is re-chained, to the *previous* (new-order)
   bar's synthetic close (`open[t] = close[t-1]`, per this task's own instruction — removes the
   artificial gap at each block boundary; `close[t]`/`high[t]`/`low[t]` then follow from `open[t]`
   times that bar's own real ratios). Dates are left exactly as the real series' dates — only
   *which* real bar's shape sits at each date changes.

This is still a documented simplification (autocorrelation *within* a calendar month IS preserved;
correlation *across* months is destroyed; a bar's shape is real but is now paired with a different
neighbourhood than it actually occurred in) — but no bar's own OHLC relationship is ever invented.

### Placebo #2 (secondary, sanity check only — never used for the decision): circular time-shift

The whole series is rotated by a random offset of &ge; 1 year (bar values are **completely
untouched** — real values, real neighbours, real sequence — only *which calendar date* each real
bar's values land on shifts, via a single wrap-around seam). If the selection rule has no hidden
time-specific edge, its survivor count here should land close to the REAL (unshifted) count, not
near placebo #1's noise floor — a large gap between "real" and "time-shifted real" would mean the
selection rule itself is somehow keying off which years are "recent", independent of any
synthetic-data question entirely.

**An honest residual, found while validating this fix**: even after the correction, `vol_breakout`
on real BTCUSD run through EITHER placebo (block bootstrap AND the untouched time-shift) still
shows somewhat higher median OOS PF than the real, most-recent-6-years evaluation window (~1.0-1.5
vs. ~0.96). Because this shows up on time-shift too — which touches no bar value at all — it is
best read as genuine regime heterogeneity in BTC's own history (older stretches were kinder to
trend/breakout strategies than the most recent one) rather than a synthetic-construction artifact;
exactly the distinction having two independent placebo mechanisms is for. It is reported, not
hidden, and is a reason to read a borderline "EDGE FOUND" for a one-bar-type strategy with extra
scepticism even after this fix.

Report the mean and standard deviation of each placebo's survivor count over its 20 shuffles.
Placebo #1's mean + 1 sd is the **noise floor**: how many "survivors" this exact selection process
finds by construction, in data with no real edge, purely from testing many combinations.

**Decision**: if the real survivor count ≤ placebo #1's mean + 1 standard deviation, the program
reports **"NO EVIDENCE OF EDGE"** — the real result is not distinguishable from what the same
procedure finds in known-noise data. Only a real count *above* that band is reported as **"EDGE
FOUND"**, and even then only as a count, per E4. Placebo #2 is reported alongside for context but
never changes this decision.

## E4. Output & secrecy

- **Public** — `research/edge/results/SUMMARY.md`: `N` tested, survivor count (overall and broken
  down by `config.EDITIONS` edition — en/ko/stocks/macro — never by individual asset or strategy),
  BOTH placebos' mean ± sd (block bootstrap, the primary floor the decision uses; time-shift, the
  secondary sanity check), the decision, and edition-level aggregate stats (combinations tested,
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
- The block-bootstrap placebo (placebo #1) applied to a pure-noise series (no real edge to begin
  with) must yield approximately zero survivors — a direct regression test for the "155 vs. 87"
  break the 2026-09-07 correction fixed: a placebo that inflates a noise floor above what a
  genuine edge would need would show up here first.
- Walk-forward windows are checked to be non-overlapping and never use data beyond their own end
  date (truncation test: a strategy's signal computed on a truncated series matches the full
  series exactly on the overlap, same style as `research/attention/run_study.py`'s own lookahead
  check).
- Encryption round-trip: write a small JSON, encrypt it with a throwaway passphrase, decrypt it
  back (`openssl enc -d ...`), and check the content matches byte-for-byte.
