# Backtest Spec v1 — 첫 그물 2개 검증

이 문서는 구현자가 그대로 코드로 옮기는 명세다. 여기 적힌 파라미터·기준은 결과를 보기 전에 확정된 것이며, 구현 중 임의로 바꾸지 않는다.

## 0. 원칙

- 수수료·슬리피지를 뺀 숫자는 어디에도 출력하지 않는다. 모든 지표는 비용 반영 후(net)다.
- 파라미터 조합은 아래 명시된 것만 돌린다. 추가 조합, 추가 필터, "조금만 바꿔보기" 금지.
- Lookahead(미래 정보 누출) 방지: bar t 종료 시점에 알 수 있는 정보로만 bar t+1의 행동을 결정한다. 모든 신호 계산에 `.shift(1)` 또는 동등한 명시적 처리를 쓰고, 이를 테스트로 증명한다 (§6).
- 데이터를 합성·보정·삭제하지 않는다 (마지막 partial bar 제거만 예외).

## 1. 데이터

`/home/claude/data/` 의 정규화된 CSV (`date,open,high,low,close,volume`, ascending).

| 파일 | 자산 | 봉 | 비고 |
|---|---|---|---|
| btc_1d.csv | BTC/USD (Bitstamp) | 1d, UTC 00:00 시작 | Upbit 일봉(KST 09:00)과 경계 동일 |
| btc_4h.csv | BTC/USD | 4h, UTC | |
| spy_1d.csv | SPY | 1d | 2026-03-20까지 |

- **마지막 행(2026-09-06, partial bar)은 BTC 두 파일 모두 제거**한다.
- 지표 warm-up(예: SMA 200)은 테스트 시작일 이전 데이터를 사용해 계산한다. 즉 신호 계산은 전체 데이터로 하고, 거래·성과 집계만 아래 기간으로 자른다.

## 2. 기간 분할 (in-sample / out-of-sample)

| 자산 | In-sample (IS) | Out-of-sample (OOS) |
|---|---|---|
| BTC (1d, 4h) | 2017-01-01 ~ 2024-09-05 | 2024-09-06 ~ 2026-09-05 |
| SPY (1d) | 2000-01-03 ~ 2024-03-20 | 2024-03-21 ~ 2026-03-20 |

IS와 OOS는 **각각 독립적으로** equity를 1.0에서 다시 시작해 집계한다. (OOS 시작 시점에 이미 포지션 중이었다면 OOS 첫 bar open에 진입한 것으로 처리한다 — 단순화, README에 명시.)

## 3. 비용 (편도, 체결가에 비율로 적용)

| 자산 | 편도 비용 | 근거 |
|---|---|---|
| BTC | 0.10% | Upbit 수수료 0.05% + 슬리피지 0.05% |
| SPY | 0.02% | 수수료 0 + 스프레드/슬리피지 |

매수 체결가 = 기준가 × (1 + cost), 매도 체결가 = 기준가 × (1 − cost).

## 4. 전략 정의

공통: long-only, 항상 "전액 보유" 또는 "전액 현금" 둘 중 하나. 레버리지·공매도 없음. 부분 체결 없음.

### 4A. MA Crossover (`ma_cross`)

- `fast = SMA(close, n_fast)`, `slow = SMA(close, n_slow)` — bar t의 close까지 포함해 계산.
- 신호(bar t 종료 시 결정): `fast[t] > slow[t]` 이면 목표 상태 = long, 아니면 flat.
- 실행: 목표 상태가 현재 상태와 다르면 **bar t+1의 open**에서 체결.
- 파라미터 조합 (고정, 3개): **(10, 50), (20, 100), (50, 200)**. 1d·4h 동일하게 "bar 개수" 기준.
- 진입 이전 warm-up 구간(slow SMA가 NaN)에서는 flat.

### 4B. Volatility Breakout (`vol_breakout`)

Larry Williams 변동성 돌파. 각 bar를 독립된 "하루"로 취급.

- `range_prev[t] = high[t-1] − low[t-1]`
- `target[t] = open[t] + k × range_prev[t]`
- 진입 조건: `high[t] >= target[t]`
- 진입 체결 기준가: `max(open[t], target[t])` (open이 이미 target 위면 open에서 체결 — 갭 상승 보수 처리). 여기에 매수 비용 적용.
- 청산: **bar t+1의 open**에서 무조건 매도 (매도 비용 적용). 보유 기간은 항상 1 bar.
- 연속 발동 시(bar t, t+1 모두 조건 충족) bar t+1 open에서 매도 후 같은 bar 내에서 target[t+1]에 다시 매수 — 별개의 두 거래, 비용 각각 부과.
- 파라미터 (고정, 2개): **k = 0.5, 0.7**.
- 1d와 4h 모두 동일 규칙 적용.

