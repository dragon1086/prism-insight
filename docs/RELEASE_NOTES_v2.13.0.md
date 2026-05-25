# PRISM-INSIGHT v2.13.0 — Human-in-the-Loop Trade Approval + Mock KIS Test Stack

> **Release Date**: 2026-05-25
> **Branch**: `feat/approval-retry-handler` → `main`

## 개요

AI가 생성한 모든 매매 신호가 KIS에 도달하기 전 텔레그램 승인 게이트를 통과하도록 **HitL(Human-in-the-Loop) 승인 레이어**를 도입했습니다. 동시에 KIS Open API 스펙 워크북과 동일한 응답을 돌려주는 **인메모리 Mock KIS 서버**를 추가해 운영 자격증명 없이도 종단간 테스트를 돌릴 수 있게 했습니다. 두 인프라는 GitHub Actions에서 모든 PR/푸시 시점에 자동 검증됩니다.

기본값은 **OFF**입니다. `ENABLE_TRADE_APPROVAL`을 설정하지 않으면 기존 직접 매매 경로가 그대로 유지되어 회귀 위험이 없습니다.

## 주요 변경사항

### 1. HitL Approval Layer (Phase 2)

신규 `approval/` 패키지가 AI 매매 신호를 텔레그램 승인 카드로 감쌉니다. 운영자는 ✅/❌/📝 버튼으로 결정하고, 결정이 없으면 30분 후 자동 거절됩니다.

```
┌─────────────────────────────────────┐
│ 🟡 매수 승인 요청                    │
│                                     │
│ 삼성전자 (005930)                    │
│ 진입가: 70,000 원                    │
│ 손절가: 66,500 원                    │
│ 목표가: 78,000 원                    │
│ 투자금액: 500,000 원                 │
│ 신뢰도: 82점                         │
│                                     │
│ AI 근거:                             │
│ • 거래량 폭증 + 외국인 매수세         │
│ • 컨센서스 목표가 11% 상회 여력       │
│                                     │
│ 만료: 14:32:15 (30분 후 자동 거절)   │
└─────────────────────────────────────┘
  [✅ 매수 승인]  [❌ 거절]  [📝 금액 수정]
```

- **✅ 승인**: KIS 주문 즉시 실행 + Redis/GCP Pub/Sub 신호 재발행
- **❌ 거절**: 주문 무발송, SQLite에 `REJECTED` 기록
- **📝 금액 수정**: `MODIFY_REQUESTED` 기록 후 `/retry_<승인ID> <새금액>` 명령으로 새 카드 발급
- **자동 손절 예외**: `AUTO_STOP_LOSS_BYPASS=true` 시 stop-loss 매도는 즉시 실행 (`AUTO_EXECUTED` 기록)

모든 결정은 `trade_approvals` SQLite 테이블에 감사용으로 영구 보존됩니다.

### 2. `/retry_<승인ID> <금액>` 명령 (Phase 2 완성)

📝 금액 수정을 누르면 메모리에 원본 제안이 보관됩니다 (`metadata`인 `account_name`/`scenario`/`holding_qty`는 SQLite에 저장되지 않으므로 인메모리 보관). 사용자가 다음 명령을 입력하면 새 금액으로 즉시 재승인 카드가 발급됩니다:

```
/retry_abc123def456 300000
/retry_abc123def456 300,000     # 쉼표 허용
```

| 입력 | 응답 |
|------|------|
| 정상 ID + 양수 금액 | 새 PENDING 카드 즉시 발급 (새 `approval_id`) |
| 금액 누락 | "금액을 함께 입력해주세요" |
| 음수/0 금액 | "금액은 0보다 커야 합니다" |
| 알 수 없는 ID | "해당 수정 요청을 찾을 수 없거나 만료되었습니다" |
| 이미 재요청된 ID | 위와 동일 (stash가 한 번에 소진) |

stash는 manager의 `timeout_seconds` (기본 30분) 이후 자동 GC됩니다.

### 3. 다계좌 자동 라우팅 (KIS Executor)

승인 후 단일 executor가 `proposal.metadata["account_name"]`을 읽어 해당 계좌의 `AsyncTradingContext`로 매매를 실행합니다. 승인 레이어 자체는 다계좌 개념을 알 필요가 없으며, 호출자가 `account_name`만 metadata에 끼워 넣으면 라우팅이 자동입니다.

### 4. Mock KIS API Server (Phase 1)

`tests/mock_kis_server.py` — FastAPI 인메모리 서버. `KIS_ENV=mock` 환경변수만으로 모든 KIS 트래픽이 `http://127.0.0.1:8000`으로 라우팅됩니다. 실제 자격증명, 토큰 발급, 네트워크 호출 없이 종단간 테스트 가능.

