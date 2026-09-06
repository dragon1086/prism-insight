# BTC 진입 동반 손절 복구·검증 기록

작성일: 2026-09-06 KST
로드맵: M0.1·M0.2·M0.3 일부 / M0.5 기반 보완. M1.1 운영 연결은 별도입니다.

## 1. 재개한 맥락

agentmemory의 재개 결정 메모 2건에서 다음 순서를 확인했습니다.

1. 진입 주문에 거래소 손절을 함께 붙여 보호 공백을 줄입니다.
2. 메인·스윙의 미체결 위험 예약과 공통 실행 직렬화를 연결합니다.
3. 체결·펀딩·MTM NAV를 일치시킨 뒤 6개 시간대의 트레일을 비교합니다.

이전 오류 세션의 agentmemory observation은 0건이므로 전체 대화를 복원했다고
주장하지 않습니다. 결정 메모를 안내로 삼아 handoff·로드맵과 실제 WIP를 대조했습니다.
기준 WIP는 8b1769f0이며 원래 작업 브랜치의 사용자 수정·연구 파일은 보존했습니다.

## 2. 이번에 수정한 실행 계약

### 메인

- 최초 진입은 Full, 추가 진입은 Partial SL을 진입 주문에 동반합니다.
- 요청 전에 고유 parent link와 pending을 영속화합니다. 모든 place_order는
  자동 재시도하지 않습니다. 보호 없는 진입으로 fallback하지 않습니다.
- order-history가 늦어도 open-order의 exact link로 parent를 먼저 복구합니다.
  ID가 없다는 이유로 아직 열린 잔량을 완료 처리하지 않습니다.
- 새 native 진입의 원장 반영에는 terminal parent와 exact Trade execution,
  중복/상충 검사, 관측된 수량 증가의 일치가 필요합니다.
- 취소 의도만 저장하고 프로세스가 종료돼도, 재시작 후 exact active parent를
  재확인한 경우에만 취소를 다시 요청합니다. 취소 ACK는 pending 해제 근거가 아닙니다.
- PositionRow·native 연결·진입 메타·fill receipt를 짧은 SQLite transaction으로
  함께 저장합니다. transaction 안에 네트워크 호출을 넣지 않습니다.
- 체결 기록 후 재시작해도 PositionRow를 중복 삽입하지 않으며 TP1 후속 단계를
  고유 link로 복구합니다. 이전 TP는 exact terminal 확인 뒤 교체합니다.
  이전 TP의 부분체결·완전체결을 새 목표 수량의 체결로 재사용하지 않습니다.
- TP1 또는 backup 제출이 unknown이면 확인 없이 재주문하거나 다음 진입을
  허용하지 않습니다. 다른 native/manual/기존 owned stop의 충분한 보호가
  별도 unknown 제출의 상태를 확정해 주지는 않습니다.

### 스윙

- 최초 시장가 주문에 Full SL을 동반하고 요청 전 pending을 저장합니다.
- 모든 place_order를 한 번만 요청합니다. timeout-after-accept에서 같은 요청을
  내부적으로 재전송하지 않습니다.
- 진입 전후 BTCUSDT·positionIdx·side·유한한 수량/가격을 확인합니다.
- 실제 진입 기록에는 parent ID/link와 연결된 Trade execution이 필요합니다.
  페이지 전체 읽기, execId 중복 제거, 상충·유한 수치·포지션 수량 일치를 검사합니다.
- 충분한 native/manual SL은 그대로 두며 소유 ID로 바꾸지 않습니다.
- 필요한 backup은 요청 전에 고유 link와 제출 상태를 저장합니다.
  ACK 유실 뒤 exact readback으로만 복구하고, 미해결 backup은 다음 진입을 막습니다.
- native child가 먼저 청산했을 때 실제 parentOrderLinkId와 child ID를 연결해
  해당 closed PnL을 확인합니다. native/manual child의 취소 권한을 추정하지 않습니다.
