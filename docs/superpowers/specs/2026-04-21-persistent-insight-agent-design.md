# Persistent Insight Agent — 설계 문서

- **문서 ID**: `2026-04-21-persistent-insight-agent-design`
- **작성일**: 2026-04-21
- **관련 PR**: #262 (`feat/archive-insight-query-system`)
- **관련 기존 문서**:
  - `docs/archive/ARCHIVE_DEPLOY_GUIDE.md` — 운영 배포 절차
  - `docs/archive/ARCHIVE_API_SETUP.md` — 양 서버 API 분리 구성
  - `docs/archive/ARCHIVE_VALIDATION.md` — 기존 검증 시나리오

---

## 1. 목적과 배경

### 1.1 사용자 스테이트먼트

> "지금까지 쌓아온 리포트 데이터를 기반으로 장기투자로 가져갈 만한 종목들의 특징을 뽑고 싶거든. 그래야 그 특징을 기억해놨다가 다음 투자에도 쓰지."

### 1.2 PR #262 (Archive 시스템) 대비 갭

현 archive 시스템은 **회고적 검색 엔진**(retrieval + 합성)으로, 다음이 부족:

1. **특징 구조화 저장 부재** — `report_enrichment`는 *결과(outcome)*만 저장. *원인 특징(feature)*은 MD에 묻힘.
2. **학습 패턴 휘발** — `insights` 테이블은 24h TTL 쿼리 캐시. 축적된 지식 없음.
3. **역매칭 경로 부재** — 새 종목이 winner 패턴과 매칭되는지 자동 판별 불가.
4. **외부 도구 통합 부재** — 실시간 가격/뉴스 결합 질의 불가.

### 1.3 목표

프리즘인사이트 토론방의 `/insight` 대화에서 생성되는 모든 인사이트를 영구 저장하고, 누적 지식 + 외부 도구를 조합해 종목 장기투자 적합성을 판단하는 에이전트를 구축.

---

## 2. 의사결정 기록

### 2.1 스코프 결정

**질문**: 이 기능을 PR #262에 포함할지, 별도 PR로 분리할지?

- 옵션 A: PR #262 그대로 머지 + Phase B 별도 PR (리스크 최저)
- 옵션 B: 모두 PR #262에 포함 (지연 +1~2주)
- 옵션 C: #262 머지 후 1주일 운영 관찰 → 설계 재조정

**결정**: **옵션 B** — 사용자 판단에 따라 한 PR에 통합.

### 2.2 의사결정 체인

| # | 결정 항목 | 선택 | 근거 |
|---|---|---|---|
| Q1 | 저장 위치 | `archive.db` + 신규 테이블 | 리포트와 한 DB, FTS 인프라 재활용, evidence JOIN 자연스러움. `stock_tracking_db.sqlite`는 거래 기록용으로 성격 불일치. |
| Q2 | 저장 granularity | 하이브리드 (Q&A 원문 + key_takeaways) | 원본 보존 + 검색 효율 모두 확보. 추가 토큰 비용 무시 가능. |
| Q3 | 검색 전략 | FTS5 → OpenAI embedding 재랭킹 + 주간 요약 티어 | 의미 유사도 필요 + 정확 매칭(종목명) 안전망 + 컨텍스트 팽창 압축. |
| Q4 | 커맨드 UX | 단일 `/insight` + mcp-agent function calling | 사용자 편의성 극대, 질문 유형 경계 없음, 기존 MCP 패턴 재사용. |
| Q5 | 외부 도구 비용 가드레일 | 프롬프트 가드레일 (D) + 일일 쿼터 (B) | 관찰 우선 원칙. 실데이터 기반으로 정책 조정. |
| Q6 | 프라이버시 모델 | 공용 풀 (user_id만 기록) | 토론방 컨셉과 일치. 그룹 채팅은 이미 공개. |

### 2.3 사용자 명시 제약

- **Firecrawl**: 사용 빈도 엄격 제한. `scrape` 사용 시 `formats=["markdown"]`, `onlyMainContent=true`. `search`는 정말 필수적인 경우만.
- **BotFather 명령어 메뉴**: 봇 기동 시 `set_my_commands` API로 동기화 필수.
- **커맨드 UX**: `/insight <query>` 인라인 방식 금지. 기존 `/evaluate` 패턴처럼 명령어 먼저 → 응답 유도 → Reply로 후속 대화.
- **Foreign Key**: 쓰기 성능 저하 우려로 FK constraint 제거, 정합성은 애플리케이션 레이어 담당.

