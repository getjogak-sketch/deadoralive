# Edge research program — results (2026-09-07)

See `CRITERIA.md` for the full pre-registered rule. This file reports only aggregate counts, per that pre-registration — no strategy id, parameter, or asset name is ever attached to a survivor here.

**N tested** (strategy variant x asset x timeframe combinations): 810
**Universe**: 54 strategy variants (29 from `registry.py`, 25 extra-grid-only, defined solely in `run_edge.py`) x 15 asset/timeframe series.
**Pre-candidates** (passed hit-rate + median-PF + trade-count, before the cross-asset/neighbour filters): 87
**Survivors** (passed every CRITERIA.md E3(a)-(e) filter): 87

| edition | N tested | pre-candidates | survivors |
|---|---|---|---|
| en | 216 | 20 | 20 |
| ko | 216 | 47 | 47 |
| macro | 270 | 9 | 9 |
| stocks | 108 | 11 | 11 |

**Placebo** (block-bootstrap-by-month noise floor, 20 shuffles): mean 155.20, sd 16.15, per-shuffle counts [150, 172, 150, 152, 168, 176, 167, 159, 166, 173, 135, 177, 151, 117, 145, 134, 149, 137, 160, 166].

## Decision: NO EVIDENCE OF EDGE

87 real survivor(s) does not exceed the placebo noise floor (mean 155.20 + 1 sd 16.15 = 171.35) — this many 'survivors' would be expected by chance alone from testing this many combinations, even with no real edge.

## Private record
**Not written this run** — `EDGE_PASSPHRASE` was unset, so no private file was produced (CRITERIA.md E4: nothing private is ever written without it).

## Parameters (fixed, CRITERIA.md)
- windows: up to 12 x 6-month (min 6 available to be eligible)
- hit rate >= 0.667 | median PF >= 1.2 | trades >= 100
- cross-asset: >= 2 other assets @ hit rate >= 0.5
- neighbour hit-rate fraction >= 0.667
- placebo: 20 shuffles, seed 20260907

