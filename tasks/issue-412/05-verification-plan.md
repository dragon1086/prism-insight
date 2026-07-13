# 05. 검증 계획

핵심 전제: **단위 테스트만으로는 "버그 없음"을 말할 수 없다.**
최종 검증은 운영 서버에 demo 계좌 모드로 배포해 실제 장 시간에 돌려보는 것이다.

## 1. 검증 피라미드

```
L4  운영 서버 배포 검증 (demo 계좌, 실제 장 시간, Phase별 필수 게이트)
L3  Shadow 병행 기록 대조 (Phase 4)
L2  회귀/시나리오 테스트 (사고 케이스 고정, 동시성)
L1  단위 테스트 (순수 함수, payload builder, 상태 전이)
```

## 2. L1 — 단위 테스트

- 파싱/정규화 함수: 기존 동작 고정 (golden 입출력)
- KIS payload builder: TR ID·가격 문자열 포맷(국내 정수) 검증 — 이슈 #412 테스트 계획 채택
- 주문 상태기계: 허용 전이 전체, 비허용 전이 거부
- 분할매도 수량 계산: #288 케이스 (미체결 잔량 존재 시 over-sell 금지)
- idempotency: 동일 key 재삽입 시 unique 제약 위반 확인

## 3. L2 — 회귀/시나리오 테스트 (CI 상주)

사고에서 나온 케이스를 테스트로 고정한다:

1. **MU 중복 SELL (2026-07-01)**: 두 실행 경로가 같은 포지션을 초 단위 간격으로
   매도 시도 → 두 번째는 chokepoint에서 abort, SELL publish 정확히 1회.
   (동시 프로세스 시뮬레이션: 별도 커넥션 2개, WAL 모드)
   *07-13 갱신: 가드는 이미 KR/US 코드에 존재한다 (`stock_tracking_agent.py:1300-1327`,
   `prism-us/us_stock_tracking_agent.py:2242-2261`). 이 테스트의 역할은 새 동작
   구현이 아니라 **기존 가드를 회귀로 고정**해 ExecutionService 이관(Phase 2) 중
   시맨틱 훼손을 잡는 것 — 따라서 Phase 2 착수 전에 먼저 작성한다.*
2. **#288 over-sell**: 피라미딩 3행, 첫 2행 지정가 미체결 상태에서 마지막 행 매도
   → 스냅샷 잔량 기준 수량, broker 재조회 금지.
3. **빈 portfolio 응답**: get_portfolio 1회 빈 리스트 → 보유 없음 확정 금지, 재확인.
4. **주문 실패 보상**: broker 예외/타임아웃 주입 → 매수 원장 보상, 매도 원장 복원,
   OrderFailed 알림 발생, UNKNOWN 경로는 체결조회 대조.
5. **shadow/live 격리 (양방향)**: shadow 포지션이 live 매도 판단 쿼리에 절대
   잡히지 않음 **+ shadow 기록이 live 주문의 중복 판정에 입력되지 않음**
   (hardstop에서 과거 SHADOW 레코드가 LIVE 손절을 3주간 차단한 실사고 —
   `tools/hardstop_seller.py:186-187`).
6. **크로스 프로세스 owner_lock (07-13 추가)**: 두 프로세스가 같은 티커의 lock을
   경합 → 한쪽만 획득, 만료 시각 경과 후 재획득 가능. Phase 5의 lock 일반화 시
   기존 루프 동작(`loop_a_position_state`)이 깨지지 않음을 고정. **특히
   hardstop↔trend_exit 동시 경합 케이스 포함** — 현재 둘은 서로 다른 lock 테이블
   (`loop_a_*` vs `loop_b_*`)이라 직렬화되지 않는 상태이므로, 통합 후 이 케이스가
   새로 막히는지가 핵심 검증점이다.

모의 broker(fake adapter)로 구현. 실제 KIS 호출 없음.

## 4. L3 — Shadow 병행 기록 대조 (Phase 4 전용)

