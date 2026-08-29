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