- 가격·수수료·수량·PnL 및 파생 정산값이 유한하지 않으면 정산하지 않습니다.
  정상적인 음수 수수료 리베이트는 허용합니다.
- 캔들이나 로컬 PositionRow가 없어도 미확정 진입을 경보합니다. 일반 tick에서는
  읽기 전용 exposure 관측을 추가하되 체결·원장을 합성하지 않습니다.

### 공통 보호·저장

- 충분한 stop coverage 확인과 해당 주문의 수정/취소 소유권을 분리합니다.
- 일부 native Trade만으로 더 큰 전체 수량의 stop을 생성·증액하지 않습니다.
- 새 backup의 미확정 상태는 그 exact link의 확인으로만 해제합니다.
- 페이지 중복 행의 orderLinkId·parentOrderLinkId 상충은 UNKNOWN입니다.
- tracking.save_position/set_meta의 기존 기본 commit 동작은 유지합니다.
  명시적인 원자 작업에서만 commit=False를 사용합니다.

## 3. 공식 API와 읽기 전용 운영 대조

Bybit V5 create-order는 진입 시 stopLoss·slTriggerBy·slOrderType·tpslMode를
지원합니다. Full은 전체 포지션, Partial은 해당 주문의 실제 체결 수량에 적용하는
계약입니다. 요청 ACK는 비동기 접수이며 체결 확정이 아닙니다.
[공식 주문 생성 문서](https://bybit-exchange.github.io/docs/v5/order/create-order)

주문 이력·현재 주문 응답의 parentOrderLinkId는 실제 제공될 때 attached child의
부모 연결에 사용할 수 있습니다. 누락된 연결을 수량·시각 유사성으로 대신하지 않습니다.
slOrderType은 order-history의 필수 응답 필드로 요구하지 않습니다.
[공식 주문 이력](https://bybit-exchange.github.io/docs/v5/order/order-list),
[공식 현재/종결 주문 조회](https://bybit-exchange.github.io/docs/v5/order/open-order)

이번 db-server 읽기 전용 확인:
- 확인 HEAD: 92c0f4b0c5b51d78cf0028ef9fa8a5a39fcb6562, worktree clean.
- 메인·스윙 client 모두 api-demo.bybit.com을 확인했습니다.
- 실제 read_complete로 양쪽 포지션 continuation을 각각 2페이지 끝까지 소비했습니다.
- 확인 시점 양쪽 positive position 0, open order 0. 이후 상태까지 보장하지 않습니다.
- 스윙 최근 주문 응답에는 parentOrderLinkId 필드가 있었지만 값이 있는 표본은
  없었습니다. 새 native SL이 체결 순간 생성되는 운영 표본은 아직 없습니다.
- 실제 계정·키·원시 주문 ID를 이 문서에 포함하지 않습니다.
- 시험 주문, 출금·이체, 설정·cron 변경, app-server 작업은 하지 않았습니다.

## 4. 검증

### 전체 작성본

- 변경 전: BTC 570 passed, 14.43초.
- 최신 전체 작성본: BTC 652 passed, 23.59초.
- 오류 주입 테스트를 먼저 실패시킨 뒤 수정했습니다. 주요 반례는:
  - lost ACK + history lag + 열린 부분 진입 parent
  - 취소 의도 저장 후 전송 전 프로세스 종료
  - 원장 INSERT/receipt 전후 프로세스 종료
  - TP1 응답 유실·ACK-only·기존 부분체결 TP 교체
  - 잘못된 symbol/side/positionIdx·NaN/무한대·상충 execution
  - native child 우선 체결·정산 지연·재시작
  - 충분한 다른 stop이 unknown backup의 fence를 해제하는 오류

### 분리한 배포 후보

- 원래 WIP에서 진입 SL 관련 11개 소스/테스트 파일만 별도 worktree로 옮겼습니다.
- 공통 위험 reservation 모듈·그 테스트, 미추적 연구 파일은 배포 후보에 포함하지 않습니다.
- 배포 후보: 625 passed, 1 skipped, 21.12초.
- skip 1개는 별도 worktree에 real market.db를 복사하지 않아 실행하지 않은
  실제 데이터 기반 no-lookahead 검사입니다. 합성 데이터 인과성 테스트는 통과했습니다.
- Ruff, Python AST syntax, git diff --check 통과.
- 원래 작성본과 배포 후보의 해당 11개 파일이 byte-identical임을 확인했습니다.
- 실제 Python 타입 검사기는 준비돼 있지 않아 타입 검사 완료로 주장하지 않습니다.

실행 명령:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=prism-btc \
  .venv-bt/bin/python -m pytest prism-btc/tests \
  -q -p no:cacheprovider --disable-warnings --maxfail=1
```

## 5. 변경 파일

- prism-btc/live/native_stop.py: 동반 SL 가격·방향·Full/Partial 파라미터.
- prism-btc/live/demo.py: native parent·체결·취소·receipt·TP·backup 복구.
- prism-btc/live/swing.py: 단일 요청·exact 체결·native/backup 정산·경보.
- prism-btc/live/protection.py: 충분한 보호 no-op과 exact 주문 link 반환.
- prism-btc/live/exchange_snapshot.py: parent/entry link 상충 검사.
- prism-btc/live/tracking.py: 원자 receipt 저장용 선택적 commit.
- prism-btc/tests/test_demo.py, test_swing.py, test_native_stop.py,
  test_execution_latency.py, test_exchange_snapshot.py: 실제 계약·장애 회귀.

전략 신호·트레일 시간대·트레일 수치·메인 5%/스윙 1.5% 예산을 바꾸지 않았습니다.
주문/원장/상태의 연결을 보완했으며 새로운 의존성이나 상주 LLM 배치를 추가하지 않았습니다.

## 6. 남은 한계와 다음 단계

1. 실제 native attachment의 생성 시점·부분체결 보호 수량은 자연 발생한
   데모 진입을 통해 확인해야 합니다. 테스트·요청 파라미터만으로 보호 공백 0을 보장하지 않습니다.
2. 스윙 unknown-entry의 완전 자동 원장 재구성은 미완료입니다.
   pending 유지·재진입 차단·관측·운영자 경보가 우선입니다.
3. exact parent/child 연결이 없거나 부분청산이 여러 주문에 나뉘어 정산되면
   미확정으로 남을 수 있습니다. 추정 PnL을 만들지 않습니다.
4. 제출 직전 crash·거절·조회 지연 후 TP/backup이 unknown으로 남으면
   수동 exact 증거 확인이 필요할 수 있습니다. ACK·TTL·open-order 부재만으로
   meta를 지우거나 주문을 다시 내지 않습니다.
5. 공통 위험 예약·동일 mutex의 두 어댑터 연결은 아직 하지 않았습니다.
   helper 존재와 운영 위험 관리 완료를 구분합니다.
6. M1 운영 연결 전에는 논리 자본·별도 계정 정체성·전체 exposure/잔여 pending의
   일관 snapshot, 실제 주문의 가격 경계, 합산 한도 계약을 확정해야 합니다.
   합산 6.5%는 이전 문서상 제안이며 이번 작업으로 자동 승인·활성화하지 않습니다.
7. M2 체결·실제 펀딩·MTM NAV 패리티와 M4 여섯 시간대 트레일 비교는
   위 선행 조건 다음입니다. 수익성이 입증됐다고 주장하지 않습니다.

## 7. 배포/완료 기록

현재 상태: 별도 브랜치 검증 완료, PR/CI/배포 기록은 검증 후 갱신합니다.
M0 전체 완료나 M1 운영 활성으로 표시하지 않습니다.
