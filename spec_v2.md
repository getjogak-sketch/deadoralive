# Spec v2 — "netcheck" v0: 유명 전략 상시 검사기

작업명(임시): **netcheck**. 최종 이름은 Cay가 정함 — 코드에서 이름은 `config.py`의 `PROJECT_NAME` 한 곳에만 둔다.

이 문서는 spec.md(v1)를 **확장**한다. v1의 원칙(§0), 비용 모델(§3), 지표 정의(§5), 엔진 정직성 검증(§6)은 그대로 유효하다. v1 코드(`data_loader.py`, `strategies.py`, `engine.py`, `tests.py`)는 **재작성하지 않고 확장**한다 — 기존 함수 시그니처와 테스트를 깨지 말 것.

## 0. 목표

매주 자동으로: 공개 API에서 가격 데이터를 받아 → 사전 등록된 전략 전부를 BTC/ETH × 1d/4h에 대해 net-of-cost로 백테스트 → 최근 2년(OOS)과 그 이전(IS)으로 나눠 지표 계산 → 판정 배지 부여 → 정적 HTML 페이지 + JSON 갱신 → 리포에 커밋.

## 1. 데이터

- 소스: **Binance 공개 REST** `GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000&startTime=...` (키 불필요). 페이지네이션으로 2017-08-17(BTCUSDT 상장)부터 전부. ETHUSDT 동일.
- `fetch_data.py`: 심볼·인터벌별로 `data/<symbol>_<tf>.csv` (v1 정규화 포맷 `date,open,high,low,close,volume`, UTC) 저장. 이미 파일이 있으면 마지막 날짜 이후만 이어받아 append (증분). 마지막 partial bar는 저장하지 않는다.
- **이 개발 환경에서는 Binance가 차단**되어 있으므로, `fetch_data.py`는 작성만 하고 실행 검증은 `--offline` 플래그로 로컬 파일(`/home/claude/data/btc_1d.csv`, `btc_4h.csv`)을 그대로 쓰는 경로로 한다. 로컬 데이터는 Bitstamp BTCUSD라 실제 운영(Binance BTCUSDT)과 약간 다르다 — README에 명시.
- ETH 로컬 데이터는 없다. 코드는 `config.py`의 `ASSETS = ["BTCUSDT", "ETHUSDT"]`를 돌되, 데이터 파일이 없는 자산은 **경고만 남기고 건너뛴다** (실패 아님).

## 2. 기간 분할 (롤링)

- 실행 시점의 마지막 완성 bar 날짜를 `as_of`라 하면: **OOS = as_of − 730일 ~ as_of**, **IS = 데이터 시작 ~ OOS 시작 전날**.
- 매주 실행되므로 창이 한 주씩 밀린다. 지표·SMA warm-up은 v1과 같이 전체 시리즈로 계산.
- 결과 파일과 페이지에 `as_of` 날짜를 반드시 표시.

## 3. 전략 레지스트리 (`registry.py`) — 파라미터 전부 사전 고정, 추가·수정 금지

공통: long-only, 전액 진입/전액 현금, bar t 종가 시점 결정 → bar t+1 시가 체결 (v1 §4A 방식). 아래 "상태형"은 목표 상태(long/flat)를 매 bar 계산하는 v1 `ma_cross` 틀을 그대로 쓰고, "1-bar형"은 v1 `vol_breakout` 틀을 쓴다. 두 틀로 표현 안 되는 전략은 "보유 N bar형"(진입 후 정해진 bar 수 뒤 시가 청산)을 하나 더 추가한다.

| id | 이름 | 유형 | 규칙 | 파라미터 |
|---|---|---|---|---|
| sma_cross | SMA crossover | 상태형 | fast>slow long | (10,50) (20,100) (50,200) |
| ema_cross | EMA crossover | 상태형 | EMA fast>slow long | (12,26) |
| above_sma | Price above SMA | 상태형 | close>SMA(n) long | n=200, n=50 |
| donchian | Donchian/Turtle breakout | 상태형 | close> 직전 N bar 최고가 → long; close< 직전 M bar 최저가 → flat (둘 다 shift(1)) | (20,10) (55,20) |
| vol_breakout | Larry Williams 변동성 돌파 | 1-bar형 | v1 §4B | k=0.5, 0.7 |
| vol_breakout_trend | 변동성 돌파 + 추세 필터 | 1-bar형 | v1 §4B + 조건 `open[t] > SMA20[t-1]` | k=0.5 |
| rsi_mr | RSI mean reversion | 상태형 | RSI(14)<30 → long; RSI>exit → flat | exit=50, exit=70 |
| rsi2_connors | Connors RSI(2) | 상태형 | RSI(2)<10 AND close>SMA200 → long; close>SMA5 → flat | 고정 |
| bb_mr | Bollinger mean reversion | 상태형 | close<lower(20,2) → long; close>middle → flat | 고정 |
| bb_breakout | Bollinger breakout | 상태형 | close>upper(20,2) → long; close<middle → flat | 고정 |
| macd | MACD signal cross | 상태형 | MACD(12,26,9) line>signal long | 고정 |
| supertrend | Supertrend | 상태형 | Supertrend(10,3) 방향 up → long | 고정 |
| tsmom | Time-series momentum | 상태형 | close > close[n] → long | n=30, n=90 |
| dip_3down | Buy the dip (3 down closes) | 상태형 | 3연속 하락 종가 → long; 첫 상승 종가 → flat | 고정 |
| dip_pct | Buy the dip (−x% bar) | 보유 N bar형 | bar 수익률 ≤ −5% → 다음 시가 진입, N bar 뒤 시가 청산 | N=5 |
| dca_weekly | 참고용: 주간 DCA | 참고 | 매주 첫 bar 시가에 고정 금액 매수, 매도 없음. 지표는 수익률·MDD만 | — |
| buy_and_hold | 참고용 | 참고 | v1 §5 | — |

