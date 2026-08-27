# Round 10 — 실패구간 클러스터링과 독립 단기 엣지 사전등록

작성일: 2026-08-21
범위: 연구 백테스트 전용. production/demo/live 변경 없음.

## 1. 목적

Round 9의 Core+Swing 조합은 거래 228건으로 빈도를 높여 +508.36%를 만들었지만,
추정 intra-MDD가 37.48%이고 2021 holdout은 -4.11%였다. 이번 라운드는 거래
빈도를 무작정 늘리지 않고, 기존 전략이 반복적으로 실패한 진입 환경을 유형화해:

1. 기존 레인의 손실을 줄일 보완장치가 있는지 확인하고,
2. 그 환경을 반대로 이용하는 독립 단기 엣지가 있는지 검증한다.

## 2. 룩어헤드 차단

- 학습 구간: 2022-01-01~2023-12-31.
- 고정 검증 구간: 2024-01-01~2025-12-31.
- 군집은 학습 구간의 손실 거래만으로 적합한다.
- 군집 선택·보완장치 선택은 학습 구간에서만 수행한다.
- 검증 구간 결과를 본 뒤 군집 수, 피처, 임계값, 보유시간을 바꾸지 않는다.
- 모든 피처는 진입시각 전에 완결된 봉·공개된 펀딩·OI만 사용한다.

## 3. 입력 거래

- 현행 main 엔진의 실제 position-lifecycle `TradeLog`.
- 고정된 Swing Lane B 거래.
- `r_multiple`은 비용 포함 net R.
- Round 9과 동일하게 반대방향 동시 신규 진입은 별도 포트폴리오 단계에서 차단한다.

## 4. PIT 피처

방향에 맞춰 부호를 정렬한다. 양수는 대체로 진입 방향과 같은 환경이다.

1. `ts_4h`: `abs(MA10-MA35)/ATR14`
2. `ts_1d`: 일봉 추세강도
3. `extension_4h`: `side × (close-MA35)/ATR14`
4. `ret_24h`: 방향 정렬 24시간 수익률
5. `atr_ratio_4h`: `ATR14/close`
6. `volume_ratio_4h`: 현재 완결 4h 거래량 / 이전 20봉 중앙값
7. `range_ratio_4h`: `(high-low)/ATR14`
8. `funding_z`: 현재 펀딩의 직전 180개 대비 robust z-score. 숏은 부호 반전
9. `oi_change_6h`: 최근 6시간 OI 변화율. 방향과 무관한 crowd unwind 피처
10. `lane_swing`: swing=1, core=0

결측은 학습구간 중앙값으로 대체한다. 스케일은 학습 손실 거래의 median/IQR로
고정하고 검증구간에는 재적합하지 않는다.

## 5. 클러스터링

- 알고리즘: NumPy 결정적 k-means.
- `k=4` 고정. elbow/silhouette로 k를 재선택하지 않는다.
- 초기 중심: median에서 가장 먼 점 1개, 이후 farthest-point 순차 선택.
- 최대 100회, 중심 이동 `1e-8` 미만 종료.
- 학습 손실 거래에 적합한 중심으로 학습의 모든 거래와 검증 거래를 할당한다.

## 6. 유해 군집 선택

학습구간 전체 거래를 중심에 할당한 뒤 다음을 모두 만족한 군집만 유해 후보로
고정한다.

- 거래수 `n >= 12`
- net PF `< 0.90`
- 평균 R `< -0.15R`
- 2022와 2023 양쪽에 표본 존재

후보가 없으면 보완장치와 반전 레인을 만들지 않고 라운드를 종료한다.

## 7. 유형별 보완 후보

### A. 차단

유해 군집에 할당된 기존 신규 진입을 전부 건너뛴다.

### B. 50% 축소

유해 군집의 account return과 stop heat를 0.5배로 줄인다. 신호·청산은 불변이다.

### C. 반대방향 단기 레인

유해 군집 신호가 발생하면 기존 포지션을 건드리지 않고 연구용 반대방향 이벤트를
만든다.