## 5. 지표 (모두 net)

각 (asset, timeframe, strategy, params, period∈{IS, OOS}) 조합마다:

| 컬럼 | 정의 |
|---|---|
| total_return | equity 최종/최초 − 1 |
| cagr | 연환산 수익률 |
| bh_return | 같은 기간 buy & hold (기간 첫 bar open 매수 → 마지막 bar close, 비용 한 번씩 적용) |
| n_trades | 완결된 거래(매수→매도) 수. 기간 끝에 보유 중이면 마지막 close에 강제 청산해 1건으로 셈 |
| win_rate | 수익 거래 비율 |
| profit_factor | Σ(양의 거래손익) / |Σ(음의 거래손익)| — 손익은 equity 단위(비율) |
| mdd | 전략 equity 곡선의 최대 낙폭 (양수 %로 표기) |
| bh_mdd | buy & hold의 최대 낙폭 |
| sharpe | per-bar 전략 수익률의 mean/std × √(bars_per_year). bars_per_year: BTC 1d=365, BTC 4h=365×6, SPY 1d=252. 무위험 0 |
| exposure | 보유 중이었던 bar 비율 |
| max_consec_loss | 최대 연속 손실 거래 수 |
| avg_trade_return | 거래당 평균 수익률 |

## 6. 엔진 정직성 검증 (필수, 결과보다 중요)

1. **No-lookahead test**: 각 전략에 대해, 전체 데이터로 계산한 목표 상태/신호 시퀀스와, 데이터를 임의의 날짜 T까지 잘라서 계산한 시퀀스가 T−1까지 완전히 동일함을 assert. 최소 3개의 T로 검사. 실패하면 결과를 출력하지 말고 원인을 보고.
2. **두 엔진 대조 (ma_cross만)**: pandas 자체 구현과 `backtesting.py` 라이브러리(`pip install backtesting`) 구현의 결과를 같은 데이터·같은 비용으로 비교. backtesting.py 설정: `cash` 충분히 크게, `commission=cost`, `exclusive_orders=True`, `trade_on_close=False`(신호 다음 bar open 체결 — 기본값). 비교 항목: n_trades(동일해야 함), total_return(상대 오차 2% 이내). 차이가 있으면 원인을 README에 설명 (예: backtesting.py는 정수 주 단위 체결).
3. **Sanity**: 비용을 0으로 놓고 돌린 값이 비용 반영 값보다 항상 좋은지 (당연하지만 부호 실수 잡기). 이 무비용 값은 검증에만 쓰고 출력 표에는 넣지 않는다.

## 7. 통과 기준 (사전 확정 — 판정은 사람이 함, 코드는 True/False 컬럼만 계산)

| 기준 | IS | OOS |
|---|---|---|
| n_trades ≥ 100 | ✓ | — |
| profit_factor ≥ 1.3 | ✓ | ≥ 1.1 |
| mdd ≤ 30% | ✓ | — |
| sharpe ≥ 0.8 | ✓ | — |
| mdd < bh_mdd | ✓ | — |

`pass_is`, `pass_oos`, `pass_all` boolean 컬럼을 summary에 추가.

## 8. 산출물

```
/home/claude/bt/
  data_loader.py       # CSV 로드, partial bar 제거, 기간 분할
  strategies.py        # ma_cross, vol_breakout — 신호/체결 로직 (pandas)
  engine.py            # equity 계산, 거래 기록, 지표
  crosscheck_bt.py     # backtesting.py 대조
  tests.py             # §6 검증 — 실행 시 모두 통과해야 함
  run_all.py           # 전부 실행 → results/
  results/summary.csv  # 모든 조합 × 기간, §5 컬럼 + §7 boolean
  results/summary.md   # 사람이 읽는 표 (IS/OOS 나란히)
  results/trades_<asset>_<tf>_<strategy>_<params>.csv   # 거래 단위 기록
  results/equity_<asset>_<tf>_<strategy>_<params>.csv   # bar 단위 equity (IS+OOS 연결 아님, period 컬럼 포함)
  README.md            # 실행 방법, 검증 결과(§6), 단순화 가정 목록
```

총 조합 수: BTC 1d(5) + BTC 4h(5) + SPY 1d(5) = 15 조합 × 2 기간 = 30 행.
