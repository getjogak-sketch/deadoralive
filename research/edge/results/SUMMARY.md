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

**Placebo — block bootstrap by month, whole bars** (PRIMARY, used for the decision below; 20 shuffles): mean 122.75, sd 12.86, per-shuffle counts [118, 136, 129, 132, 122, 137, 139, 116, 128, 108, 110, 135, 118, 91, 113, 113, 121, 135, 114, 140].
**Placebo — circular time-shift** (secondary sanity check, NOT used for the decision; 20 shuffles): mean 121.40, sd 17.43, per-shuffle counts [113, 113, 122, 100, 129, 105, 126, 121, 150, 122, 99, 136, 91, 103, 122, 147, 109, 141, 149, 130]. If the selection rule has no hidden time-specific edge, this should land close to the real count (87), not near the block-bootstrap floor above.

## Decision: NO EVIDENCE OF EDGE

87 real survivor(s) does not exceed the block-bootstrap placebo noise floor (mean 122.75 + 1 sd 12.86 = 135.61) — this many 'survivors' would be expected by chance alone from testing this many combinations, even with no real edge.

## Private record
**Not written this run** — `EDGE_PASSPHRASE` was unset, so no private file was produced (CRITERIA.md E4: nothing private is ever written without it).

## Parameters (fixed, CRITERIA.md)
- windows: up to 12 x 6-month (min 6 available to be eligible)
- hit rate >= 0.667 | median PF >= 1.2 | trades >= 100
- cross-asset: >= 2 other assets @ hit rate >= 0.5
- neighbour hit-rate fraction >= 0.667
- placebo: 20 shuffles, seed 20260907

