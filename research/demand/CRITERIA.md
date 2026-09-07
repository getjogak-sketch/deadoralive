# Search-demand study — pre-registered criteria (fixed before any results are seen, 2026-09-07)

## Question

Before building a new "sea" (a new edition of this engine — a new asset class, platform, or
market not yet covered), do people actually *search* for the kind of strategy that edition would
cover? This is a cheap, mechanical filter on candidate seas using Google Trends search-interest
data — it is not a substitute for the engine's own after-fee backtest once a sea is built, and it
says nothing about whether a strategy that people search for actually *works*.

## Anchor

The anchor is our current sea (crypto trading strategies), represented by its best-known keyword,
fixed here in advance: **"supertrend strategy"**. Every batch of Google Trends requests below
includes this exact keyword, so every candidate keyword's 0–100 interest score is read from the
same normalization basis as the anchor's (Google Trends normalizes to 100 = the peak value across
the up-to-5 terms in one request — comparing scores from two *different* requests is not valid,
which is exactly why the anchor rides along in every batch instead of being measured once).

## Candidate groups

Seven keyword groups (`research/demand/keywords.yml`): `anchor_crypto` (the anchor's own group,
for reference), `forex_commodities`, `bot_templates`, `indicator_combos`, `stocks_etf`,
`sports_systems`, `korean` (Korean-language equivalents of the crypto sea, `geo=KR`).

## Qualification rule (fixed, applied mechanically — no post-hoc tuning)

For each candidate group, its **best keyword** is whichever of its own keywords has the higher
median 12-month interest. A candidate sea (group) **qualifies** if, on that best keyword, in the
same request batch as the anchor:

1. **Level**: median 12-month Google Trends interest ≥ 50% of the anchor's median 12-month
   interest (measured in that same batch), AND
2. **Trend**: that keyword's own interest is *not* in a year-over-year decline of more than 30%
   (median of the most recent 12 months vs. the median of the prior 12 months).

**N/A** (neither qualifies nor disqualifies) if Google Trends returns no data at all for that
keyword — a rate limit, an empty series, or a request failure never gets scored as a disqualify;
it is reported as N/A and the run continues.

"Median 12-month interest" and "year-over-year change" are both computed on `timeframe="today
5-y"` weekly data (the request window this study uses throughout), never on a shorter or
differently-sourced series.

## What this is not

- Not a backtest. A qualifying sea still needs its own after-fee, out-of-sample check once built
  — search interest says people are curious, not that a strategy is profitable.
- Not a claim that Google Trends' 0–100 index is a volume figure — it is a relative, normalized
  index, valid for comparison only within one request batch (see "Anchor" above), which is the
  reason for the anchor-in-every-batch design and a real, documented limitation on comparing across
  the `korean` group's `geo=KR` batches and the worldwide batches: a KR-only batch's normalization
  basis differs from a worldwide batch's even though both include the same anchor keyword string,
  so a `korean`-group ratio is a weaker signal than a same-geo one and is flagged as such in
  `RESULTS.md`.
- Not exhaustive. `related_queries` (top/rising) are recorded per keyword purely to surface
  adjacent terms a human might want to add to a future run — they play no role in the qualify
  decision above.

## Outputs

`research/demand/run_demand.py` writes, under `research/demand/results/`: one raw interest-over-time
CSV and one related-queries CSV per group, `RESULTS.md` (one table per group: keyword, median
12-month interest, ratio vs. anchor, YoY change, qualifies?, top 5 related queries), and
`summary.json` (the same numbers, machine-readable). `--synthetic` self-tests the scoring logic
(median/YoY/qualify/N-A) against fabricated series with known answers, without any network call.