---

## 3. 아키텍처 개요

```
┌─────────── 텔레그램 그룹 ────────────┐
│ 사용자: /insight                     │
│ 봇:    "질문을 입력해주세요"         │
│ 사용자: 삼성전자 장기투자 어때?      │
└──────────┬──────────────────────────┘
           ▼
┌─ app-server (prism) ────────────────┐
│ telegram_ai_bot.py                  │
│   ConversationHandler /insight      │
│   ↓                                 │
│ HTTP POST /insight_agent            │
│   (SSH tunnel → db-server)          │
└──────────┬──────────────────────────┘
           ▼
┌─ db-server (root) ──────────────────────────────────────┐
│ archive_api.py                                          │
│   /insight_agent endpoint (신규)                        │
│   ↓                                                     │
│ cores.archive.insight_agent.InsightAgent                │
│   (mcp-agent + gpt-5.4-mini)                            │
│   ├─ tool: archive_search_insights (FTS+임베딩)         │
│   ├─ tool: report_retrieve                              │
│   ├─ tool: yahoo_finance (free)                         │
│   ├─ tool: kospi_kosdaq (free)                          │
│   ├─ tool: perplexity (paid, 1x 권장)                   │
│   └─ tool: firecrawl (paid, 엄격 가이드)                │
│   ↓                                                     │
│ StructuredOutput { answer, key_takeaways, tickers, … }  │
│   ↓                                                     │
│ persistent_insights INSERT (fire-and-forget)            │
│   + embedding 생성                                      │
│   + insight_tool_usage 로깅                             │
│   + user_insight_quota ++                               │
└──────────────────────────────────────────────────────────┘

배경 잡 (기존 auto_insight weekly 확장):
  매주 월 03:00 → 지난 7일 persistent_insights → weekly_insight_summary 생성
                + superseded_by 업데이트
```

### 3.1 핵심 설계 원칙

- 기존 `/query` 엔드포인트는 유지 (단순 검색 경로 보존)
- 단일서버 / 양서버 모드 모두 지원
- 임베딩 저장소는 SQLite BLOB 컬럼 + 브루트포스 cosine (10k 이하 충분)
- 추후 50k+ 도달 시 sqlite-vec 이관 검토

---

## 4. 데이터 모델

```sql
-- archive.db 신규 테이블 5종

CREATE TABLE IF NOT EXISTS persistent_insights (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER,
    chat_id             INTEGER,
    question            TEXT NOT NULL,
    answer              TEXT NOT NULL,
    key_takeaways       TEXT NOT NULL,               -- JSON array of strings
    tools_used          TEXT,                        -- JSON array
    tickers_mentioned   TEXT,                        -- JSON array
    evidence_report_ids TEXT,                        -- JSON array (report_archive.id)
    embedding           BLOB,                        -- float32 × 1536
    model_used          TEXT,
    previous_insight_id INTEGER,                     -- 멀티턴 체인
    superseded_by       INTEGER,                     -- weekly_insight_summary.id
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pi_chat    ON persistent_insights(chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_created ON persistent_insights(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS persistent_insights_fts USING fts5(
    question, key_takeaways,
    content='persistent_insights', content_rowid='id',
    tokenize='unicode61'
);

-- 트리거로 FTS 자동 동기화 (archive_db.py 기존 패턴 복제)

CREATE TABLE IF NOT EXISTS weekly_insight_summary (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start         TEXT NOT NULL UNIQUE,
    week_end           TEXT NOT NULL,
    summary_text       TEXT NOT NULL,
    source_insight_ids TEXT,                         -- JSON array
    insight_count      INTEGER,
    top_tickers        TEXT,                         -- JSON array
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_wis_week ON weekly_insight_summary(week_start DESC);

CREATE TABLE IF NOT EXISTS insight_tool_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id INTEGER NOT NULL,
    tool_name  TEXT NOT NULL,
    call_count INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_itu_insight ON insight_tool_usage(insight_id);

CREATE TABLE IF NOT EXISTS user_insight_quota (
    user_id INTEGER NOT NULL,
    date    TEXT NOT NULL,                           -- YYYY-MM-DD KST
    count   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS insight_cost_daily (
    date             TEXT PRIMARY KEY,               -- YYYY-MM-DD KST
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    embedding_tokens INTEGER DEFAULT 0,
    perplexity_calls INTEGER DEFAULT 0,
    firecrawl_calls  INTEGER DEFAULT 0
);
```

