# Spec v3 — Dead or Alive: 5 extensions (pre-registered, additive)

Extends spec_v2. All v1/v2 rules stand: net-of-cost everywhere, no-lookahead test for every strategy, thresholds/costs only in config.py, no parameter tuning, no data fabrication. Everything here is ADDITIVE — do not rewrite existing modules or change existing outputs' numbers. Implement in the order A → B → C → D → E, committing after each (separate commits, tests green at each commit). Korean strings you add must read as natural Korean (합쇼체, no translationese, no 추천/수익 보장/확실/필승).

## A. Stocks edition (English only) — SPY, QQQ, daily

- New edition `stocks`: assets `SPY`, `QQQ`, timeframe `1d` only. Output `docs/stocks/index.html` + `docs/stocks/methodology.html` (English; reuse the English builders with an edition parameter). `results/latest_stocks.json`, `results/history/stocks_<as_of>.json`.
- Data: Stooq daily CSV, no key: `https://stooq.com/q/d/l/?s=spy.us&i=d` (columns Date,Open,High,Low,Close,Volume). Full history each run is fine (small). If Stooq fails (non-200, empty, or HTML), try Yahoo chart JSON `https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=max&interval=1d` as fallback; if both fail, skip with a warning (never fail the run). Drop today's bar if the US market session is not over (bar date == today UTC).
- DATA_START for stocks: 2000-01-01. Cost 0.02% one-way. bars_per_year 252. Same registry (all 22 variants) and same badge thresholds.
- Language switch links: English (crypto) ↔ Stocks ↔ 한국어.
- Not part of the Korean edition (regulatory choice; do not add a Korean stocks page).

## B. Machine-readable API (static)

- `docs/api/v1/<edition>/latest.json` for editions en, ko, stocks — the same payload as results/latest_*.json. `docs/api/v1/<edition>/history/index.json` listing available as_of dates and file names, plus `docs/api/v1/<edition>/history/<as_of>.json` copies.
- `docs/api/v1/README.md` (also rendered as `docs/api/index.html`, English): schema (every field of a row: strategy_id, params, asset, timeframe, edition, as_of, verdict, is{...}, oos{...}, robustness{...} from C), update cadence, cost/badge definitions by reference to methodology, license (data: exchange public APIs; results: CC BY 4.0), the same legal disclaimer, and a 5-line Python `requests` example.
- Keep JSON stable: add fields, never rename.

## C. Robustness map (parameter neighbourhood) — diagnostic only

- For every non-reference registry variant with ≥1 numeric parameter, evaluate OOS net Profit Factor on a small grid of neighbouring parameter values: for each numeric param p, the three values {round(p×0.75), p, round(p×1.25)} (min 2, and for MA pairs keep fast < slow; for k-type floats use p−0.1, p, p+0.1 with min 0.1). 1 param → 3 runs, 2 params → 9 runs. Reuse the existing engine functions unchanged; this is a read-only diagnostic and MUST NOT feed the verdict.
- Store per row: `robustness: {"grid": [{"params": "...", "oos_pf": x, "oos_n_trades": n}, ...], "share_pf_ge_1": fraction of grid points with OOS PF ≥ 1.0 and n_trades ≥ 10, "note": "diagnostic; verdict uses registered params only"}`.
- Page: add a small "Robustness" column showing `share_pf_ge_1` as e.g. "7/9" with a tooltip listing the grid (params → PF); colour 3 levels (≥ 2/3 green-ish, 1/3–2/3 amber, < 1/3 grey). Methodology: a short section "Robustness map (diagnostic)" explaining that a strategy that only works at exactly its registered numbers is probably a coincidence; state plainly that the grid never changes the verdict and never selects parameters. Korean edition column title "주변 설정값 안정성", section "주변 설정값 안정성 (참고용)" in natural Korean.
- Performance: the grid must run inside the weekly Action within ~10 minutes total; if it is slower, reduce to 1-D grids per param (3+3 instead of 9) and say so in README.

## D. "Popular combos" registry group — as commonly taught (pre-registered NOW)

Add a second registry group `POPULAR_COMBOS` (rendered on all pages as its own table titled "Popular combos — as commonly taught on YouTube / TradingView", Korean "유튜브·트레이딩뷰에서 많이 가르치는 조합 전략"). Same execution rules as spec_v2 (state at close t → fill at open t+1; long-only; all-in). All parameters are fixed here and must not be changed:

