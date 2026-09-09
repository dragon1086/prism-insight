# Astra BUY 운영 반영 전 안전 계약

## 범위

2026-09-09 사용자 요청에 따른 사전 보강이다. 운영 서버 기준선은 `3b63370d`이다.
모델·CLI·cron의 운영 전환은 이 변경과 별개다. BTC, 보고서 모델, 매매 점수와
regime 기준, 계좌별 슬롯·주문 순서는 변경하지 않는다.

검증 후보는 Astra/high/Fast, BUY timeout 240초, 최대 3개 동시 분석이다.
고정 입력 24회에서 매수 평균 high 44.3초/xhigh 129.1초였고, 별도 MCP 병렬
시험에서 한국 3건 121.0초, 미국 3건 135.6초를 관측했다. 작은 표본의 기술
통계이며 수익성·모델 동등성·운영 p95 또는 240초 완료 보장이 아니다.

## 기본값과 설정 분리

`prism_core.codex_config.resolve_buy_codex_settings()`만 BUY 설정을 읽는다.

- `PRISM_BUY_CODEX_MODEL`: 미설정 시 `gpt-5.6-sol`.
- `PRISM_BUY_CODEX_EFFORT`: 미설정 시 CLI 모델 기본값. 임의 medium으로 표시하지 않는다.
- `PRISM_BUY_CODEX_TIMEOUT`: 미설정 시 `PRISM_CODEX_FAST_TIMEOUT`, 그것도 없으면 90초.
- timeout은 유한한 `(0, 600]`초만 허용한다. 모델·effort는 허용 목록으로 검증한다.
- SELL은 기존 sol, 기존 shared timeout, 기존 implicit effort를 유지한다.
- Fast는 기존 CLI 명령의 명시 설정을 유지한다. 로그의 모델·effort·tier는 요청값이지
  실제 backend snapshot이나 청구 tier 확인값이 아니다.
- 기존 legacy sol fallback과 KR 마지막 보조모델 재시도는 유지한다.

`.env.example`의 새 항목은 주석 상태다. 이 코드를 배포해도 Astra/병렬도3/240초가
자동으로 활성화되지는 않는다. KR 기본 병렬도4, US 기본2도 보존한다.

## 프로세스 소유권

BUY는 `generate_codex_fast_async()`를 사용한다. 취소 시 thread에 신호를 보내고
정리가 끝난 후 취소를 전파한다. 동기 함수도 timeout 시 본인이 만든 프로세스 그룹에
TERM, 짧은 유예, KILL을 적용하고 직접 자식을 회수한다.

- POSIX에서는 `start_new_session`으로 만든 그룹만 종료한다. 다른 PID를 탐색·종료하지 않는다.
- 반복 취소도 정리를 중단하지 않는다. timeout·취소는 legacy 판단 성공으로 처리하지 않는다.
- Windows는 직접 자식 정리만 보장한다. 운영 대상 db-server는 Linux다.
- 모델 호출 timeout은 fallback 재시도까지 포함하는 전체 후보 deadline이 아니다.

KR도 US와 같이 전체 legacy MCP host 수명을 agent-instance lock으로 직렬화한다.
LLM 대기 동안 DB lock을 유지하지 않는다. 세 Codex 호출이 함께 실패해도 legacy
host가 동시에 세 개 시작되지 않도록 회귀 테스트한다.

## 매도 우선과 순차 실행

US는 모든 계좌의 `update_holdings()`를 BUY pre-pass 전에 실행한다. 그 후 공유
분석 계좌를 primary로 복원하고 BUY 분석을 병렬 실행한다. BUY 적용은 기존 보고서
순서와 계좌별 보유·슬롯·섹터 게이트를 유지한다. 매도 검토가 실패한 계좌의 BUY는
건너뛰되 다른 계좌의 검토와 기존 위험 관리는 계속한다.

이 변경은 별도 시장/하드스탑 루프를 대체하지 않는다. 다른 루프가 배치 지연의
위험을 모두 해결한다고 가정하지 않는다.

## 가격 재조회와 원장 독립성

분석 때 확보한 가격을 무조건 최종 BUY 가격으로 재사용하지 않는다.

- KR: 읽기 전용 KIS 시세를 우선하고, 사용할 수 없으면 별도로 조회한 당일 KRX
  데이터로 simulator-only 경로를 지원한다. 저장된 보유/분석 DB 가격으로 되돌아가지 않는다.
  KRX 당일 행이 없으면 과거 거래일 값을 임의 허용하지 않는다.