- 신호: 유해 군집으로 분류된 기존 신규 진입
- 체결: 다음 30분 봉 시가 taker
- 방향: 기존 신호의 반대
- 손절: 30m ATR14 × 1.5
- 목표: 1.5R
- 최대 보유: 24개 30m 봉(12시간)
- 한 봉에 stop과 target 동시 도달: stop 우선
- 왕복 taker 수수료 + 기존 SL slippage 반영
- 거래당 위험 0.5%
- 동시 1건, 종료 후 12시간 cooldown

## 8. 검증 합격 기준

### 기존 레인 보완장치

2024~2025에서 다음을 모두 만족해야 한다.

- PF가 무보완 대비 개선
- closed MDD가 악화되지 않음
- 총수익 감소가 10% 상대 이내, 또는 총수익 증가
- 거래수 감소 30% 미만
- 2024와 2025 각각 손익이 기존보다 악화되지 않음

### 반대방향 단기 레인

- 검증 거래수 `>= 20`
- PF `> 1.30`
- 평균 R `> +0.15R`
- 2024와 2025 각각 양수
- 기존 Core+Swing 월수익 상관 `< 0.60`

하나라도 실패하면 별도 레인으로 승격하지 않는다.

## 9. Round 9 +500% 포트폴리오 결합 기준

통과 후보만 `g=2.75` 공격적 통제형에 결합한다.

- 총수익 `>= 500%`
- PF `>= 1.5`
- closed MDD `< 25%` 또는 기존 24.98%보다 개선
- heat `<= 10%`, gross `<= 8x`
- 2021 holdout 손실이 기존 -4.11%보다 악화되지 않음

## 10. 금지 사항

- 검증 결과를 본 뒤 k, 피처, 군집 선택 임계값, 단기 레인 stop/target/hold를 변경하지 않는다.
- 손실 거래의 사후 MFE/MAE를 군집 입력으로 쓰지 않는다.
- LLM 해석을 주문 경로 또는 클러스터 할당에 넣지 않는다.
- 통과 전 production/demo/live 상수를 변경하지 않는다.

## 11. 실행 결과 (2026-08-21)

### 11.1 엄격 사전등록 판정

학습구간 손실 거래에 4개 중심을 적합했으나 두 군집은 전체 할당 표본이 1건으로
퇴화했다. 의미 있는 군집은 C1과 C3 두 개였다.

- C1 — 강추세 말단·과밀 펀딩:
  - 학습: n=14, PF 0.738, 평균 -0.146R
  - 검증: n=22, PF 0.430, 평균 -0.377R
  - 중앙 진입환경: 4h 추세강도 2.11, 방향 확장 2.99ATR, 방향정렬 펀딩 z 1.67
- C3 — 정상 혼합 전환:
  - 학습: n=98, PF 3.167, 평균 +0.772R
  - 검증: n=92, PF 2.302, 평균 +0.505R

C1은 평균 R 사전 기준 `< -0.15R`을 0.004R 차이로 통과하지 못했다. 따라서
엄격한 `harmful_clusters`는 빈 집합이며, 사전등록 A/B/C 후보는 자동 기각했다.
이 경계를 검증 결과를 본 뒤 완화하지 않았다.

### 11.2 C1 사후 진단 — 승격 불가

C1이 검증에서 명확히 악화된 뒤 유형별 처방을 진단했다. 아래 수치는 가설 생성용,
production 근거가 아니다.

- C1 전체 차단:
  - 검증 수익 +97.43% → +127.32%, PF 1.538→1.824
  - MDD 19.49%→20.76%, 2025 수익 10.65%→8.49%로 사전 기준 실패
- C1 50% 축소:
  - 검증 수익 +112.65%, PF 1.667
  - MDD 20.10%, 2025 수익 9.81%로 실패
- C1 반대방향 12h 레인:
  - 15건, -1.07%, PF 0.783, 평균 -0.140R
  - 2024 -1.25%, 2025 +0.18%; 명확히 기각

해석: C1은 반대로 베팅할 단기 엣지가 아니라, 기존 방향을 너무 늦게 추가하는
피라미딩 품질 문제다.

### 11.3 C1 추가 피라미딩만 중단 — forward shadow 후보