| id | name | rule | params |
|---|---|---|---|
| ema_9_21 | EMA 9/21 crossover | EMA9 > EMA21 → long, else flat | fixed |
| ema200_macd | EMA200 trend + MACD cross | long when close > EMA200 AND MACD(12,26,9) line > signal; flat when MACD line < signal OR close < EMA200 | fixed |
| rsi_uptrend | RSI dip in uptrend | long when RSI(14) < 30 AND close > SMA200; flat when RSI(14) > 70 OR close < SMA200 | fixed |
| bb_squeeze | Bollinger squeeze breakout | bandwidth = (upper−lower)/middle with BB(20,2); "squeeze" if bandwidth ≤ min(bandwidth over prior 120 bars). Long when squeeze was true on any of the prior 5 bars AND close > upper; flat when close < middle | fixed |
| ichimoku_cloud | Ichimoku cloud breakout | tenkan(9), kijun(26), senkou A = (tenkan+kijun)/2 shifted +26, senkou B = (52-high+52-low)/2 shifted +26 (i.e. cloud value at bar t was computed from bars ≤ t−26 — no lookahead). Long when close > max(A,B); flat when close < min(A,B); hold state in between | fixed |
| heikin_ashi_trend | Heikin-Ashi colour | compute HA candles from the raw OHLC; long after 2 consecutive HA close > HA open; flat on first HA close < HA open | fixed |
| supertrend_ema200 | Supertrend + EMA200 filter | long when Supertrend(10,3) is up AND close > EMA200; flat when Supertrend flips down | fixed |

Every new strategy gets the no-lookahead truncation test like all others (the ichimoku shift and the HA recursion are the two places a lookahead bug is most likely — test them explicitly). Add Korean names/rules in the same natural style as the existing RULE_KO / STRATEGY_NAME_KO tables. Robustness grid (C) applies where numeric params exist (treat fixed constants like 9/21, 200, 120, 5 as the numeric params for the grid — grid only, never changing the registered values).

## E. "Check my strategy" via GitHub Issues (free, automated, no server)

- Issue form `.github/ISSUE_TEMPLATE/check-strategy.yml` (title prefix `[check]`, label `check`): fields — edition (dropdown en/ko/stocks), asset (dropdown of that edition's assets; keep as a single dropdown listing all assets with the edition prefix), timeframe (dropdown 1d/4h), strategy type (dropdown of registry ids incl. POPULAR_COMBOS, ex-reference), params (text, format `name=value, name=value`; leave empty for fixed strategies), and a required checkbox "I understand this is an automated educational backtest, not investment advice".
- Workflow `.github/workflows/check.yml` on `issues: [opened, labeled]` with label `check`: parse the form body; validate strictly (known strategy id, params numeric, within sane bounds: window lengths 2..500, k 0.1..2.0, fast<slow); on invalid input comment the exact problem and close. On valid input: load committed data/ files (no fetch), run the single variant with the same engine/cost/thresholds, and post ONE comment containing: the scorecard (IS and OOS metrics, badge), the robustness grid from C, the fee-drag number, a link to methodology, and the legal disclaimer (English for en/stocks, the Korean verbatim disclaimer for ko). Then close the issue with label `done`. Permissions: issues: write, contents: read. Limit runtime: one variant per issue; ignore issues without the `check` label. Guard against injection: never eval user text; parse with a strict regex.
- Add a "Check your own strategy" link (Korean: "내 전략도 검사해 보기") on all pages pointing to the new-issue URL with the template (`{REPO_URL}/issues/new?template=check-strategy.yml`). The Korean link text must not imply advice or guarantees.
- Test: a unit test for the parser/validator with 5 good and 5 bad inputs; and a dry-run script `python check_issue.py --body sample.md --dry-run` that prints the comment markdown without touching GitHub.

## Deliverables / reporting

- Commits: one per section A–E, each with tests passing and `run_weekly.py --offline` producing all pages. Commit as Cay <cayleepak@gmail.com>, each message ending with the two trailer lines:
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Y85LzfYHeAA7MgWxN3v1vZ
- Never commit data/*.csv.
- At the end: `git format-patch origin/main..HEAD -o /mnt/user-data/outputs/v3/` is NOT possible (no origin here) — instead run `git format-patch -5 -o /mnt/user-data/outputs/v3/` (adjust the count to the number of commits you made) and report the file list, test counts, robustness runtime measured locally (BTC only), and anything you could not verify because exchange/finance APIs are blocked here (Stooq/Yahoo reachability is unknown until CI runs).