### 4.1 조회용 View

```sql
CREATE VIEW IF NOT EXISTS insight_metrics_daily AS
SELECT
  DATE(created_at, 'localtime') AS d,
  COUNT(*) AS queries,
  COUNT(DISTINCT user_id) AS unique_users,
  AVG(LENGTH(answer)) AS avg_answer_len
FROM persistent_insights
GROUP BY DATE(created_at, 'localtime');
```

### 4.2 정합성 관리

FK 없이 다음 애플리케이션 레이어 원칙 유지:

- `persistent_insights.previous_insight_id` → 저장 전 상위 insight 존재 확인
- `persistent_insights.superseded_by` → 주간 요약 잡만 갱신
- 월 1회 `cleanup_orphans.py` (월간 cron) — 유효하지 않은 참조 null 처리

---

## 5. 데이터 플로우

### 5.1 `/insight` 쿼리 처리

```
[1] /insight 명령어 수신 (ConversationHandler)
[2] "질문을 입력해주세요" 안내
[3] 사용자 질문 입력 (INSIGHT_ENTERING_QUERY 상태)
[4] 질문 길이 검사 (2000자 초과 거부)
[5] 일일 쿼터 체크 (user_insight_quota) — 초과 시 거부
[6] (배경) 질문 임베딩 생성
[7] Retrieval 3-tier 병렬:
     (a) persistent_insights FTS top-50 → 임베딩 재랭킹 top-5
         (superseded 제외)
     (b) weekly_insight_summary 최근 4주
     (c) report_archive FTS top-5 (종목 언급 시)
[8] InsightAgent (mcp-agent) 실행
     - 시스템 프롬프트: 가드레일 + retrieval 결과
     - 함수 호출로 외부 도구 선택
     - structured output (Pydantic)
[9] key_takeaways 임베딩 생성
[10] persistent_insights INSERT (fire-and-forget)
[11] Telegram 답변 전송 (message_id → insight_contexts 매핑)
```

### 5.2 Reply 기반 멀티턴

```
사용자 → /insight → 봇 "질문 입력"
사용자 → 질문
  봇 → 답변 (msg_id=X) [insight_contexts[X] 저장, TTL 30분]
사용자 → (X에 Reply) 후속 질문
  봇 → _handle_insight_reply → 컨텍스트 복원 + 재실행 + 새 answer 저장
       (previous_insight_id=이전_id로 체인 기록)
```

### 5.3 주간 요약 (`auto_insight --mode weekly` 확장)

```
[1] 지난 주(월~일) persistent_insights 조회 (superseded IS NULL)
[2] 건수 분기:
     - 0건: skip
     - 1~5건: 생략
     - 6+ 건: 생성
[3] LLM에 key_takeaways + ticker + 시각 전달 → 5~10 bullet 압축
[4] weekly_insight_summary INSERT
[5] 해당 기간 persistent_insights.superseded_by 일괄 업데이트
```

비용: 주 1회 × ~1500 input tokens × gpt-5.4-mini ≈ $0.0005/주.

---

## 6. 컴포넌트 상세

### 6.1 신규 파일

| 파일 | 책임 |
|---|---|
| `cores/archive/persistent_insights.py` | 영구 insight CRUD, FTS 동기화, 임베딩 재랭킹, 쿼터 관리 |
| `cores/archive/insight_agent.py` | mcp-agent 래퍼, 시스템 프롬프트 구성, structured output 파싱 |
| `cores/archive/embedding.py` | OpenAI 임베딩 호출 + BLOB 직렬화 |
| `cores/archive/insight_prompts.py` | 시스템 프롬프트 상수, 가드레일 문자열 |

### 6.2 변경 파일

| 파일 | 변경 범위 |
|---|---|
| `cores/archive/archive_db.py` | `init_db()`에 신규 테이블 + 트리거 추가 |
| `cores/archive/auto_insight.py` | `weekly_summary()` 확장 (persistent_insights 분기) |
| `archive_api.py` | `POST /insight_agent` 엔드포인트 추가 |
| `telegram_ai_bot.py` | `/insight` 핸들러 교체 + `InsightConversationContext` + Reply 핸들러 + `set_my_commands` |
| `archive_query.py` | `--insight-stats` CLI 옵션 |
| `docs/archive/ARCHIVE_DEPLOY_GUIDE.md` | 신규 테이블 마이그레이션 + env 추가 |
| `docs/archive/ARCHIVE_API_SETUP.md` | `/insight_agent` 엔드포인트 문서화 |