- 토큰 발급(`/oauth2/tokenP`), 현재가, 잔고, 매수/매도/취소/예약주문 등 주요 엔드포인트 구현
- 잔고/포지션 상태는 인메모리 dict에 유지, 주문 시 자동 갱신
- 응답 스키마는 KIS Open API 스펙 워크북(339 시트)과 1:1 매칭

### 5. KIS Spec Contract Tests

`tests/test_spec_compliance.py` — `tests/fixtures/kis_api_spec.xlsx`를 파싱해 Mock 서버의 응답 필드를 스펙과 비교합니다. KIS가 필드를 추가/삭제/이름 변경하면 fail-loud 형태로 차이를 보고합니다. 스펙 워크북은 `tests/fixtures/kis_api_spec.xlsx`로 커밋되어 있어 CI에서도 그대로 실행됩니다.

### 6. GitHub Actions CI (`.github/workflows/ci.yml`)

신규 워크플로우가 모든 PR 및 main 푸시에서 다음을 자동 실행합니다:

| 잡 | 대상 | 의존성 |
|----|------|--------|
| `approval-layer` | `tests/test_approval_store.py`, `test_approval_handler.py`, `test_approval_integration.py` (커버리지 + JUnit XML) | pytest, pytest-asyncio, pytest-cov |
| `mock-kis-server` | `tests/test_mock_kis_server.py`, `test_spec_compliance.py` | fastapi, httpx, uvicorn, openpyxl |
| `ci-summary` | 두 잡 결과 집계, 하나라도 실패 시 워크플로우 fail | — |

전체 `requirements.txt`(pandas/playwright/mcp-agent)는 설치하지 않고 최소 의존성만 사용합니다. KIS·텔레그램·GCP 자격증명을 필요로 하는 기존 통합 테스트는 별도 잡으로 분리될 때까지 게이트되지 않습니다.

## 변경된 주요 파일

| 파일 | 역할 |
|------|------|
| `approval/__init__.py`, `models.py`, `store.py`, `db_schema.py`, `message.py`, `handler.py` | 신규 패키지 — TradeProposal, ApprovalManager, SQLite 영속화, 텔레그램 메시지 빌더 |
| `trading/approval_integration.py` | 신규 — Buy/Sell Specialist → TradeProposal 빌더, 단일 KIS executor, 신호 publisher 재호출, 텔레그램 핸들러 (`telegram_callback_handler`, `telegram_retry_handler`) |
| `stock_tracking_agent.py` | `ENABLE_TRADE_APPROVAL`로 매수/매도 경로 분기 (게이트 ON → 승인 큐, OFF → 기존 inline KIS 호출) |
| `telegram_ai_bot.py` | `CallbackQueryHandler(pattern=r"^apv:")` + `MessageHandler(filters.Regex(r'^/retry_[0-9a-fA-F]'))` 등록 |
| `tests/mock_kis_server.py` | 신규 — FastAPI Mock KIS |
| `tests/test_mock_kis_server.py`, `test_spec_compliance.py` | Mock 응답 + KIS 스펙 contract 검증 |
| `tests/test_approval_*.py` | ApprovalManager 27 cases (store / handler / integration) |
| `tests/fixtures/kis_api_spec.xlsx`, `README.md` | KIS Open API 스펙 워크북 + 갱신 가이드 |
| `.github/workflows/ci.yml` | 신규 — Phase 1/2 CI |
| `trading/kis_auth.py` | `KIS_ENV=mock`/`KIS_MOCK_URL` 분기, mock 디폴트 설정 |
| `.gitignore` | `.coverage`, `.claude/`, `trade_approvals.db` 등 추가 |

## 환경변수

```bash
# 승인 레이어 (기본 OFF)
ENABLE_TRADE_APPROVAL=false       # true로 설정하면 게이트 활성화
APPROVAL_DB_PATH=trade_approvals.db
APPROVAL_TIMEOUT_SECONDS=1800      # 30분 (수정 대기 stash와 공통)
AUTO_STOP_LOSS_BYPASS=false        # true면 stop-loss 매도는 즉시 실행

# Mock KIS (테스트용)
KIS_ENV=mock                       # 모든 KIS REST/WS 트래픽을 mock으로 라우팅
KIS_MOCK_URL=http://127.0.0.1:8000
KIS_MOCK_WS_URL=ws://127.0.0.1:8000
```

## 마이그레이션

