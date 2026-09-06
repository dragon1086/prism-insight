# BTC TP/SL 병합·데모 배포 기록

작성일: 2026-09-06 KST
상태: #662·#663 병합 및 db-server 데모 배포 완료. 실제 새 TP/SL 체결·수익성 검증은 별도입니다.

## 1. 사용자 승인과 리뷰 정책

사용자가 “앞으로도 필수리뷰 필요없으니 바로 머지 및 배포”를 명시적으로 지시했습니다.
이 요청을 PRISM-INSIGHT 저장소의 검증된 작업 범위에 적용했습니다.

- 최종 설정은 required_approving_review_count=0입니다. PR 경유 요구는 유지합니다.
- 변경 전 전체 protection JSON을 로컬 감사 파일로 보존했습니다.
- 변경 전후를 비교하여 필수 승인 수 1→0 외 설정이 동일함을 확인했습니다.
- 작업 중 review 항목 삭제 후, PR 요구 자체까지 없애지 않도록 기존 review 설정을
  복원하고 승인 수만 0으로 정리했습니다.
- required_status_checks, strict=true, 기존 CLA 및 다른 보호 설정은 유지했습니다.
- 관리자로 강제 병합한 것이 아니라 사용자 요청에 따라 리뷰 정책을 변경한 뒤 정상 병합했습니다.
- CI 실패·다른 저장소·미승인 실자금·새 위험 예산까지 포괄 승인한 것으로 해석하지 않습니다.
- 해당 정책은 AGENTS.md와 프로젝트 메모리에 남겼습니다.

## 2. TP 또는 SL 체결 시 반대 주문은 어떻게 되는가

### 부분 TP

현재 메인 TP1은 전체 포지션이 아니라 약 1/3의 익절입니다.
따라서 TP1이 모두 체결돼도 남은 약 2/3의 SL을 취소하면 안 됩니다.
남은 실제 노출 이상의 충분한 reduce-only 보호를 유지합니다.
기존 SL의 표시 수량이 반드시 잔량과 같은 숫자로 줄어드는 것은 아닙니다.

### 전량 종료