### 6.3 주요 인터페이스

```python
# embedding.py
async def embed_text(text: str) -> bytes
def decode_embedding(blob: bytes) -> np.ndarray
def cosine(a: bytes, b: bytes) -> float

# persistent_insights.py
async def save_insight(*, user_id, chat_id, question, answer,
                       key_takeaways, tools_used, tickers_mentioned,
                       evidence_report_ids, model_used,
                       previous_insight_id=None,
                       db_path=None) -> int

async def search_insights(query: str, limit: int = 5,
                          exclude_superseded: bool = True,
                          db_path=None) -> list[InsightRow]

async def recent_weekly_summaries(weeks: int = 4,
                                  db_path=None) -> list[dict]

async def check_and_increment_quota(user_id: int, limit: int,
                                    db_path=None) -> tuple[bool, int]

# insight_agent.py
class InsightAgent:
    def __init__(self, model: str = "gpt-5.4-mini"): ...

    async def run(self, question: str, user_id: int, chat_id: int,
                  previous_insight_id: int | None = None) -> InsightResult
```

### 6.4 시스템 프롬프트 가드레일 (요약)

```
# Role: PRISM 장기투자 인사이트 엔진
# Mission: 아카이브 지식 + (필요 시) 외부 도구로 사용자 질문에 답변하고,
# 답변에서 재사용 가능한 핵심 패턴을 key_takeaways로 추출한다.

# 외부 도구 사용 원칙
- 먼저 archive_search_insights + report_retrieve로 답변 시도
- 외부 도구는 최후 수단, 각 도구 1회 이하 권장

## firecrawl
- URL이 명확할 때만 firecrawl_scrape 사용
  - 필수 파라미터: formats=["markdown"], onlyMainContent=true
- firecrawl_search는 정말 필요한 경우만

## perplexity
- 최신 뉴스/시장 이벤트 맥락 필수일 때 1회만

## 무료 도구 (비용 제약 없음)
- yahoo_finance, kospi_kosdaq

# 답변 형식 (structured output)
- answer: 한국어, 400~1200자
- key_takeaways: 1~3개 문장 (재사용 가능한 핵심 패턴)
- tickers_mentioned: JSON array
- tools_used: JSON array
- evidence_report_ids: archive 리포트 id JSON array
```

### 6.5 BotFather 명령어 메뉴 동기화

봇 기동 `post_init` 훅에서 `Application.bot.set_my_commands()` 1회 호출. 기존 명령어 + `insight` 신규 1개.

---

## 7. 에러 처리 & 옵저빌리티

### 7.1 실패 모드와 대응

| # | 실패 지점 | 대응 |
|---|---|---|
| 1 | OpenAI synthesize 실패 | Retrieval 결과 원문 fallback |
| 2 | 임베딩 생성 실패 | `embedding=NULL`로 저장, 검색은 FTS only |
| 3 | MCP 도구 오류 | mcp-agent 자동 핸들링, LLM이 다른 도구 시도 |
| 4 | MCP 도구 타임아웃 (30s) | 해당 도구 스킵, 답변 계속 |
| 5 | persistent_insights INSERT 실패 | Error 로그만, 사용자 영향 없음 |
| 6 | HTTP API 호출 실패 (two-server) | 5s/30s 타임아웃 → 명확한 재시도 메시지 |
| 7 | 쿼터 초과 | KST 리셋 시간 안내 |
| 8 | Reply TTL 만료 | "새로 /insight로 시작해주세요" |
| 9 | 2000자 초과 입력 | 조기 거부 |
| 10 | key_takeaways JSON 파싱 실패 | 답변 일부를 takeaway로 저장, warning 로그 |

### 7.2 로깅 포맷

`logs/insight_agent.jsonl` (JSONLines):

```json
{
  "ts": "2026-04-21T22:41:03+09:00",
  "event": "insight_query",
  "user_id": 12345,
  "chat_id": -100200300,
  "question_hash": "a5f4…",
  "retrieval": {"fts_hits": 23, "reranked": 5, "weekly": 2, "reports": 3},
  "tools_used": ["archive_search", "yahoo_finance"],
  "tool_errors": [],
  "tokens": {"input": 2340, "output": 612},
  "latency_ms": 3421,
  "result": "success",
  "insight_id": 1289,
  "model": "gpt-5.4-mini"
}
```

### 7.3 운영 점검

