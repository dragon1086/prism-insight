# 초분할 단계별 도입 및 검증

## 공통 원칙

- 한 단계에서 한 종류의 부작용만 노출합니다.
- 이전 단계의 증거와 rollback이 없으면 다음 단계로 넘어가지 않습니다.
- 단계·threshold·event schema 변경 시 policy version과 holdout을 다시 시작합니다.
- 기존 주문·손절·매도·메시지 변화가 0인지 먼저 증명합니다.
- 자동 LIVE 승격은 금지하며 최종 승격에는 사용자 승인이 필요합니다.

## Phase 0 — 계약·문서

완료 조건:

- 용어, 불변조건, 상태 모델, 실패 로그, Evidence 계약 문서화
- 기존 포지션·주문·피라미딩 경계 확인
- live path 코드 변경 0

## Phase 1 — 순수 도메인 코어

현재 단계입니다.

- `prism_core/micro_split.py`
- 목표 단계와 레짐 상한 검증
- 정수 주식 예상 delta 계산
- DB·환경·네트워크·주문 import 0
- production caller 연결 0

완료 기준:

- 10/30/60/100 및 300 상한 단위테스트
- 일반장 100 초과 차단
- 동일 단계·하향 단계 차단
- 가격 하락만으로 추가매수 불가
- 정수 주식 반올림이 목표금액을 초과하지 않음

## Phase 2 — CAPTURE/SHADOW 계산

기존 결정마다 현행 결과와 초분할 후보 결과를 나란히 기록합니다.

- 주문, holdings, message, score, sell 변화 0
- `target_pct`, transition reason, projected qty만 secret-minimized event로 기록
- 같은 `decision_id`와 `policy_version`으로 exact join
- 계정별 unit amount 원값 대신 필요 시 비율·hash·구간만 내보냄

완료 기준:

- 20 US 거래세션 이상
- 신규 진입 결정 30건 이상
- 중복 transition 0
- 미래정보 누수 0
- baseline decision/order diff 0
- restart 후 같은 캠페인에 중복 leg 0

## Phase 3 — 내부 시뮬레이터 초분할

- additive campaign/leg shadow table 추가
- 가상 목표비중·가상 체결·가중 평균가·가상 MFE/MAE 계산
- 기존 holdings와 실제 KIS 주문은 계속 불변
- 현행 100/200/300과 초분할 10~300을 동일 가격 경로로 replay

완료 기준:

- 장부 보존식과 슬롯 합 불변
- 손절 시 남은 가상 수량 0
- no-trade/고가주에서도 캠페인 결과 보존
- 비용·슬리피지·정수 주식 민감도 보고

## Phase 4 — 실행 SHADOW

- 계정별 `desired_qty`, `buy_delta` 계산만 수행
- KIS 주문 호출 0
- 실제 KIS 보유·미체결과 비교하되 내부 캠페인을 수정하지 않음
- `SUBMITTED`를 fill로 세지 않음

완료 기준:

- 계정별 정수 수량 projection 재현성 100%
- 가격 하락만으로 발생한 add 0
- 중복 intent 예상 0
- 전체 목표 슬롯·현금·open-risk 위반 0

## Phase 5 — 데모/제한 계정

- Bybit가 아니라 KIS demo 또는 별도 제한 계정만 사용
- 첫 단계는 신규 매수만, 피라미딩 100 초과는 비활성
- confirmed fill reconciliation과 부분/미체결/취소 복구 검증
- hardstop·trend-exit·batch exit의 수량 일치 검증

## Phase 6 — 제한 LIVE

사전 조건:

- [Trading Change Review Harness](../TRADING_CHANGE_REVIEW_HARNESS.md) 통과
- Evidence 기준 통과
- 단일 시장·단일 계정·제한된 기간·비중
- 즉시 OFF kill switch와 기존 100% 경로 rollback
- 사용자 명시 승인

초기 LIVE에서는 100% 상한만 허용하고, 300% 피라미딩은 별도 SHADOW/승격 대상으로 둡니다.

## 롤백 단위

- Phase 1: import 제거만으로 완전 롤백
- Phase 2: feature flag OFF, event emission 중단
- Phase 3: campaign shadow 쓰기 OFF, 기존 holdings 무접촉
- Phase 4: projection OFF, 주문 영향 없음
- Phase 5~6: execution gate OFF 후 기존 all-in 경로 복귀