Bybit의 position-native TP/SL은 포지션 종료 시 취소되고, 포지션 크기에 따라
수량이 조정되는 계약입니다. 한쪽만 수정하면 기존 TP/SL binding이 해제될 수 있습니다.
[Bybit 공식 Trading Stop 문서](https://bybit-exchange.github.io/docs/v5/position/trading-stop)

PRISM의 메인 TP1 GTC limit과 보조 SL은 전부 하나의 native OCO 쌍으로 연결된 것이 아닙니다.
전량 종료 후 봇이 별도 주문을 정리하는 경로가 포함됩니다.
메인 독립 TP는 exact terminal 확인까지 상태를 유지합니다.
보조 SL의 취소 요청/로컬 handle 정리만으로 거래소 잔여 주문 0을 단정하지 않습니다.

취소 요청 ACK는 비동기 접수이며 실제 취소 완료와 다릅니다.
[Bybit 공식 Cancel Order 문서](https://bybit-exchange.github.io/docs/v5/order/cancel-order)

따라서 “하나만 체결되면 TP와 SL이 항상 동시에 사라진다”는 설명은 부정확합니다.
부분 익절은 SL 유지, 전량 종료는 잔여 주문 정리·실제 조회 확인이 기준입니다.
시장 수집·네트워크·정산 조회 실패가 있을 수 있어 정리 시간의 고정 SLA도 보장하지 않습니다.

## 3. 병합 증거

- [PR #662](https://github.com/dragon1086/prism-insight/pull/662)
  - 기능 head: c282526455aff598c3796d3201bf01f3f921a4ac
  - merge: d70018f539aa5ed07414f9d1a7cecbd277524f9d
  - 병합 시각: 2026-09-06 03:44:43 UTC
- [PR #663](https://github.com/dragon1086/prism-insight/pull/663)
  - 원 기능 head: ba91abddf8e19d44da44306fbb1ef052af60b477
  - #662 병합 후 main으로 base를 바꾸고 최신 main을 포함했습니다.
  - 재검증 head: 0194b0f8acf5be3e803d25b847afe5b80c42eceb
  - 기능 tree가 원 head와 같음을 확인하고 CI 7개를 다시 통과했습니다.
  - merge: 9ad38cd4f5a16e0eadd62aaa76ab5dc4d7ef01c4
  - 병합 시각: 2026-09-06 03:51:18 UTC

최종 기능 tree: b0cdb189d3165ce15f0d700393b2b936340eb3f6.

## 4. 서버 배포 전 검증

- 배포 전 서버 HEAD: 92c0f4b0c5b51d78cf0028ef9fa8a5a39fcb6562, clean.
- 양쪽 Bybit client가 api-demo.bybit.com임을 확인했습니다.
- positions continuation을 각각 끝까지 읽고 양 계정 포지션 0·open order 0을 확인했습니다.
- 로컬 demo/swing 보유와 entry/reduce/close/TP/SL 미해결 상태가 없음을 확인했습니다.
- 기존 shadow 포지션 1개는 가상 전략의 정상 상태로 보존했습니다.
- 실행 중 runner가 없고 다음 정규 tick 직전이 아님을 확인한 뒤 배포했습니다.
- app-server, 계정 키, .env, cron, 운영 DB를 직접 변경하지 않았습니다.

## 5. 서버 격리 테스트와 실제 반영

서버의 별도 git worktree에서 테스트했으므로 운영 DB·환경 파일을 테스트 입력으로 사용하지 않았습니다.
TCP 접속 차단을 걸었고 실제 차단 시도 횟수도 0이었습니다.

- 서버 Python 3.11.11
- 709 passed, 1 skipped, 26.24초
- skip 1개: 검증 worktree에 private real market.db를 복사하지 않은 실제 데이터 검사
- 기능 code SHA: 9ad38cd4f5a16e0eadd62aaa76ab5dc4d7ef01c4

배포:
- 시작/완료: 2026-09-06 03:56:41 UTC (12:56:41 KST)
- git merge --ff-only로만 반영했습니다.
- 반영 후 clean 및 Python 소스 15개 구문 검증을 확인했습니다.
- rollback source ref: ops/btc-before-9ad38cd4-20260906 (서버 로컬, 원격에 push하지 않음)
- cron SHA-256 전후 동일:
  e7fe2eda6a0d36e9f99b31d2da37747e0dcf4c7927e09aceaff4f9c6a1cdf26d

## 6. 자연 실행 확인

임의 runner 재실행이나 시험 주문을 만들지 않고 기존 cron의 자연 tick을 기다렸습니다.

- shadow 정상 heartbeat: 2026-09-06 04:01:04 UTC
- demo 정상 heartbeat: 2026-09-06 04:02:05 UTC
- 두 mode의 code_version: 9ad38cd
- 배포 시각 이후 btc_events의 error 0건
- 관측 시점의 demo/swing 로컬 포지션은 없고 기존 shadow 포지션 1개는 유지됐습니다.
- 04:05 UTC 양쪽 broker를 다시 조회해 포지션 0·open order 0을 확인했습니다.
  새 TP/SL 체결이 발생한 표본으로 간주하지 않습니다.

정상 heartbeat는 새 코드가 정규 파이프라인에서 실행됐다는 증거입니다.
실제 신규 진입·부분 TP·SL·모든 잔여 주문 취소가 앞으로 항상 성공한다는 증명은 아닙니다.

## 7. 남은 점과 유지할 경계

- 실제 부분 TP/전량 종료 시 exact fill·남은 SL coverage·잔여 주문 0을 전진 관측합니다.
- 보조 SL cancel의 terminal readback과 일부 unknown/manual 복구 경계는 개선 여지가 있습니다.
- 원가·수수료·펀딩·MTM NAV 완전 패리티와 6개 시간대 트레일 비교는 미완료입니다.
- M1 공통 위험 예약/직렬화와 합산 한도·슬리피지 확정은 이번에 활성화하지 않았습니다.
- 기존 journal·연구·일지·초분할 HTML 및 원래 WIP는 보존합니다.
- 이후 문서/운영 지침만 정리한 커밋은 위 기능 tree의 변경과 구분합니다.
