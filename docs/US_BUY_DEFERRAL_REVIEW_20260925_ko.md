# 미국 매수보류 및 사전 거래량 측정 검토

## 범위와 결론

보고서 장애 수리와 매매 정책 검토를 분리한다. 이번 작업은 새 거래량 전역 필터,
점수·손익비·손절 완화, SHADOW/LIVE 승격을 하지 않는다.
거래량의 측정값·분모·기준시각을 단계별로 일치시키는 방향은 검토할 가치가 있지만,
관측 3건만으로 매수 증가나 성과 개선을 입증하지 않는다.

## 운영 Evidence Packet

- 서버 코드: `5658f8a1`; 생성기 마지막 변경: `35d4a01`.
- Packet ID: `a4b3c1dc2c63be420b4c30c7`, schema 3, 계약 `entry-quality-harness-v2`.
- as-of: 2026-09-24 18:53:34 UTC. 동일 입력 두 번 생성 시 ID 일치.
- prospective 시작: 2026-09-01 14:52:42 UTC, 최초 live capture 기준.
- 원시 JSONL은 DB 서버에 남기고 정형 생성기의 sanitized Packet만 검토했다.

부족 사유를 생략하지 않는다:

- `PROSPECTIVE_DATES_LT_20`: 16일.
- `PROSPECTIVE_CANDIDATES_LT_100`: 70건.
- `CAPTURED_CANDIDATES_LT_100`: 70건.
- `STRATEGY_CLOSED_TRADES_LT_30`: 5건.

후보 결과 연결은 0건이며, daily/weekly setup·event risk는 70건 모두 MISSING이다.
이벤트 중복 49건은 생성기가 제거했다. decision ID 누락·후보 decision 중복·미래정보
제외는 0이다. 연결 진입 5건의 집행 상태는 SUBMITTED_ONLY 4건, REJECTED 1건이다.
전략 원장 결과와 실계좌 체결은 별개이며 실계좌 실현손익을 입증하지 못한다.

## 최신 세 후보

2026-09-25 03:53 KST / 9월 24일 14:53 EDT의 기록이다.

- VLO, Macro Sector Leader: no_entry, 점수 6/최소 5, 기록 R/R 0.5, 모멘텀 1.
- NBIS, Intraday Rise Top: no_entry, 점수 2/최소 5, 기록 R/R 0.1, 모멘텀 0.
- BE, Closing Strength Top: no_entry, 점수 2/최소 5, 기록 R/R 0.9, 모멘텀 1.

세 건 모두 연결 진입이 없다. `gate_allowed=false`는 여기서 결정론적 게이트의
거절 증거가 아니다. US tracker는 entry일 때만 그 게이트를 호출하고 no_entry에는
false 기본값을 남긴다 (`prism-us/us_stock_tracking_agent.py:4281-4288`, 기준 코드).

기록 R/R은 moderate_bull 하한 1.2보다 낮으나 목표·손절·가격 원자료가 Packet에
없으므로 독립 재계산하지 않았다. 상세 거래량·평균·보류 원인도 Packet에 없다.
정확한 decision ID가 없는 근접 시각 로그를 억지로 연결하지 않았다.

## 실제 코드의 조건과 전달 경계

기준 코드의 `prism-us/us_trigger_batch.py`:

- Closing Strength는 이미 당일 누적 거래량이 전일보다 큰 조건을 사용한다 (700-710행).
- Intraday Rise는 거래대금·상승률·양봉 중심이다 (596-618행).
- Macro Sector Leader는 거래대금·주도 섹터 중심이다 (864-887행).

`prism-us/cores/agents/trading_agents.py`의 평균 20일 거래량 대비 2배는 여러
모멘텀 신호 중 하나다 (197-207행). Macro Sector Leader는 약한 단기 모멘텀만으로
일괄 배제하지 않는다 (221-224행). 장중 누적량을 하루 완료량으로 해석하지 않아야
한다 (333-342, 695-703행).

스크리닝의 전일 대비 증가, BUY의 평균 대비 증가, 돌파 확인은 동일 조건이 아니다.
현재 스크리닝→보고서 전달은 ticker/name/trigger/mode 중심이고 실제 R/R은 미확정이다
(`us_stock_analysis_orchestrator.py:578-587`). tracker의 trigger 입력도 type/mode/RR
중심이다 (`us_stock_tracking_agent.py:5127-5133`).

## 대안과 다음 검토

1. 기존 유지: 즉시 정책 변경 위험은 없지만 근거 전달의 빈틈이 남는다.
2. 동일 측정 근거 전달·관측: 기존 수집 자료에서 수치·분모 평균·기간·자료시각·완료봉/
   장중 여부를 구분하고 충족/미달/미확인을 명시한다. 현재 가장 작은 다음 검토다.
3. 전역 거래량 필터: 모든 트리거를 같은 진입 형태로 취급하고 유효 후보까지 줄일 수
   있으므로 지금 적용하지 않는다.

규칙 가설을 정하기 전에 trigger별 동일 후보 입력과 유지할 정상 후보·거절할 반례를
확보한다. 분석 후 탈락 감소, 후보 수, 좋은 후보 누락을 함께 평가한다. 같은 표본에서
임계값을 고르고 개선을 확정하지 않는다. 새 규칙의 holdout·중단·복귀 조건을 먼저
정하며, 기존 BUY·주문 직전 가격 검증·예산·SELL·손절은 보존한다.

**판정: CONTINUE_CAPTURE.** 정책 구현·승격이나 매수 증가 입증이 아니다.