- US: 계좌별 적용 직전 독립적인 시장 시세를 다시 조회한다. broker intent 생성 전에도
  엄격한 KIS quote를 읽으며 이전 기준가 `base`로 대체하지 않는다.
- 유효한 양수·유한수인지 확인하고, 기존 target/stop 관계와 기존 진입 게이트를
  재검증한다. 새로운 slippage·수익률·나이 임계값은 추가하지 않는다.
- 목표가·손절가·점수를 시세에 맞춰 유리하게 이동시키지 않는다. 기존 산술 불일치
  검증도 유지한다. `entry_price`는 검증한 시세와 일치시키고 분석 기준값을 별도로 남긴다.
- broker 주문 실패나 strict quote 실패로 독립적인 simulator 기록을 취소하지 않는다.
  유효한 시장 가격 자체를 확보하지 못한 경우는 가격 검증 실패로 처리한다.
- 기록하는 retrieval/context age와 거래소 데이터 age를 구분한다. provider의 검증 가능한
  timestamp가 없으면 exchange age는 unknown이다. 재조회가 거래소 실시간성 보장은 아니다.

## 출력·프롬프트 계약

`prism_core.trading_scenario_contract`는 매매 결론·점수를 수정하지 않는다.

- ENTRY는 유한한 양수 가격/손익비 필드와 `stop < fresh price < target`이 필요하다.
  잘못된 ENTRY는 실패로 식별하고 거래하지 않는다.
- NO ENTRY의 결측 가격·손익비는 `null`로 보존한다. 메시지·저장 경로가 이를
  처리하도록 회귀 테스트하며, 검증하지 못한 가격을 0으로 꾸미지 않는다.
- 저장할 `sell_triggers`는 기존 정책을 참조하는 고정 템플릿으로 제한한다.
- SELL instruction은 저장된 sell_triggers/hold_conditions/rationale가 참고 근거이며
  현재 매도·trailing 정책을 덮어쓰지 못함을 명시한다.
- BUY의 손익비 후보는 기존 문구의 가장 가까운 저항과 그 다음 저항 범위다. floor를
  통과시키려고 세 번째 저항까지 확장하거나 새 7% trailing/5일선 전량매도 규칙을 만들지 않는다.
- 데이터 불일치는 기존 기준의 충족 여부에 미치는 영향으로 구분한다. 중요하지 않은
  차이에 독립적인 감점·차단을 추가하지 않고, 중요한 충돌·결측을 사실처럼 처리하지 않는다.

합성 문구 비교 8회에서 기존·명확화 문구의 결정은 같았다. 따라서 이 명확화가
수익성이나 진입률을 개선했다는 주장은 하지 않는다. 핵심 기준 완화도 아니다.

## 사전 검증과 운영 체크리스트

1. backend/settings/process-tree timeout·취소 회귀 테스트.
2. KR/US BUY 설정 변경이 SELL 호출 인자에 영향을 주지 않는지 확인.
3. 세 동시 실패의 fallback 직렬화, DB lock 해제와 취소 전파 확인.
4. 모든 US 계좌의 SELL이 지연된 BUY보다 먼저 실행되는지 확인.
5. 병렬도3·완료 순서 뒤집힘에서도 계좌별 BUY 적용 순서 보존 확인.
6. fake quote/broker 및 임시 DB로 새 시세·invalid quote·simulator 독립성·null 처리 확인.
7. KR/US 테스트는 shadowed package 때문에 별도 프로세스에서 실행한다. 테스트에 실제
   계좌·DB·메시지 발행을 사용하지 않는다.
8. 운영 적용 때는 clean worktree, 검증 commit, ff-only 배포 계약을 지킨다. 별도 설치한
   Codex CLI 0.153.4의 OAuth·MCP 동작을 확인한 뒤 해당 바이너리를 명시한다.
9. BUY 후보값과 KR/US 병렬도3을 명시하되, SELL 기존값과 다른 cron은 유지한다.
10. 첫 실제 배치의 요청 모델/effort, latency, fallback, quote source/age, 계좌별 SELL
    선행, 주문 순서를 관측한다. 이는 사전 단위 테스트로 대체할 수 없다.

## 롤백

검증 전에는 운영 변경을 하지 않는다. 향후 전환 후 롤백 시 BUY override 제거 또는
기존 sol 설정 복원, 기존 Codex 바이너리 및 병렬도 복원으로 모델 경로를 되돌린다.
이미 생성된 주문·체결·원장은 Git으로 되돌리지 않는다. runtime 설정 백업은 비추적
안전 경로에 보존하며 보고서/저장소로 비밀값을 복사하지 않는다.