초기 core 진입과 Swing Lane B는 그대로 유지하고, C1으로 분류된 core의
40/30/30 중 추가 30% 트랜치만 중단했다.

- 2024~2025 고정 검증:
  - 수익 +97.43% → +119.43%
  - PF 1.538 → 1.673
  - closed MDD 19.49% → 19.49% (동일)
  - 2024 78.43%→91.50%, 2025 10.65%→14.59%
  - 114건 중 8건만 제거
- 2022~2025 `g=2.75` 전체 진단:
  - +508.36% → **+605.38%**
  - CAGR 57.49% → 63.46%
  - PF 1.697 → 1.820
  - closed MDD 24.98% → 22.36%
  - 추정 intra-MDD 37.48% → 33.54%
  - 거래 228→217
- 사용하지 않은 방향성 holdout:
  - 2021 -4.11%→-1.57%, MDD 24.38%→21.87%
  - 2026 YTD +52.70%로 변화 없음

성과는 일관되게 개선됐지만, 보완 형태를 2024~2025 결과 확인 후 선택했으므로
`promotable=False`로 코드에 고정했다.

## 12. 최종 판정과 다음 단계

- 실패구간 회고·클러스터링 접근은 유효했다.
- 이번 반대방향 단기 엣지는 기각한다.
- **C1 core 추가 피라미딩 중단**만 forward shadow 관찰 후보로 유지한다.
- production 진입 차단은 아직 하지 않는다.

Forward 승격 조건:

1. 최소 90일 또는 C1 신규 사례 20건 중 늦게 충족되는 시점까지 관찰.
2. 차단 가정 PnL이 실제 추가 트랜치 PnL보다 우수.
3. 초기 진입·Swing 거래수는 불변.
4. 월별 손익 3개월 중 2개월 이상 개선.
5. OI/funding 결측 시 반드시 fail-open(관찰 불가, 거래 영향 없음).

조건 통과 후에도 demo에서 먼저 피라미딩 차단을 검증하고 별도 승인 없이는
production으로 승격하지 않는다.

## 13. Demo 차단 + forward shadow 비교 구현 (Rocky 승인, 2026-08-22)

Rocky 확인: PRISM-BTC의 실제 집행 계정은 실자금 계정이 아니라 Bybit demo다.
따라서 demo에서는 C1 추가 피라미딩 차단을 즉시 활성화하고, shadow는 같은 주문을
계속 가상 체결해 비교군을 유지한다. 초기 core 진입과 Swing Lane B는 양쪽 모두 불변.

### 구현 파일

- `core/failure_guard.py`
  - 2022~2023 robust center/scale와 k=4 centroid를 버전
    `round10-2022_2023-k4-v1`로 동결.
  - 피처 하나라도 결측/비정상이면 `None` fail-open.
  - C1이더라도 초기 트랜치 0은 항상 관찰 대상에서 제외.
- `live/failure_observer.py`
  - 완결 4h/1d, 직전 180개 펀딩, PIT 6시간 OI로 동일 피처 생성.
  - OI DB 없음·7개 미만·2시간 이상 stale·시간 간격 이상 시 fail-open.
- `collector/open_interest.py`
  - 공식 Bybit `GET /v5/market/open-interest`의 `linear/BTCUSDT/1h`, 최신 200개.
  - `timestamp` unique upsert, `openInterest`와 `singleOpenInterest` 저장.
- `collector/funding.py`
  - 공식 Bybit `GET /v5/market/funding/history` 최신 200개 증분 upsert.
  - 최신 펀딩이 12시간 이상 stale이면 observer는 fail-open.
- `live/tracking.py`
  - `btc_failure_shadow` 관찰 원장 추가.
- `live/shadow.py`
  - C1 add-on intent에 observation ID만 부착.
  - 실제 주문은 기존처럼 pending→fill→close.
  - 종결 시 실제 net PnL/R와 `avoided_net_pnl=-actual_net_pnl` 기록.