- `archive_query.py --insight-stats` — 일간/주간 호출량, 상위 사용자, 도구 사용률
- Alert 제안:
  - 일일 perplexity+firecrawl 합계 > 100 → 경고
  - 저장 실패 rate > 5% → 조사
  - 임베딩 NULL rate > 10% → OpenAI API 이슈 의심

### 7.4 비용 추적

`insight_cost_daily` 테이블에 매 쿼리 UPSERT — 월말 청구서 교차검증.

---

## 8. 테스트 전략 (안정성 초점, 성능 시험은 제외)

### 8.1 단위 테스트 (pytest)

- `tests/test_persistent_insights.py` — CRUD, FTS 트리거 동기화, 쿼터 로직, superseded 업데이트
- `tests/test_embedding.py` — BLOB round-trip, cosine 계산 정확성
- `tests/test_insight_agent.py` — retrieval 순서, structured output 파싱, fallback
- `tests/test_weekly_summary.py` — 건수 분기 (0/1~5/6+), superseded 일괄 update

### 8.2 통합 테스트

`tests/test_insight_e2e.py`:

- 임시 archive.db 시드 → `/insight_agent` 호출 → DB 검증
- OpenAI 호출 mock
- 검증 항목:
  - [x] FTS 후보가 임베딩 재랭킹되어 반환
  - [x] 답변 저장 후 persistent_insights + FTS + 쿼터 일관
  - [x] Reply 멀티턴 → previous_insight_id 체인 정상
  - [x] 쿼터 초과 거부
  - [x] 주간 요약 후 superseded_by 일괄 갱신

### 8.3 수동 QA 체크리스트 (배포 직전)

```
□ archive.db 신규 테이블 5종 + FTS + 트리거 생성 확인
□ `python -m cores.archive.persistent_insights --self-check` 스모크 테스트
□ /insight → 질문 → 답변 동작
□ 답변 Reply → 후속 답변 (멀티턴)
□ 30분 초과 Reply → 만료 메시지
□ 일일 쿼터 초과 거부 + 리셋 시간 표시
□ archive_query.py --insight-stats 집계 정상
□ BotFather 메뉴에 /insight 노출
□ insight_tool_usage 로그 기록
□ 주간 요약 drill (수동 실행) 성공
```

### 8.4 마이그레이션 안전성

- 모든 신규 테이블 `CREATE IF NOT EXISTS`
- 기존 `insights` (24h 캐시) 테이블 유지
- 프로덕션 배포 전 DB 사본으로 마이그레이션 드라이런
- 롤백 스크립트 제공: `DROP TABLE persistent_insights; …`

---

## 9. 확정 요약 (Go / No-Go)

| 구분 | 결정 |
|---|---|
| 저장 위치 | `archive.db` + 신규 5 테이블 |
| 저장 대상 | Q&A 원문 + key_takeaways + 임베딩 BLOB |
| 저장 트리거 | 모든 `/insight` 자동 (fire-and-forget) |
| 검색 전략 | FTS5 top-50 → 임베딩 재랭킹 top-5 + 주간 요약 티어 |
| UX | 단일 `/insight` + ConversationHandler + Reply 멀티턴 (30분 TTL) |
| LLM | `gpt-5.4-mini` via mcp-agent function calling |
| 외부 도구 | yahoo_finance / kospi_kosdaq (무료), perplexity / firecrawl (엄격 가이드) |
| 비용 관리 | 프롬프트 가드레일 + 일일 쿼터, 관찰 로그로 향후 조정 |
| 프라이버시 | 공용 풀 (user_id만 기록) |
| BotFather | 봇 기동 시 `set_my_commands` 호출로 `/insight` 메뉴 등록 |
| 테스트 포커스 | 안정성 (단위/통합/수동 QA). 성능 시험은 실시간 서비스 아니므로 제외 |

---

## 10. 다음 단계

1. 이 설계 문서 커밋 (별도 브랜치 or 현 feat/archive-insight-query-system)
2. `superpowers:writing-plans` 스킬로 실행 가능한 구현 계획 수립
3. 구현 → 테스트 → 배포

## 부록 A — 관련 문서 링크

- [ARCHIVE_DEPLOY_GUIDE.md](../../archive/ARCHIVE_DEPLOY_GUIDE.md) — PR #262 배포 가이드
- [ARCHIVE_API_SETUP.md](../../archive/ARCHIVE_API_SETUP.md) — 양 서버 API 구성
- [ARCHIVE_VALIDATION.md](../../archive/ARCHIVE_VALIDATION.md) — 기존 검증 시나리오
