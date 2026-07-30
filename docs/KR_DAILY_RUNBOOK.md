# KR 일일 제품 운영 Runbook

상태: Phase 1 읽기 전용 SHADOW 운영 절차. 브로커·계좌·메시지·예약 실행은 이 절차의 범위가 아닙니다.

## 1. 안전 경계

이 명령은 승인된 KIS 시장·재무 읽기, 공개 AgentNews, 기존 ChatGPT OAuth, 격리된 로컬 SQLite와 로컬 보고서/대시보드 파일만 사용합니다. 다음 기능은 사용하지 않습니다.

- 계좌번호, 잔고, 보유종목 조회
- 주문, 정정, 취소, broker paper 또는 live
- Telegram 등 외부 메시지 발송
- launchd/cron 등록 또는 활성화
- 리스크·점수·정책 변경

운영 UAT에는 기존 사용자 DB가 아닌 새 격리 디렉터리를 사용합니다. `.env`, 토큰 캐시, 원시 응답, DB와 생성 JSON/Markdown은 private user data로 취급하며 커밋하거나 업로드하지 않습니다.

## 2. 사전 확인

```bash
git status --short --branch
git rev-parse HEAD
python -m prism_app kr-daily --help
python -m prism_app stockeasy-capability
python tools/audit_broker_boundaries.py
```

`stockeasy-capability`이 `STOCKEASY_UNAVAILABLE`이면 핵심 KIS/PRISM 경로는 계속 실행합니다. `CONNECTED`는 승인 범위와 hash가 검증된 일회성 로컬 snapshot import를 뜻하며 상시 브라우저/API 연결을 뜻하지 않습니다. 자세한 계약은 `docs/STOCKEASY_RUNBOOK.md`를 따릅니다.

## 3. 격리 실행

```bash
UAT_DIR="$(mktemp -d /tmp/prism-kr-uat.XXXXXX)"
python -m prism_app kr-daily \
  --as-of "$(python3 -c 'import datetime; print(datetime.datetime.now().astimezone().isoformat())')" \
  --research-db "$UAT_DIR/research.sqlite" \
  --paper-db "$UAT_DIR/paper.sqlite" \
  --ops-db "$UAT_DIR/ops.sqlite" \
  --report-output "$UAT_DIR/kr-daily.md" \
  --dashboard-output "$UAT_DIR/dashboard.json"
printf 'UAT_DIR=%s\n' "$UAT_DIR"
```

승인된 StockEasy snapshot UAT에서는 아래 두 옵션을 **같이** 추가합니다. 하나만 주면 fail-soft `STOCKEASY_REJECTED`이며 핵심 경로는 계속됩니다.

```bash
  --stockeasy-snapshot "$PRIVATE_DIR/stockeasy_sanitized_snapshot_v1.json" \
  --stockeasy-permission-record "$PRIVATE_DIR/stockeasy_permission_record_v1.json"
```

`--as-of`는 현재-only 공급자를 과거로 재구성하는 옵션이 아니라 미래 시각 입력을 막는 상한입니다. 실제 PIT 의사결정 시각은 현재-only KIS 재무 응답 수신 후 고정됩니다. 같은 명령을 다시 실행하면 새 네트워크 관측이며 자동으로 same-snapshot replay가 되지 않습니다.

## 4. 필수 readback

최종 JSON에서 다음을 확인합니다.

- `status`: `COMPLETED`, `COMPLETED_WITH_POLICY_REJECTIONS`, `REPORT_ONLY`, `IDEMPOTENT_REPLAY` 중 하나인 경우에만 읽기 전용 실행 성공
- `broker_called=false`, `message_sent=false`, `schedule_activated=false`
- `uat_accepted=false` (사용자가 승인하기 전까지 유지)
- 원천 주장 수, stable unique identity 수, 제외/무효/절단 수
- 각 후보의 provider source와 PRISM `candidate_status`
- SWING/TREND의 동일 `data_snapshot_id`, 별도 feature/score ID와 버전
- 점수 구성, 정확 재합산, 임계값 실제값/연산자/통과 여부/veto
- DB/report/dashboard의 symbol, stable `security_id`, snapshot, 전략 상태 일치
- StockEasy를 공급했다면 `CONNECTED / SITE_AVAILABLE / IMPORTED`, 네 필수 행, 실제 sanitized observation, clocks/hash, `price_authority=KIS_KRX`, 삭제 검증 일치

DB는 읽기 전용으로 조회합니다.

```bash
python - "$UAT_DIR/research.sqlite" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for table in (
    "feedback_runs", "decision_snapshots", "trade_plan_proposals",
    "proposal_disposition_events",
    "process_quality_outcomes", "proposal_outcomes", "retrospective_events",
    "lesson_candidates", "lesson_evidence_events",
):
    print(table, con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
PY
```

## 5. 상태 해석

- `POLICY_REJECTED`: 모델 응답은 파싱되었지만 결정론적 veto가 적용되었습니다. `NO_ENTRY`로 바꾸지 않습니다.
- `REPORT_ONLY`: 분석은 참고할 수 있지만 신규 진입 판단에 사용할 수 없습니다.
- 시장 국면·폭·수급·주도 섹터가 불완전하면 정확한 진입·손절·목표 가격을 사용자 행동 수준으로 공개하지 않습니다. DB에 보존된 LLM 후보도 주문 승인이나 권고가 아닙니다.
- PENDING outcome row가 0이면 “만기 대기 중”이라고 주장하지 않습니다. 이는 prospective maturity 등록이 실제 daily runtime에 연결되지 않았다는 뜻입니다.
- real lesson/support/contra/retrieval row가 0이면 “다음 실행이 교훈을 재사용했다”고 주장하지 않습니다. fixture로 검증된 메커니즘과 실제 runtime 증거를 분리합니다.

## 6. 장애와 복구

- 후보 실패, invalid/incomplete readback, publication 실패는 성공으로 덮지 않습니다.
- KRX 장애와 명시적 coherent fallback은 원천/품질과 함께 표시합니다.
- OAuth 포트 충돌 등 candidate-wide 오류는 모든 후보 실패로 기록하고 UAT 증거로 사용하지 않습니다.
- same-snapshot recovery는 공급자를 다시 호출하지 않는 frozen invocation/snapshot replay로 별도 검증합니다.
- 임시 private artifact는 사용자 검토가 끝난 뒤 명시적으로 삭제합니다. 삭제 전 hash와 경로를 UAT 기록에 남기되 원시 내용을 문서/채팅에 복사하지 않습니다.

## 7. 사용자 UAT 종료 조건

사용자는 `docs/KR_DAILY_USER_UAT_PACKAGE.md`와 실제 private `kr-daily.md`/`dashboard.json`을 직접 확인합니다. 승인 시에도 broker/live, 메시지, 예약, 리스크 변경은 활성화되지 않습니다. 사용자가 승인 문구를 남기기 전에는 제품 상태를 `UAT 승인 완료` 또는 `operated ready`로 표시하지 않습니다.

2026-07-30 체크리스트는 manifest hash와 일치하는 보존된 frozen artifact에 대한 승인입니다. 해당 임시 파일이 사라졌거나 hash가 다르면 그 실행은 UAT할 수 없습니다. 이 경우 새 격리 실행을 만들고 새 artifact의 candidate/snapshot/score로 별도 UAT를 수행하며, 새 관측을 2026-07-30 frozen run의 재현 또는 same-snapshot replay라고 부르지 않습니다.