총 22개 전략 변형 × 자산 2 × TF 2 = 88 조합 (+참고 2×4). RSI·EMA·MACD·Supertrend·Bollinger는 `pandas`로 직접 구현 (라이브러리 의존 없이; 계산식은 표준 정의를 docstring에 적기). 지표 계산은 전부 `indicators.py`에 모으고, **모든 지표는 bar t까지의 데이터만 사용**해야 하며 v1 §6.1 no-lookahead 테스트가 모든 전략에 대해 돌아야 한다.

## 4. 판정 배지 (사전 고정 — 결과 보고 바꾸지 않음)

OOS(최근 2년) net 지표 기준, (전략, 자산, TF)마다:

| 배지 | 조건 |
|---|---|
| ALIVE | OOS PF ≥ 1.2 AND OOS trades ≥ 30 AND OOS MDD < 같은 기간 B&H MDD |
| FADING | 위 미달이지만 OOS PF ≥ 1.0 AND trades ≥ 10 |
| DEAD | OOS PF < 1.0 AND trades ≥ 10 |
| TOO FEW TRADES | OOS trades < 10 |

참고 행(dca, b&h)에는 배지 없음. IS 지표는 맥락으로 나란히 표시.

## 5. 산출물

```
netcheck/
  config.py           # PROJECT_NAME, ASSETS, TIMEFRAMES, COSTS(자산별 편도), OOS_DAYS=730, 배지 임계값
  fetch_data.py       # Binance 증분 수집 (--offline 로컬 모드)
  indicators.py       # SMA/EMA/RSI/BB/MACD/Supertrend/Donchian
  registry.py         # §3 표 그대로 — 전략 id, 유형, 파라미터, 설명 한 줄
  strategies.py       # v1 확장
  engine.py           # v1 확장 (+보유 N bar형, +DCA 참고)
  metrics.py          # v1 compute_metrics 분리 (기존 engine.py에서 이동해도 되지만 import 경로 유지)
  verdict.py          # §4 배지
  run_weekly.py       # 전체 실행 → results/latest.json, results/history/<as_of>.json, docs/index.html, docs/methodology.html
  build_site.py       # JSON → HTML (템플릿은 파이썬 문자열 또는 단일 .html 템플릿; 외부 JS 프레임워크 없음, 인라인 CSS)
  tests.py            # v1 테스트 + 모든 신규 전략에 대한 no-lookahead + 지표 스팟 체크(예: RSI 알려진 값)
  .github/workflows/weekly.yml   # cron: 매주 월요일 00:30 UTC. 단계: checkout → python 3.11 → pip install pandas numpy requests → fetch_data.py → tests.py → run_weekly.py → commit & push (data/, results/, docs/)
  docs/index.html, docs/methodology.html, docs/latest.json(복사본)
  README.md           # 목적, 실행법, 방법론 요약, 로컬/운영 데이터 차이, 단순화 가정, 법적 고지("교육·정보 목적, 투자 조언 아님, 수익 보장 없음")
```

### 페이지(`docs/index.html`) 요구사항
- 상단: 프로젝트명, `as_of`, 한 줄 설명("Popular retail trading strategies, re-tested every week — net of fees, out-of-sample."), 배지 집계(ALIVE n / FADING n / DEAD n / TOO FEW n).
- 본문: 자산·TF 탭 또는 섹션 4개. 각 섹션 표 컬럼: Strategy · Params · Verdict · OOS Return · OOS PF · OOS MDD · OOS Trades · OOS Win% · IS PF · IS MDD · B&H OOS Return · B&H OOS MDD · **Fee drag** (= 같은 OOS 구간에서 비용 0으로 돌린 수익률 − net 수익률; 이 값만 gross를 사용하며 "before fees, for illustration" 라벨).
- 배지 색: ALIVE 초록, FADING 노랑, DEAD 회색, TOO FEW 연회색. 다크/라이트 모두에서 읽혀야 함(시스템 `prefers-color-scheme`).
- 하단: 방법론 링크, 코드 링크(placeholder `REPO_URL` in config), 법적 고지, 이메일 수집 링크 placeholder(`SIGNUP_URL`, 비어 있으면 숨김).
- 영어. 모바일에서 표가 가로 스크롤되게 (`overflow-x:auto`).
- 외부 리소스 로드 없음(폰트·JS·CSS 전부 인라인).

### `docs/methodology.html`
- 비용 모델, IS/OOS 롤링 정의, 체결 규칙, 배지 규칙, no-lookahead 테스트·두 엔진 대조 설명, 전략 표(§3) 그대로, "우리가 하지 않는 것"(파라미터 튜닝, 전략 추가 시 사전 등록 원칙).

## 6. 검증 (필수)

- `tests.py` 전부 통과 (v1 테스트 + 신규 전략 no-lookahead + RSI/EMA/MACD 스팟 체크).
- `crosscheck_bt.py`(v1)를 `sma_cross`에 대해 그대로 재실행해 여전히 일치하는지 확인.
- `run_weekly.py --offline`을 로컬 BTC 데이터로 끝까지 실행해 `docs/index.html`이 생성되고 88(−ETH 스킵) 조합이 표에 나오는지 확인.
- 비현실적으로 좋은 결과(예: OOS PF > 5, 샤프 > 4)가 있으면 버그로 간주하고 원인 보고.

## 7. 하지 말 것

- 전략·파라미터 추가/수정. 배지 임계값 수정. 비용 수정.
- v1 함수 시그니처 변경, v1 테스트 삭제.
- 외부 백테스트 라이브러리를 메인 엔진으로 교체(대조용 backtesting.py만).
- 데이터 합성.
