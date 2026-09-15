# 운영 서버 Git 계약

> 최종 정상화: 2026-08-29
> 기준 commit: `be6e4db8`

## 현재 서버 상태

- db-server: `/root/prism-insight`, branch `main`, `origin/main`과 동기화
- app-server: `/home/prism/prism-insight`, branch `main`, `origin/main`과 동기화
- prism-backend: ClickStack exporter 파일 배포 대상이며 Git checkout이 아님

2026-08-29 정상화 때 기존 상태는 다음 로컬 backup branch와 서버 외부 경로에
보존했습니다. 이 branch는 원격에 push하지 않습니다.

- db-server branch: `ops/pre-clean-db-20260829_144159`
- db-server backup: `/root/prism-server-backups/db-20260829_144159`
- app-server branch: `ops/pre-clean-app-20260829_144342`
- app-server backup: `/home/prism/prism-server-backups/app-20260829_144342`

backup에는 정리 전 status, tracked patch, cron, 환경 설정 backup, checksum이 있습니다.
비밀값이 포함될 수 있으므로 저장소나 채팅으로 복사하지 않습니다.

## 정상 배포 절차

### 변경 범위별 검증 게이트 (필수)

기능 수정·장애 수정·배포 요청을 받으면 AGENTS.md가 이 절차를 적용하도록 지시한다.
장기 메모리 회상에 성공했는지와 무관하게 적용한다. 문서 자체가 서버에서 명령을
자동 실행하는 것은 아니다. 아래 CI 게이트는 PR마다 자동 실행되며, 운영 스모크는
담당 에이전트가 실행하고 증거를 확인해야 하는 배포 완료 조건이다.

1. **변경 전:** 영향받는 입력 → 호출자 → 출력 소비자를 확인하고, 장애라면 실제
   오류/응답 구조를 재현하는 회귀 테스트를 먼저 만든다. 테스트 개수만으로 판단하지 않는다.
2. **배포 전:** 변경 범위의 단위·통합 테스트와 정확한 PR head의 필수 CI를 확인한다.
   관련 검증 실패/미실행을 성공으로 취급하지 않는다. 기존 실패는 변경 전 재현 증거와
   이번 변경과의 관계를 기록한다. 배포 대상이 clean이며 확인한 commit인지 검사한다.
3. **배포 후:** 운영 Python에서 관련 import/구문 및 안전한 스모크를 실행한다.
   서비스 상태, 새 오류, cron/설정 불변을 확인한다. 실패하면 배포 완료로 보고하지 않고
   원인을 수정하거나 범위를 한정한 안전한 복구 절차를 적용한다.
4. **완료 보고:** 테스트, CI, 서버 스모크, 첫 정규 배치 결과를 구분한다. 첫 배치가
   아직 실행되지 않았으면 반드시 미관측이라고 쓴다. 스모크를 정규 배치 성공으로 부르지 않는다.

변경 범위에 맞춰 다음 검증을 추가한다.

- **스크리닝/시세 공급자:** flat/MultiIndex 양방향/빈 응답/중복/다중 종목을 다룬다.
  실제 KR 소비자와 US 오전·오후 배치→JSON 통합 테스트를 실행한다. AST 추출 단위
  테스트만으로 대체하지 않는다. 운영에서는 제한된 읽기 전용 실제 시세 스모크를 확인한다.
- **보고서/메시지:** 정상·실패 출력과 마지막 소비 경계(PDF 추출/메시지 렌더링 등)를
  검증한다. 내부 경로·예외·비밀값 노출과 실패 결과의 정상 캐시 저장을 점검한다.
- **매매/스케줄/정책:** TRADING_CHANGE_REVIEW_HARNESS.md를 함께 적용하고, 주문 없는
  격리 테스트로 경계·중복 집행·기존 조건 보존을 확인한다. 실제 주문으로 시험하지 않는다.
- **문서 전용:** 실행 코드/설정이 변경되지 않았음을 diff로 확인하고 참조와 CI를 점검한다.
  문서 변경을 핑계로 실시간 시세 조회나 운영 배치 재실행을 추가하지 않는다.

`.github/workflows/ci.yml`의 **KR/US provider-shape batch integration gate**는
PR마다 실제 모듈 통합 테스트를 자동 실행한다. 관측 출력은 임시 경로로 격리하고
테스트 네트워크/주문/채널 발송을 차단한다. 이 검사를 제거하거나 건너뛰어 머지하지 않는다.
새로운 기능 경계가 생기면 해당 회귀를 CI에 연결한다. LLM·정기 모니터링을 추가하는 규칙은 아니다.

항상 대상 서버에서 다음 순서를 지킵니다.

```bash
git status --short --branch
git fetch origin main
git merge --ff-only origin/main
git status --short --branch
```

사전 `git status`가 clean이 아니면 merge하지 않습니다. 먼저 변경을 다음처럼 분류합니다.

1. tracked source 변경
2. runtime 데이터가 잘못 tracked seed에 기록된 경우
3. untracked source 또는 비밀 파일
4. ignored runtime·log·DB 파일

분류와 보존 없이 `reset --hard`, `clean`, stash, checkout으로 지우지 않습니다.

## Runtime 데이터

정상 실행은 tracked seed를 수정하지 않습니다.

- 종목 map: `runtime/stock_map.json`
- US 거래소 cache: `runtime/us_exchange_cache.json`
- event spool: `logs/prism_events.jsonl`
- SQLite, log, report, backup: `.gitignore`의 runtime 규칙 적용

상세 경로와 fallback은 [`RUNTIME_DATA_PATHS_ko.md`](RUNTIME_DATA_PATHS_ko.md)를
따릅니다.

## 서버별 검증

### db-server

- 핵심 Python module `py_compile`
- 관련 targeted pytest
- Entry Quality read-only trigger prior와 Evidence Packet 생성
- `prism-observability-shipper`, tunnel, archive API 상태
- 다음 BTC shadow/demo cron tick의 traceback 유무
- cron hash와 운영 DB·spool 존재 확인

db-server는 `OPENAI_SERVICE_TIER=priority`를 ignored `.env`에 명시합니다. 코드 기본값은
`default`이므로 다른 서버로 Fast tier가 전파되지 않습니다.

### app-server

- 핵심 Python module `py_compile`
- dashboard production build
- `prism-dashboard`, archive tunnel 상태
- Telegram bot 단일 PID와 `Application started`
- 내부·외부 dashboard HTTP 200

app-server 운영 Python에는 pytest가 없으므로 회귀 테스트는 배포 전 로컬에서 실행하고,
서버에서는 compile·build·service smoke로 검증합니다.

## 금지사항

- 서버 worktree에 `scp`로 source를 장기간 덮어쓰지 않습니다.
- 운영 중 생성된 stock map·exchange cache를 tracked seed에 저장하지 않습니다.
- backup branch를 origin에 push하지 않습니다.
- 분석 작업이 서버 Git 상태를 reset하거나 clean하게 만들지 않습니다.
- SHADOW/LIVE 승격 때문에 서버 파일을 직접 고치지 않습니다. 검증된 commit을 배포합니다.

## Rollback

새 commit에 문제가 있으면 먼저 서비스를 안전하게 멈추고, 위 backup branch와 외부
checksum backup을 이용해 필요한 tracked/runtime 상태만 복원합니다. 운영 DB나 `.env`를
Git 명령으로 복원하지 않습니다. 원인 수정은 로컬에서 테스트·commit한 뒤 다시
ff-only 배포합니다.
