# PRISM 초분할 설계 문서

> 상태: **Phase 2a — US 신규 적격진입 0→10% SHADOW projection**
> 정책 초안: `micro-split-v1-draft`  
> 거래 영향: **0**

## 용어

- **슬롯**: 계좌를 10등분한 기본 자본 단위. 1슬롯은 100%로 표현합니다.
- **초분할**: 한 종목의 1슬롯을 10~100% 누적 목표비중으로 나눠 점진 진입하는 방식입니다.
- **피라미딩**: 강세장에서 수익 중인 종목의 상한을 100%보다 높여 최대 300%, 즉 3슬롯까지 확장하는 방식입니다.
- **목표비중**: 내부 전략 원장이 원하는 연속적인 슬롯 비중입니다.
- **실행수량**: KIS 같은 브로커에서 실제 주문·확인된 정수 주식 수량입니다.

일반 시장에서는 종목당 100%가 상한입니다. `strong_bull` 또는 `parabolic`에서만
최대 300%를 허용합니다. 목표비중과 실주문은 독립적으로 기록합니다.

## 문서 구성

- [아키텍처](ARCHITECTURE_ko.md): 불변조건, 상태 모델, 정수주식 투영, 컴포넌트 경계
- [단계별 도입](ROLLOUT_ko.md): CAPTURE부터 제한 LIVE까지의 승격 순서와 롤백
- [증거 계약](EVIDENCE_CONTRACT_ko.md): 현행 대비 비교 지표와 표본·fill 기준
- [시행착오](FAILURE_LOG_ko.md): 이미 겪은 실패와 재발 방지 규칙

## 현재 구현

`prism_core/micro_split.py`는 다음 두 계산만 수행합니다.

1. 레짐별 허용 단계와 단조 증가 목표비중을 검증합니다.
2. 목표비중이 올라간 순간에만 정수 주식 예상 수량을 계산합니다.

순수 코어는 DB, 네트워크, 환경 변수, KIS, 에이전트, 주문 모듈을 import하지 않습니다.
Phase 2a에서는 US 최종 `entry_eligible` 경계가 `observability.micro_split`을 fail-open으로
호출해 신규 캠페인의 0→10% 목표와 계정별 정수주식 예상 수량을 JSONL에 기록합니다.
schema v2는 원 단위금액을 노출하지 않고 10·30·60·100%별 예상 수량, 최초 1주 가능
단계, 단위금액 snapshot reference를 함께 기록합니다.
반환값은 사용하지 않으며 `ExecutionService`, holdings, 피라미딩, 매도 루프에는 연결하지
않습니다. `.env MICRO_SPLIT_SHADOW_ENABLED=false`가 기본 OFF입니다.

`tools/build_micro_split_evidence_packet.py`는 sanitized JSONL의 실제 SHADOW 이벤트와
과거 candidate의 정수주 counterfactual projection을 분리한 결정론적 Packet을 만듭니다.
네트워크·DB·브로커·주문 접근은 없습니다.

## D1~D3 판정

- 실제 관측: 3 US 거래일, 후보 19건, 적격 진입·초분할 이벤트 2건
- 이벤트 계약: 기대 대비 100%, 중복·민감정보 노출·거래 영향 0
- 실행·성과: `SUBMITTED_ONLY` 2건, confirmed fill 0건, matured outcome 0건
- 판정: **HOLD / CONTINUE_CAPTURE**

schema v1은 삭제하거나 덮어쓰지 않습니다. v2 coverage와 replay 표본을 별도로 축적하며
Phase 3 campaign ledger SHADOW로 아직 승격하지 않습니다.

## 기본 초안 단계

- 기본 슬롯: `10 → 30 → 60 → 100%`
- 강세장 확장 연구안: `130 → 160 → 200 → 230 → 260 → 300%`

이는 LIVE 파라미터가 아니라 SHADOW 비교를 시작하기 위한 versioned 초안입니다. 단계나
전이 조건을 바꾸면 새 `policy_version`으로 취급하고 holdout을 다시 시작합니다.

## 비목표

- 현재 매수·매도 금액을 즉시 변경하지 않습니다.
- KIS 주문 성공 여부로 내부 목표비중을 변경하지 않습니다.
- 가격이 하락했다는 이유로 수량을 늘리지 않습니다.
- 초분할을 물타기나 손절 회피 수단으로 사용하지 않습니다.
- 사용자 승인 없이 SHADOW·데모·LIVE로 자동 승격하지 않습니다.
