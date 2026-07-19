# Issue #412 — 주문 실행 아키텍처 리팩토링 마스터 플랜

> 이슈: [#412 매수/매도 Agent 분리와 KIS 실제 주문 실행 구조 이식 설계](https://github.com/dragon1086/prism-insight/issues/412)
> 브랜치: `feature/issue-412-execution-architecture`
> 상태: Phase 4-b2b-1 KR ENTRY 배포 완료, Phase 4-b2b-2 KR EXIT 구현 준비
> (2026-07-06 시작, 2026-07-13 main #432 기준 전면 재검토,
> **2026-07-19 Phase 4-b2b-1 PR #465, main `3361550f` 게이트 OFF 배포 완료**)

## 목적

이슈 #412의 방향(계층 분리, OrderIntent 기반 실행, Broker Adapter)은 수용하되,
greenfield 이식이 아니라 **라이브 시스템의 strangler 방식 단계 전환**으로 실행한다.

최종 목표 2가지:

1. **정합성**: 로컬 원장 선커밋 → 실주문 fire-and-forget 구조를 제거하고,
   원장과 증권사 계좌가 어긋나지 않는 실행 계층을 만든다.
2. **이식성**: `StockTrackingAgent` god class를 시장 불가지론적 코어 + 포트/어댑터로
   해체한다. 이식성의 합격 기준은 **prism-us 포크(07-13 기준 3,600줄, 성장 중)가
   "프로파일 + 어댑터 조합"으로 대체되는 것**이다. 두 시장을 하나의 코어가 감당하면
   제3 프로젝트 이식은 자동으로 따라온다.

## 문서 맵

| 문서 | 내용 |
|------|------|
| [01-current-state.md](01-current-state.md) | 현재 아키텍처 분석 — 실제 주문 경로, 결함, 이미 존재하는 안전장치 (file:line 증거) |
| [02-issue-412-review.md](02-issue-412-review.md) | 이슈 #412 설계 검토 — 채택 / 보완 / 대체 / 추가 |
| [03-target-design.md](03-target-design.md) | 목표 아키텍처 — 코어 엔진, 포트 인터페이스, 이벤트, 상태기계, DB 모델 |
| [04-migration-plan.md](04-migration-plan.md) | Phase 0~6 단계별 실행 계획과 각 단계 완료 조건 |
| [05-verification-plan.md](05-verification-plan.md) | 검증 전략 — 단위/golden/shadow 병행 기록/demo 계좌/서버 배포 절차/롤백 |
| [11-phase4b2-pending-exit-plan.md](11-phase4b2-pending-exit-plan.md) | Phase 4-b2 분할 계획 — 4-b2a 기반 API, 4-b2b KR 전환, 4-b2c US queue 연속성/전환 |
| [12-phase4b2b-kr-execution-plan.md](12-phase4b2b-kr-execution-plan.md) | Phase 4-b2b KR 전환 상세 계획 — 4-b2b-0~3 안전 분할, 실패/재시도 정책, 배포 gate |
| [13-phase4b2b1-kr-entry-plan.md](13-phase4b2b1-kr-entry-plan.md) | Phase 4-b2b-1 KR ENTRY 구현 계획 — no-auto-retry guard, private lifecycle, 결과별 TDD |

## 진행 체크리스트

- [x] Phase 0: 계획/설계/검증 문서 작성 (이 디렉토리)
- [x] Phase 0: 이슈 #412 코멘트로 방향 합의 (2026-07-06 게시)
- [x] Phase 0.5: main #432 기준 재검토 — 앵커/전제 갱신, 주문 경로 9곳 재인벤토리 (2026-07-13)
- [x] Phase 1-a: 파싱/정규화 순수 함수 추출 (PR #447, main `36e6e5ec`)
- [x] Phase 1-b: KST 주문시간대 순수 함수 추출 (PR #455, main `c4c2539d`)
- [x] Phase 2: ExecutionService chokepoint 도입 (PR #456, main `1aca6029`)
- [x] Phase 3: OrderIntent 영속화 (PR #459, main `8ff33b17`)
- [x] Phase 4-a: position OPEN/CLOSED shadow 병행 기록 + 대조 (PR #460/#461, main `5ed6f38c`)
- [x] Phase 4-b1: persisted intent ↔ position linkage, legacy 순서/read 유지 (PR #462, main `9b3ecc58`)
- [x] Phase 4-b2a: transaction-aware reserve/PENDING lifecycle 기반 API — 운영 호출부 미배선 (PR #463, main `de74af0c`)
- [x] Phase 4-b2b-0: KR 전환 안전 기반 — originating store, exit quarantine, 3상태 holding 조회 (PR #464, main `449e4750`)
- [x] Phase 4-b2b-1: KR ENTRY 전체 경로 PENDING write-ahead (PR #465, flag OFF 배포)
- [ ] Phase 4-b2b-2: KR EXIT 전체 경로 PENDING write-ahead + 실패 보상 (flag OFF)
- [ ] Phase 4-b2b-3: 별도 승인·무거래 창 검증 후 KR 시장 gate 일괄 활성화
- [ ] Phase 4-b2c: US queued-intent 연속성 + US 전체 경로 전환 (시장 단위 gate)
- [ ] Phase 4 read switch: 충분한 운영 대조 후 별도 승인
- [ ] Phase 5: BrokerAdapter 추출 (체결/미체결/정정 포함) + lock 일반화 + reconciliation (alert-only)
- [ ] Phase 6: 이벤트 버스 / 코어-어댑터 패키지 분리 / prism-us 흡수

## 작업 원칙

- 각 Phase는 독립적으로 배포 가능하고, 실패 시 해당 Phase만 롤백한다.
- Phase마다 **서버(운영 환경) demo 계좌 배포 검증**을 통과해야 다음 Phase로 넘어간다.
  (검증 절차는 05-verification-plan.md 참고)
- 코드 변경 전에 반드시 해당 Phase 문서를 갱신하고, 완료 조건을 먼저 정의한다.
- 기존 사고에서 나온 회귀 케이스(2026-07-01 MU 중복 SELL, #288 over-sell,
  빈 portfolio 응답)는 자동화 테스트로 고정한다.

Phase 2의 구체적인 작업 순서와 비목표는 [07-phase2-execution-plan.md](07-phase2-execution-plan.md)를 따른다.
Phase 4-a의 안전한 shadow 범위와 관찰 gate는 [09-phase4a-execution-plan.md](09-phase4a-execution-plan.md)를 따른다.
Phase 4-b1의 동작 보존형 intent linkage 범위는 [10-phase4b1-intent-linkage-plan.md](10-phase4b1-intent-linkage-plan.md)를 따른다.
Phase 4-b2의 안전한 분할, 상태 전이, 활성화 차단 조건은 [11-phase4b2-pending-exit-plan.md](11-phase4b2-pending-exit-plan.md)를 따른다.
