# 초분할 시행착오·실패 로그

> 이 문서는 append-only에 가깝게 운영합니다. 실패를 지우지 않고 원인·영향·재발 방지
> 규칙을 추가해 다른 PRISM 버전이 같은 실수를 반복하지 않게 합니다.

## F-001 — KIS만 50%, 시뮬레이터는 100%

- 사건: 과거 `PULSE_PILOT_FACTOR`가 실 KIS `buy_amount`만 절반으로 줄였습니다.
- 영향: 방송·매매일지·내부 성과는 전량, 실계좌는 반쪽이라 동일 전략처럼 비교할 수 없었습니다.
- 조치: 해당 fractional sizing을 제거하고 배치당 종목 수 제한으로 대체했습니다.
- 교훈: 목표비중 원장과 실행 원장은 독립이어야 하지만 같은 campaign/leg로 연결돼야 합니다.

## F-002 — legacy holding 행에는 수량·비중이 없음

- 사건: 현행 `stock_holdings`, `us_stock_holdings`는 행 하나를 1슬롯으로 가정합니다.
- 영향: 10%·30% leg, 가중평균가, 계정별 정수 수량을 정확히 표현할 수 없습니다.
- 조치: 기존 행을 초분할 leg로 재사용하지 않고 additive campaign ledger를 설계합니다.

## F-003 — 제출을 체결로 오인할 위험

- 사건: broker order accepted/SUBMITTED 뒤 실제 보유·미체결이 없는데 내부 OPEN은 남을 수 있습니다.
- 영향: fill 학습과 실제 PnL 귀속이 오염됩니다.
- 교훈: `SUBMITTED`는 fill이 아니며 confirmed reconciliation 전에는 실행 수량을 늘리지 않습니다.

## F-004 — 피라미딩 종목의 고빈도 보호 공백

- 사건: hardstop/trend-exit은 다중 holding 행 ticker를 건너뛰고 batch가 부분청산을 담당합니다.
- 영향: 노출이 가장 큰 피라미딩 종목의 청산 cadence가 느려질 수 있습니다.
- 교훈: 초분할 LIVE 전에 모든 exit 경로가 campaign-owned 수량을 같은 의미로 처리해야 합니다.

## F-005 — 정수 주식과 고가주

- 사건: 목표금액이 1주 가격보다 작으면 KIS는 주문할 수 없습니다.
- 잘못된 접근: KIS 가능 여부로 내부 원장·시뮬레이터 진입을 차단했습니다.
- 올바른 계약: 내부 목표비중은 계속 기록하고, KIS는 목표금액이 다음 정수 주식 임계값을 넘을 때만 delta를 주문합니다.

## F-006 — 가격 하락이 자동 물타기로 변할 위험

- 사건: 같은 목표비중에서 현재가로 desired quantity를 매 tick 다시 계산하면 가격 하락 시 수량이 늘어납니다.
- 조치: 정수 수량 projection은 **목표 단계가 상승한 사건에서만** 실행합니다.
- 교훈: target advance 없이 price-only add는 항상 금지합니다.

## F-007 — 레짐 슬롯 제한의 bottom-up refill

- 사건: `_get_regime_slots()`가 1~2개를 반환해도 최종 선택기가 고정 3개까지 다시 채웠습니다.
- 영향: post-FTD·약세/횡보 매수 절제가 무효화됐습니다.
- 조치: top-down+bottom-up 합을 최종 hard cap으로 적용했습니다(PR #633).
- 교훈: 계산한 정책값이 최종 side-effect 직전까지 실제 배선됐는지 통합 테스트해야 합니다.

## F-008 — 보고서 위험값과 최종 게이트 불일치

- 사건: US `max_portfolio_size`와 `macro_adjustment`가 최종 매수 게이트에 반영되지 않았습니다.
- 조치: scenario cap과 `buy + macro + journal` 계산을 최종 게이트에 연결했습니다(PR #633).
- 교훈: prompt 필드가 존재한다는 사실은 실행됐다는 증거가 아닙니다.

## F-009 — 일부 yfinance 비정상 셀이 전체 배치를 종료

- 사건: 중첩 Series·라벨 불일치가 morning/afternoon trigger 비교에서 예외를 냈습니다.
- 조치: 비정상 ticker 제거와 trigger별 fail-open 경계를 추가했습니다(PR #634).
- 교훈: 초분할 stage 계산도 종목 단위 오류를 격리하고 전체 배치를 계속해야 합니다.

