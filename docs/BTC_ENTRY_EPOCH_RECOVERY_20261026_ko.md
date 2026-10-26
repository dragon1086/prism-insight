# M0.3 00:16 미확정 진입의 추가 격리 복구

## 사건과 실행 범위

대상은 `s_55d333c6722dba9c167c4c34` / `sc_b7cafdb91c0d43da95926732c1a850b4` 한 건이다.
#1130은 전향적 재발 방지이며 이미 남은 UNKNOWN을 해제하지 않았다. 09:15에도 539분 경보가 계속됐다.
사용자는 1급 장애로 목표 달성과 신속한 배포·복구를 요청했다. 기존 .001 Sell reduceOnly 사건과
이번 .025 Buy Limit 사건의 미확정 사실을 둘 다 보존하는 별도 전환을 적용한다.
빈 조회로 취소/거절/미전송을 소급 확정하지 않으며 손익·체결 자료를 만들지 않는다.

현재 계좌가 비어 있다는 관측과 원 주문이 접수되지 않았다는 주장은 다르다. 늦은 Buy는
포지션을 만들 수 있다. 추가 격리는 거래소의 종결 증명이 아니라 이 미확정을 보존한 행정적 복구다.
새 구간에서도 두 link·과거 금융 구간·계좌 소유권을 감시하고 지연 증거/조회 실패는 새 진입을 막는다.

## 구현

- 기존 `incident_epoch_v1` 영수증은 수정하지 않는다. 정확한 이번 진입만
  `incident_entry_epoch_v1`에 선행 영수증 해시와 결박한다. 추가 영수증은 한 건만 허용한다.
- 두 사건의 intent/child/계약/미정산 상태는 원문 그대로 유지한다. 조회에서 제외하는 범위는
  영수증의 정확한 사건 집합이며, 모든 UNKNOWN을 제외하지 않는다. 건강 점검은 보존 건수를 표시한다.
- 새 epoch를 control v2에 기록하므로 기존 epoch에 묶인 worker와 구버전 receipt 해석은 전송 전에 실패한다.
  실제 전환은 scheduler 정지/복원, worker 부재, 완료되지 않은 전송 없음의 호스트 증거도 요구한다.
- 이번 entry는 만료된 단일 OPEN과 원 요청/목표/수량/SL이 일치해야 한다. 정확 주문 조회,
  전체 계좌 두 번 완전 조회, 일일 위험·금융 근거, 기존 봉인 무변경을 검증한다.
- 새 상태는 OBSERVING stage0, 위험은 기존값과0.5% 중 작은 값, 자동 승격 금지다.
  이전3연패 latch·일일 원장·위험 한도·전략은 그대로 둔다. 정상 관측/판단은 정기 runner가 수행한다.
- 기존 두 사건의 금융 cursor는 각 epoch에 묶는다. 두 원본의 변경은 전환 트랜잭션 안에서도 다시 확인한다.
  DB 오류·변조·부분 실패 시 새 영수증/control/state 전환을 원자적으로 되돌린다.

## 검증·완료 기준

오프라인 검사: 두 사건 원본 보존, 첫 WAIT와 후속 새 OPEN, 이전 worker 차단, 두 사건의 늦은
주문/체결 감지, 승인/선행 해시/포지션/조회 변화 거부, DB 실패 원자성, 영수증 변조, 경보 의미를 확인한다.
테스트 주문은 메모리 가상 거래소에서만 만든다. 전체 검사는 PR CI에 맡긴다.

실제 완료는 CI8개 SUCCESS → clean ff-only 배포 → 전환 영수증/cron 복원 → 첫 정기 WAIT 또는
정상 판단 확인 → 새 구간24시간 execution_stalled0 및 incident_epoch_halted0 순서로 기록한다.
24시간이 지나기 전에는 완료라고 보고하지 않는다. 이전 구간의 실패한 경보 기록은 보존한다.

구현 후 변경 범위6개 테스트 파일의 오프라인398개 검사가 통과했다. 본문에 적은
실제 전환·정기 실행·24시간 관측은 아직 이 테스트 결과에 포함되지 않는다.

## 실제 전환 결과 (2026-10-26 KST)

- PR1133 head9d345d7d의 상태 검사8개 전부 SUCCESS 후5e4a0f0a로 병합·배포했다(10:42:41).
- 10:44:34 추가 전환 COMMITTED. epoch `orca-entry-20261026-1792979064888243035`.
  두 사건 snapshot과 원 UNKNOWN 보존, 현재 pending 차단0, cron 전후 동일을 확인했다.
- 10:45 정기 WAIT와10:50 배치의10:51 WAIT가 확인됐다. 후속 tape에서도 first_observation=false,
  초안/선택2호출 COMPLETE·WAIT를 확인했다. 실제 체결을 만들기 위해 주문을 강제로 내지 않았다.
- 실제 첫 검증 서비스는 Result=success/exit0/JSON PASS: 새 UNKNOWN0, HALT0, 오류0이었다.
  검증을 끝낸 첫 timer는 중지했고24시간 단발 timer는 active다(10/27 10:44 KST 이후).
- 원 관측 구간은 실행 정체가 재발해 실패한 것으로 보존하고 구24시간 timer를 중지했다.
  새24시간 결과는 아직 미래다. 현재 복구와24시간 안정성 완료를 구분한다.

서버 근거: `/root/prism-ops/btc-entry-epoch-20261026/`의 deployment.json, verification-baseline.json,
post-cutover.json, verification-first.json, second-observation.json, preservation-audit.json, schedules.json.
로컬 사본은 `tasks/btc-entry-epoch-20261026/`에 있다. 이 기록은 과거 거래소 거절/취소/정산 증명이 아니다.