DB 스키마 변경, 의존성 변경, KIS 자격증명 변경 모두 없습니다. `ENABLE_TRADE_APPROVAL`이 설정되지 않으면 기존 코드 경로가 그대로 동작합니다.

`trade_approvals` 테이블은 `ApprovalStore` 첫 인스턴스화 시 자동 생성됩니다(`init_schema`). 별도 마이그레이션 스크립트 불필요.

## 운영 활성화 절차 (권장)

게이트 활성화는 운영 정책 결정이므로 본 릴리스에서는 ON으로 전환하지 않습니다. 활성화 시 권장 순서:

1. **데모 모드에서 우선 검증**: `DEFAULT_MODE=demo` + `ENABLE_TRADE_APPROVAL=true`로 1~2주 운영
2. **텔레그램 모니터링**: `trade_approvals` SQLite를 주기 조회해 `PENDING` 비율, `EXPIRED` 비율, `MODIFY_REQUESTED → /retry` 전환율 확인
3. **자동 손절 정책 결정**: 손절 매도까지 사람이 확인할지 (`AUTO_STOP_LOSS_BYPASS=false`) 즉시 실행할지 (`true`) 별도 결정
4. **실거래 전환**: 데모에서 30분 timeout 적정성 확인 후 `DEFAULT_MODE=real`

## 알려진 제한사항

1. **수정 stash는 인메모리**: 봇 재시작 시 진행 중인 MODIFY 흐름은 유실됩니다. 사용자는 새 AI 신호를 기다려야 합니다.
2. **CI는 최소 의존성만 검증**: 전체 `requirements.txt` 설치가 필요한 통합 테스트는 별도 잡으로 분리될 때까지 게이트되지 않습니다.
3. **Mock 서버 ↔ 실제 KIS 동작 차이**: Mock은 스펙 응답 스키마는 일치하지만, 시세/거래량 등 시장 데이터는 정적입니다. 시세 의존 로직은 별도 단위 테스트 필요.
4. **`hts_kor_isnm` 모의투자 빈값**: KIS 모의투자 도메인은 현재가 응답에서 종목 한글명을 비워 보냅니다. 운영 코드에서 모의/실전 분기 시 참고 필요.

## 텔레그램 공지

### 한국어

```
🛡️ PRISM-INSIGHT v2.13.0 — Human-in-the-Loop 매매 승인 + Mock KIS 테스트 스택

🟡 AI가 생성한 모든 매수/매도 신호가 KIS 주문 전에
텔레그램 승인 카드로 표시되도록 게이트를 추가했습니다.
✅ 승인 / ❌ 거절 / 📝 금액 수정 3-버튼.
30분 응답 없으면 자동 거절.

🎯 핵심 기능:
1. 다계좌 라우팅: account_name metadata로 KIS 자동 분기
2. 금액 수정: /retry_<승인ID> <새금액>으로 재승인 카드 발급
3. 손절 예외: AUTO_STOP_LOSS_BYPASS=true 시 stop-loss는 즉시 실행
4. 감사 추적: 모든 결정이 trade_approvals SQLite에 영구 보존

🧪 부수적으로 추가된 Mock KIS API + KIS 스펙 contract 테스트로
실제 자격증명 없이 종단간 검증이 가능합니다. GitHub Actions에서
모든 PR/푸시가 자동 검증됩니다.

⚙️ 기본값은 OFF. ENABLE_TRADE_APPROVAL=true로 켜야 활성화됩니다.
운영 적용은 데모 모드에서 1~2주 검증 후 권장.
```

### English

```
🛡️ PRISM-INSIGHT v2.13.0 — Human-in-the-Loop Trade Approval + Mock KIS Test Stack

🟡 Every AI-generated buy/sell signal now passes through a Telegram
approval card before hitting KIS. 3 buttons: ✅ approve / ❌ reject /
📝 modify amount. 30-min timeout → auto-reject.

🎯 Key features:
1. Multi-account routing via `metadata["account_name"]`
2. Amount modification: /retry_<id> <new_amount> spawns a fresh card
3. Stop-loss exception: AUTO_STOP_LOSS_BYPASS=true lets stop-loss
   sells skip the human gate
4. Audit trail: every decision persisted to trade_approvals SQLite

🧪 Bonus infrastructure: in-memory Mock KIS API + KIS Open API spec
contract tests let CI run E2E without real credentials. GitHub
Actions gates every PR and main push.

⚙️ Default: OFF. Set ENABLE_TRADE_APPROVAL=true to enable. Recommend
1-2 weeks of demo-mode validation before flipping the switch on real
trading.
```

---

**Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>**
