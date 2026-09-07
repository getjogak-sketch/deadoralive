# Attention spike persistence — results (2026-09-07)

Universe: 3000 articles sampled from 25873 (top-1000 monthly lists, 2016-07-01..2026-08-31); fetched OK: 2941.

**Verdict (k=5, per CRITERIA.md): HOLD** — neither GO nor NO-GO thresholds met

| key | N | med R_3 | med R_7 | med R_14 | R_7≥3 share | peak after t | med half-life (d) | half-life≤1 |
|---|---|---|---|---|---|---|---|---|
| k3/IS/all | 24342 | 2.60 | 2.17 | 1.87 | 35% | 38% | 2 | 42% |
| k3/IS/sudden | 12505 | 2.08 | 1.78 | 1.56 | 24% | 31% | 2 | 49% |
| k3/IS/ramp | 11837 | 3.27 | 2.80 | 2.38 | 47% | 44% | 2 | 34% |
| k3/OOS/all | 14569 | 2.87 | 2.34 | 1.98 | 38% | 40% | 2 | 36% |
| k3/OOS/sudden | 7006 | 2.35 | 1.93 | 1.67 | 28% | 35% | 2 | 42% |
| k3/OOS/ramp | 7563 | 3.44 | 2.87 | 2.39 | 48% | 45% | 2 | 29% |
| k5/IS/all | 16031 | 3.64 | 2.89 | 2.37 | 48% | 34% | 2 | 48% |
| k5/IS/sudden | 6915 | 2.81 | 2.26 | 1.91 | 36% | 28% | 1 | 56% |
| k5/IS/ramp | 9116 | 4.46 | 3.58 | 2.91 | 58% | 39% | 2 | 41% |
| k5/OOS/all | 9740 | 4.13 | 3.17 | 2.54 | 52% | 37% | 2 | 41% |
| k5/OOS/sudden | 3967 | 3.23 | 2.48 | 2.03 | 41% | 32% | 2 | 49% |
| k5/OOS/ramp | 5773 | 4.83 | 3.82 | 3.00 | 60% | 41% | 2 | 36% |
| k10/IS/all | 8709 | 6.35 | 4.66 | 3.52 | 67% | 32% | 1 | 51% |
| k10/IS/sudden | 3069 | 4.71 | 3.41 | 2.66 | 55% | 25% | 1 | 59% |
| k10/IS/ramp | 5640 | 7.34 | 5.47 | 4.11 | 73% | 36% | 2 | 47% |
| k10/OOS/all | 5303 | 6.98 | 5.00 | 3.73 | 70% | 34% | 2 | 46% |
| k10/OOS/sudden | 1733 | 5.56 | 3.93 | 3.01 | 60% | 29% | 1 | 52% |
| k10/OOS/ramp | 3570 | 7.65 | 5.58 | 4.17 | 75% | 37% | 2 | 44% |

R_H = mean views over days t+1..t+H divided by the pre-spike 30-day median baseline.
peak after t = share of events whose 14-day maximum comes after the spike day (i.e. acting on t+1 was not too late).
half-life = first day after t on which views fall below 50% of the spike-day views (31 = never within 30 days).
