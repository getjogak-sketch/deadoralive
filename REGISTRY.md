# Strategy registry — pre-registration rules

This document is the rulebook behind `registry.py`, `registry_ledger.json`, and the public page
`docs/registry.html`. It exists so that "Dead or Alive" can be trusted the same way a pre-registered
clinical trial can be trusted: **the exact thing being tested, and the exact fixed numbers it runs
at, are written down and dated before any result is computed** — not chosen, tuned, or rewritten
after seeing how well they performed. A trial that lets the researcher pick the endpoint after
seeing the data isn't a trial; a backtest that lets the operator pick the parameters after seeing
the equity curve isn't a backtest, it's curve-fitting with extra steps. Pre-registration is the one
mechanical thing that stops that from happening here, and it only works if it is public and dated.

## The rule

1. **A strategy variant enters the registry with:**
   - its exact rule text (the entry condition, the exit condition, and how ties/edge cases are
     handled — in plain English, matching `registry.py`'s `"rule"` field verbatim),
   - its fixed parameters (every number the rule depends on — window lengths, thresholds,
     multipliers — as a `{name: value}` map, matching `registry.py`'s `variant["params"]`),
   - the date it was registered (`registered_on`),
   - the git commit that added it (`registered_commit`) — anyone can check out that commit and see
     the rule text/params exactly as registered, with no later edit possible,
   - where the rule came from (`source`: a standard textbook indicator, a combination commonly
     taught on YouTube/TradingView, or a strategy proposed by the community — see below).
2. **No edits after registration.** Once an entry exists in `registry_ledger.json`, its
   `params`, `rule_text`, `registered_on`, and `registered_commit` never change — not to fix a
   typo in the rule description, not to nudge a parameter that "obviously" should have been
   slightly different, not for any reason. `tests.py` enforces this mechanically: it compares the
   live ledger against a checked-in snapshot (`registry_ledger.snapshot.json`) and fails the whole
   test suite — which blocks every weekly run (`run_weekly.py` refuses to write results if
   `tests.py` fails) — if any existing entry's fields differ from that snapshot.
3. **Corrections are new entries.** If a rule genuinely needs to change — a bug in how it was
   implemented, a parameter that was mis-transcribed from the source it claims to teach — the fix
   is a **new** ledger entry with a new `id`, a new `registered_on` date, and (ideally) a note in
   its `rule_text` referencing what it replaces. The old entry, and every verdict it ever produced,
   stays in the ledger and in `results/history/` untouched. The site is allowed to say "we were
   wrong about X"; it is never allowed to make X quietly disappear or retroactively look like it
   always said something else.
4. **Verdicts are computed only from what's in the ledger.** `verdict.py` and every number shown
   for a strategy come from running the exact registered rule at the exact registered parameters
   against real market data — never from the "Robustness map" diagnostic grid (which explicitly
   never selects parameters or changes a verdict; see `docs/methodology.html`), and never from a
   parameter chosen after looking at how it would have performed.

## Sources

| `source` | Meaning |
|---|---|
| `textbook` | A standard single-indicator strategy as commonly described in trading textbooks/references (the original `REGISTRY` table — SMA/EMA crossovers, RSI mean reversion, Bollinger Bands, MACD, Supertrend, Donchian/Turtle breakout, etc.). |
| `popular_combo` | A multi-indicator combination as it is commonly taught in retail trading content on YouTube/TradingView (the `POPULAR_COMBOS` table). |
| `community` | A strategy proposed by a reader via the "Propose a strategy" issue template (see below), accepted and registered before any result was computed for it. None yet — this row exists in the schema for when the first one is accepted. |

## Proposing a strategy

Open a ["Propose a strategy"](../../issues/new?template=propose-strategy.yml) GitHub issue with
the exact rule text, the fixed parameters, and (if applicable) a link to where the strategy is
taught. This is **not automated** — it is a queue a maintainer reads. An accepted proposal is
registered (added to `registry.py` and `registry_ledger.json` with `source: "community"`, dated
the day it's added) **before any backtest is run for it** — the same pre-registration rule as
every other entry. A rejected or still-pending proposal never appears on the results pages; there
is no way for a strategy to show up on this site with a result already attached to it the first
time anyone sees it.

## 등록 규칙 (한국어)

이 문서는 `registry.py`, `registry_ledger.json`, 공개 페이지 `docs/ko/registry.html`의 근거가
되는 규칙을 설명합니다. 목적은 임상시험을 사전 등록하는 것과 같습니다 — **무엇을 검증할지, 어떤
고정값으로 검증할지를 결과를 보기 전에 미리 문서로 남겨 두는 것**입니다. 결과를 본 뒤에 판정 기준을
바꿀 수 있는 임상시험이 신뢰받지 못하듯, 수익 곡선을 본 뒤에 설정값을 바꿀 수 있는 백테스트도
신뢰할 수 없습니다. 사전 등록은 이를 기계적으로 막는 유일한 장치이며, 공개되고 날짜가 기록되어
있어야만 의미가 있습니다.

1. **전략이 등록될 때 함께 기록하는 것**: 정확한 규칙 문구(진입·청산 조건), 고정된 설정값, 등록일
   (`registered_on`), 등록된 커밋 해시(`registered_commit` — 이 커밋을 그대로 열어 보면 등록 당시
   규칙과 설정값을 확인할 수 있고, 이후 수정될 수 없습니다), 출처(`source`: 표준 지표인
   "textbook", 유튜브·트레이딩뷰에서 흔히 가르치는 조합인 "popular_combo", 커뮤니티 제안인
   "community").
2. **등록 후에는 수정하지 않습니다.** `registry_ledger.json`에 한 번 들어간 항목의 설정값·규칙
   문구·등록일·등록 커밋은 이후 절대 바꾸지 않습니다 — 오타 수정이든, "이 값이 더 맞는 것 같다"는
   이유든 마찬가지입니다. `tests.py`가 이를 기계적으로 검사합니다: 현재 원장을 체크인된 스냅샷
   (`registry_ledger.snapshot.json`)과 비교해서, 기존 항목의 값이 하나라도 다르면 전체 테스트가
   실패하고, 그러면 `run_weekly.py`는 결과를 아예 내보내지 않습니다.
3. **정정이 필요하면 새 항목을 만듭니다.** 규칙에 정말 문제가 있었다면(구현 오류, 출처를 잘못
   옮겨 적은 설정값 등) 새로운 `id`와 새로운 등록일로 별도 항목을 추가합니다. 기존 항목과 그동안의
   판정 기록은 `results/history/`에 그대로 남습니다. "그때는 틀렸다"고 밝힐 수는 있어도, 조용히
   지우거나 원래 다른 말을 한 것처럼 바꾸는 일은 하지 않습니다.
4. **판정은 원장에 등록된 값으로만 계산합니다.** "주변 설정값 안정성" 참고 표는 판정에 전혀
   반영되지 않으며, 결과를 본 뒤 더 나아 보이는 값으로 설정값을 바꾸는 일도 없습니다.

### 전략 제안하기

["전략 제안하기"](../../issues/new?template=propose-strategy.yml) 이슈 양식으로 정확한 규칙
문구와 고정 설정값, 가능하면 출처 링크를 남겨 주세요. 자동으로 처리되지는 않으며, 운영자가 검토하는
대기열입니다. 채택된 제안은 결과를 계산하기 전에 먼저 `source: "community"`로 등록하고, 등록된
날짜부터 다른 전략과 똑같은 방식으로 매주 검사합니다. 검토 중이거나 반려된 제안은 결과 페이지에
전혀 나타나지 않습니다 — 처음 보는 순간 이미 결과가 붙어 있는 전략이란 있을 수 없습니다.
