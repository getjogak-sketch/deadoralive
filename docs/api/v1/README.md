# Dead or Alive — API v1

Static, read-only JSON. No key, no rate limit beyond normal HTTP caching — these are plain files
served by GitHub Pages, refreshed once a week by the same pipeline that renders the HTML pages.

## Editions

- `en` — English (crypto: BTCUSD, ETHUSD, 1d/4h) (`methodology.html`)
- `ko` — 한국어 (Korean, crypto: KRW-BTC, KRW-ETH, 1d/4h) (`ko/methodology.html`)
- `stocks` — Stocks (SPY, QQQ, 1d) (`stocks/methodology.html`)

## Endpoints (per edition)

- `GET /api/v1/<edition>/latest.json` — the most recent run's full payload (identical in shape to
  this repo's own `results/latest.json` / `results/latest_ko.json` / `results/latest_stocks.json`).
- `GET /api/v1/<edition>/history/index.json` — `{"edition": "...", "history": [{"as_of": "...",
  "file": "history/<as_of>.json"}, ...]}`, newest first.
- `GET /api/v1/<edition>/history/<as_of>.json` — a copy of that edition's payload as of that date.

## Row schema

Every element of `payload["rows"]` carries:

- `strategy_id`: registry id, e.g. "sma_cross" — see the methodology page's strategy table for the full list, including the POPULAR_COMBOS group
- `strategy_name`: human-readable name of the strategy
- `params`: the fixed parameter string for this variant, e.g. "10-50"; "-" for reference rows
- `type`: "state" | "onebar" | "holdN" | "reference" — which engine primitive ran this row
- `asset`: e.g. "BTCUSD", "KRW-BTC", "SPY"
- `timeframe`: "1d" or "4h"
- `edition`: "en" | "ko" | "stocks"
- `as_of`: date (UTC) of the last fully-closed bar this run used, ISO YYYY-MM-DD
- `verdict`: "ALIVE" | "FADING" | "DEAD" | "TOO FEW TRADES" | null (reference rows)
- `is`: object of in-sample metrics — see the methodology page's metric definitions
- `oos`: object of out-of-sample metrics (the ones the verdict is based on), plus fee_drag
- `robustness`: object — {grid: [...], share_pf_ge_1, note}; diagnostic only, added by the parameter-neighbourhood robustness map (see methodology: "Robustness map (diagnostic)"); present on tradeable (non-reference) rows once that extension has run, absent on older history snapshots and on reference rows
- `suspicious`: bool — OOS PF or Sharpe crossed the "investigate, don't trust" threshold

Cost model and verdict-badge thresholds are defined once, by reference, on each edition's
methodology page (see the table above) — they are not repeated as numbers in this document so
this document never goes stale relative to `config.py`, the single source of truth for both.

## Update cadence

Weekly, Monday 00:30 UTC (see `.github/workflows/weekly.yml`) — one run per week, rolling the
in-sample/out-of-sample window forward by a week each time. `workflow_dispatch` can trigger an
out-of-cycle run; `payload["generated_at"]` (UTC, ISO 8601) is the actual wall-clock time of the
run that produced a given snapshot, which may differ from `as_of` (the date of the last
fully-closed price bar that run used).

## Stability

Fields are only ever added, never renamed or removed, and an existing field's meaning is never
changed — a consumer that only reads fields it recognizes keeps working across every future
weekly run. `robustness` above is one such field: it did not exist in the first release of this
API and appears only once the robustness-map extension started running; its absence on an older
`history/<as_of>.json` snapshot is not an error.

## License

- **Data**: sourced from each exchange's/data provider's own public API (Bitstamp, Upbit, Stooq,
  Yahoo Finance) — subject to those providers' own terms, not this project's.
- **Results** (the computed metrics, verdicts, and this JSON structure itself): CC BY 4.0 — reuse
  freely with attribution.

## Legal

Educational / informational content only. This is not investment advice, and nothing here is a recommendation to buy or sell any asset. Past backtested performance, especially net-of-cost historical simulation, does not guarantee future results.

## Example

```python
import requests

data = requests.get("https://getjogak-sketch.github.io/deadoralive/api/v1/en/latest.json").json()
for row in data["rows"]:
    print(row["strategy_id"], row["params"], row["asset"], row["timeframe"], row["verdict"])
```