- `live/demo.py`
  - C1으로 분류된 core add-on(`tranche_index>0`)만 post-only 발행 전에 차단.
  - 분류 불가·데이터 stale이면 fail-open하여 기존 주문을 그대로 진행.
  - 초기 트랜치와 기존 보유 포지션은 절대 건드리지 않음.
- `live/runner.py`
  - shadow/demo 매매 처리 완료 후 시간당 1회 OI·펀딩 갱신.
  - API/DB 실패는 warning만 남기고 매매 및 heartbeat 계속.

### 관찰 상태

- `intent`: C1 add-on 의도 발생. 실제 주문에는 영향 없음.
- `filled`: 기존 주문이 실제 shadow 포지션으로 체결됨.
- `expired`: 기존 pending 주문이 미체결 만료됨.
- `closed`: 실제 종결 손익과, 차단했을 때 피했을 손익을 기록.

### 운영 판정 쿼리

```sql
SELECT status, COUNT(*) AS n,
       ROUND(SUM(COALESCE(actual_net_pnl, 0)), 2) AS actual_pnl,
       ROUND(SUM(COALESCE(avoided_net_pnl, 0)), 2) AS avoided_pnl,
       ROUND(AVG(actual_r_multiple), 3) AS avg_actual_r
FROM btc_failure_shadow
WHERE mode='shadow'
GROUP BY status;
```

`closed >= 20`과 최소 90일을 모두 충족한 뒤 demo 차단과 shadow 비교를 판정한다.
실자금 계정으로의 승격은 여전히 별도 승인 없이는 금지한다.

### 런타임 활성화

- `~/Library/LaunchAgents/com.prism.btc-shadow.plist`: 매시 :01/:31
- `~/Library/LaunchAgents/com.prism.btc-demo.plist`: 매시 :02/:32
- 실행경로: `~/work/prism-insight/.venv/bin/python`
- 기존 2026년 6~7월 cursor는 최신 확정 30m/4h/10m로 이동해 과거신호 재생 차단.
- 활성화 전 demo 거래소 포지션 0, 주문 0, 로컬 demo/shadow 포지션 0 확인.
- 첫 shadow/demo tick 모두 `error=None`, 신규 과거봉 처리 0, exit code 0.
- HTTP 라이브러리 로그는 WARNING으로 제한해 Telegram bot token URL 기록 차단.

### 검증

- 실제 Bybit public OI API: 1h 200행, 최신 timestamp, single-side 필드 파싱 확인.
- 실제 Bybit public funding API: 최신 200행과 rate 파싱 확인.
- BTC 전체 테스트: 355 passed.
- Ruff 및 Python compile 통과.

## 14. 운영 알림 라우팅·스윙 재생 수정 (2026-08-22)

첫 demo tick에서 스윙 메시지 4건이 개인 테스트방으로 전송됐다.

### 원인

1. 메인 demo/shadow cursor만 최신화했고 mode=`swing`의 독립 cursor는 7월에
   머물러 있었다.
2. 스윙 데모 키가 없어 ExchangeBackend가 아니라 VirtualBackend였으므로 실제
   거래소 체결은 없었지만, 8월의 과거 4h 신호를 재생해 가상 open/close 2쌍을
   만들었다.
3. `_resolve_channel`이 모든 모드에서 `BTC_TELEGRAM_CHANNEL_ID`를 먼저 선택해
   개인 테스트방으로 보냈다.

### 수정

- demo/live 알림은 `TELEGRAM_CHANNEL_ID` 운영채널을 우선.
- shadow/research 알림만 `BTC_TELEGRAM_CHANNEL_ID` 테스트방을 우선.
- 스윙이 flat이고 cursor가 최신봉보다 8시간 이상 뒤처졌으면 과거 봉을 처리하지
  않고 최신 30m/4h로 fast-forward, `cursor_resync` 이벤트 기록.
- 재생으로 생성된 swing 거래 2건, equity 2행, trade 이벤트 4건을 정확히 제거하고
  swing equity 10,000달러·trade counter 0으로 복원.
- Bybit demo 실제 포지션/주문은 모두 0건이었음을 재확인.
- 운영채널 라우팅 확인 메시지 1건 전송 성공.
- 전체 BTC 테스트: 357 passed, Ruff 통과.