- 신구 원장 동시 기록, 판단은 구원장 기준.
- 매 거래일 마감 후 대조 스크립트: (ticker, account, qty, 상태) 완전 일치 여부.
- 5 거래일 연속 무불일치가 읽기 전환 조건. 불일치 1건이라도 나오면 카운터 리셋.

## 5. L4 — 운영 서버 배포 검증 (Phase별 게이트)

### 절차 (매 Phase 공통)

1. 서버에서 해당 브랜치 checkout, **demo 계좌 모드 확인**
   (`kis_devlp.yaml` svr=vps 계열 + 환경 flag. real 전환 조건: config flag +
   runtime env flag 동시 충족 — 이슈 #412 채택)
2. 실제 장 시간에 정규 cron 스케줄로 최소 N 거래일 운영
   (Phase 1: 1일, Phase 2: 3일, Phase 4: 5일+3일, Phase 6: 5일)
   — batch뿐 아니라 **루프 cron(hardstop/trend_exit/fill_chaser, */10)도 포함**해서
   돌린다. Phase 2부터는 루프 경로도 chokepoint를 지나므로 루프 없는 검증은 무효.
   - **선행 실사 (07-13)**: 루프 3종의 서버 crontab 설치 상태를 먼저 확인할 것.
     코드 주석 기준 셋 다 "Intended cron (SHADOW until reviewed)"이고 특히
     fill_chaser는 "NOT installed" 명시 (`tools/fill_chaser.py:47`) — 미설치
     루프는 검증 시작 전 SHADOW로 설치한다.
   - **게이트 일수 정의 (07-13)**: market-pulse batch-rest(`cores/regime_policy.py`)가
     CORRECTION/UNDER_PRESSURE 레짐에서 배치를 통째로 쉬게 하므로, "N 거래일"은
     달력이 아니라 **주문 경로가 실제 실행된 거래일 N일**로 센다 (batch-rest로
     쉰 날은 카운트 제외). 나쁜 레짐 주간에 무실행 통과를 막기 위함.
   (참고: #431로 US 분석 배치는 아침/오후 2회 — 검증 일정 산정 시 반영)
3. 매일 확인 항목:
   - `order_intents` ↔ `broker_orders` ↔ KIS demo 계좌 잔고 3자 대조
   - 에러 로그 grep: `Actual purchase failed`, `Actual sell failed`, UNKNOWN 상태 잔존
   - Telegram 알림 정상 수신 (특히 OrderFailed — Phase 3부터)
   - GCP/Redis 시그널 수신측 중복 없음 (decision_id 기준)
4. 이상 발견 시: 해당 Phase revert 배포 → 원인 분석 문서화 → 수정 후 재검증

### 검증용 인위 시나리오 (demo 계좌에서 안전)

- 정상 매수 → 익일 매도 full cycle
- 장외 시간 매수 트리거 → 예약주문 경로 + 익일 체결 확인
- 강제 실패: 잘못된 종목코드 주문 → FAILED 처리·알림·원장 보상 확인
- 수동 개입: KIS 앱에서 demo 계좌에 수동 주문 → reconciliation 탐지 (Phase 5)

### live 전환 게이트 (마지막)

- 전 Phase 완료 + demo 무사고 기간 충족
- Rocky 수동 승인 (config flag는 사람이 직접 변경, 자동화 금지)
- 첫 live 주는 최소 슬롯(1종목)으로 카나리 운영

## 6. 판단 로직 무변경 확인 (전 Phase 공통)

이 리팩토링은 **실행 계층**의 재설계다. LLM 판단(프롬프트, 점수, 시나리오)은
변경 대상이 아니다. Phase마다 판단 결과(decision, buy_score)의 분포가
리팩토링 전과 유의미하게 달라지지 않았는지 로그 대조로 확인한다.
달라졌다면 실행 계층 리팩토링이 판단 입력을 오염시킨 것 — 즉시 중단 신호.
