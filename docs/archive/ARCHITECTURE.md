# Architecture

PRISM Archive는 단순한 RAG가 아닙니다. 다섯 계층의 지식 메모리, 자가개선 루프, 객관적 outcome 그라운딩이 결합된 **메모리-증강 에이전트 아키텍처**입니다.

이 문서는 시스템이 어떻게 동작하는지 단계별로 설명합니다.

---

## 1. 개요 — 양서버 / 단일서버 모드

```
┌──────── 텔레그램 사용자 ────────┐
│  /insight 명령어 입력            │
└─────────────┬───────────────────┘
              ▼
┌── app-server (선택, prism 유저) ──┐
│ telegram_ai_bot.py                │
│   - ConversationHandler           │
│   - Reply 멀티턴 (30분 TTL)       │
│   - 👍/👎 InlineKeyboard          │
└─────────────┬─────────────────────┘
              ▼   (HTTPS or SSH 터널)
┌── db-server (또는 동일 서버) ─────────────────────────┐
│ archive_api.py (FastAPI :8765)                       │
│   - /health  /stats  /search  /query                 │
│   - /insight_agent  /feedback                        │
│         │                                            │
│         ▼                                            │
│ cores.archive.insight_agent.InsightAgent             │
│   ┌─ 5-tier retrieval                            ┐  │
│   │  ① ticker_semantic_facts (Mem0)              │  │
│   │  ② report_enrichment outcomes                │  │
│   │  ③ weekly_insight_summary                    │  │
│   │  ④ persistent_insights (FTS+임베딩 재랭킹)   │  │
│   │  ⑤ report_archive 발췌                       │  │
│   └──────────────────────────────────────────────┘  │
│         │                                            │
│         ▼                                            │
│ Claude (mcp-agent + AnthropicAugmentedLLM)           │
│   function calling: yahoo_finance / kospi_kosdaq /   │
│                     perplexity / firecrawl           │
│         │                                            │
│         ▼                                            │
│ JSON {answer, key_takeaways, tickers, tools}         │
│         │                                            │
│         ▼                                            │
│ persistent_insights INSERT (자동 저장)                │
└──────────────────────────────────────────────────────┘
```

- **단일 서버 모드**: `ARCHIVE_API_URL`을 비워두면 텔레그램 봇이 InsightAgent를 직접 import 호출.
- **양 서버 모드**: SSH 터널을 통해 봇이 archive_api에 HTTP POST.

---

## 2. 데이터 모델 (SQLite, archive.db)

### 핵심 테이블

| 테이블 | 역할 | 주요 컬럼 |
|---|---|---|
| `report_archive` | MD 리포트 본문 | ticker, content, report_date, market, file_hash |
| `report_archive_fts` | FTS5 인덱스 (ticker/company/content) | unicode61 토크나이저 |
| `report_enrichment` | 분석 시점 + 사후 outcome | return_30d/90d/180d/365d, max_drawdown, market_phase |
| `ticker_price_history` | 장기 가격 백필 | report_id, price_date, close, return_pct |

### 누적 인사이트 레이어 (Phase 2)

| 테이블 | 역할 |
|---|---|
| `persistent_insights` | 모든 `/insight` Q&A를 저장. embedding(BLOB), tickers_mentioned(JSON), confidence_score 포함 |
| `persistent_insights_fts` | FTS5 인덱스 (question + key_takeaways) — 트리거로 자동 동기화 |
| `weekly_insight_summary` | 주간 LLM 압축본 — superseded raw insights 그룹 |
| `insight_tool_usage` | MCP 도구 호출 빈도 (관측용) |
| `user_insight_quota` | 사용자/일자별 호출 카운트 |
| `insight_cost_daily` | 토큰·외부 API 호출 누적 (월말 청구서 교차검증용) |

### 자가개선 레이어 (Phase B, 2026-04)

| 테이블 | 역할 |
|---|---|
| `insight_feedback` | 👍/👎 사용자 신호 (DPO-lite) — `(insight_id, user_id) UNIQUE` |
| `ticker_semantic_facts` | LLM이 증류한 ticker별 원자적 사실 (Mem0 패턴). category(fundamental/momentum/risk/sentiment/thesis), confidence(0~1), supporting_insight_ids |

`persistent_insights.confidence_score` 컬럼이 추가되어 피드백 합산값이 normalize되어 retrieval 가중치로 작동합니다.

---

## 3. 5계층 검색 컨텍스트

`/insight` 호출 한 번에 LLM 컨텍스트는 다음 순서로 조립됩니다 — **위로 올라갈수록 정련도·신뢰도 ↑**:

| Tier | 출처 | 예시 |
|---|---|---|
| **① 종목별 시맨틱 사실** | `ticker_semantic_facts` | `005930 [fundamental|conf=0.85] HBM 점유율 회복으로 2026 1Q 매출 +8% 가이던스 상향` |
| **② 객관 outcome** | `report_enrichment` JOIN | `005930: 30d=+15.8% \| 90d=+12.1% \| 365d=+34.4% \| MDD=-18.7% \| 국면=bull \| 분석일=2026-03-10` |
| **③ 주간 요약** | `weekly_insight_summary` | "지난주 16건 인사이트 압축 — 반도체 섹터 모멘텀 우세" |
| **④ 누적 인사이트** | `persistent_insights` (top-5) | 과거 Q&A의 key_takeaways, **confidence_score 보정 후** |
| **⑤ 원본 리포트** | `report_archive` (top-6) | 발췌 ~400자 |

### 검색 가중치 공식 (Tier ④)

```python
final_score = cosine_similarity(question_embedding, insight_embedding)
            + 0.15 * confidence_score          # +0.15 / -0.15 boost
# confidence_score < -0.6 → 완전 제외 (heavily downvoted insight 차단)
```

이게 RLHF의 핵심 — 사용자가 한 번 "이 답변 부정확"이라고 표시하면 다음 retrieval에서 deboost되고, 강하게 거부되면 아예 제외됩니다.

---

## 4. 자가개선 루프

```
       ┌────────────────────────────────┐
       │  사용자 /insight + 답변 + 👍/👎 │
       └────────────┬───────────────────┘
                    │
                    ▼
       ┌────────────────────────────────┐
       │  persistent_insights INSERT    │
       │  insight_feedback INSERT       │
       │  confidence_score 재계산       │
       └────────────┬───────────────────┘
                    │
                    ▼
                  (시간)
                    │
        매주 월 03시 cron (auto_insight)
                    │
   ┌────────────────┼─────────────────────┐
   ▼                ▼                     ▼
weekly_summary  distill_semantic_facts  market_phase
(원본 압축)    (Mem0/Auto Dream)       리포트
   │                │
   │ ┌──────────────┘
   ▼ ▼
ticker_semantic_facts UPSERT
   │
   ▼
다음 /insight 호출 시
Tier ① 컨텍스트로 주입
```

### 주간 압축 — 2단계

1. **`compress_weekly_insights`** — 지난 주 ≥6건의 `persistent_insights`를 1건의 `weekly_insight_summary`로 압축, 원본은 `superseded_by` 표시 (검색 시 기본 제외, 단 `evidence` 추적 가능)
2. **`distill_semantic_facts`** — 최근 30일 인사이트를 ticker별로 그룹 (≥2 mentions). LLM이 outcome 데이터 + 기존 사실 + 새 인사이트를 종합해 **재사용 가능한 atomic facts**를 ticker_semantic_facts에 UPSERT.

각 fact는 `(category, confidence, supporting_insight_ids)`를 보유. 충돌 발생 시 `superseded_by`로 신구 정합화.

---

## 5. LLM 역할 분리

| 역할 | 모델 | 이유 |
|---|---|---|
| InsightAgent function calling | **claude-sonnet-4-6** (AnthropicAugmentedLLM) | OpenAI gpt-5.x reasoning 모델은 chat-completions API에서 function tools와 reasoning_effort를 동시 지원 안 함 (400 invalid_request_error). Claude는 안정적 |
| 임베딩 | **OpenAI text-embedding-3-small** (1536-dim) | 가성비 + Korean 성능 우수 |
| 주간 압축 / fact 증류 | **gpt-5.4-mini** (reasoning_effort=none, max_completion_tokens) | function calling 없으니 OpenAI 사용 가능, 비용 저렴 |
| 단순 /query 합성 | **gpt-5.4-mini** | 기존 query_engine 경로 |

### MCP 도구 (mcp-agent)

| 도구 | 비용 | 용도 |
|---|---|---|
| `kospi_kosdaq` | 무료 | 한국 주가/거래량 — Claude가 시스템 프롬프트 가이드에 따라 우선 호출 |
| `yahoo_finance` | 무료 | 미국 주가/재무/뉴스 |
| `perplexity` | $0.005/req | 최신 뉴스 맥락 (가드레일: 1회 이하 권장) |
| `firecrawl` | $0.01/scrape | 특정 URL 본문 (가드레일: scrape only, `onlyMainContent=true`) |

시스템 프롬프트가 LLM에게 "컨텍스트 충분하면 외부 도구 호출 금지"를 지시. 실제 운영에서 평균 0회 또는 무료 도구 1~2회로 수렴.

---

## 6. UX — Reply 멀티턴 + 피드백

```
User → /insight                          (ConversationHandler 진입)
Bot  → "질문을 입력해주세요"

User → "셀트리온 장기투자 적합한가?"
Bot  → [답변 본문 + 👍/👎 inline keyboard] (msg_id=42 → insight_contexts 캐시)

User → (Reply to 42) "수익률이 적은데?"     (replied_to_msg_id 확인)
Bot  → [컨텍스트 복원 + 재답변]

User → 👎 클릭                             (CallbackQueryHandler)
Bot  → "👎 부정확 기록됨 (신뢰도 -0.20)"   (keyboard 갱신)
       → DB의 confidence_score 업데이트
       → 다음 retrieval에서 deboost
```

`InsightConversationContext`는 30분 TTL. `_handle_insight_reply`는 직전 `previous_insight_id`를 체인 연결해 다턴 추적 가능.

---

## 7. 비용 / 보안 모델

### 비용 (예상 월 운영)

| 항목 | 예상량 | 월 비용 |
|---|---|---|
| 사용자 질문 (인스턴스당 200/월) | Claude in≈3k+out≈1k | ~$1 |
| 임베딩 | 200개 질문 + 200개 takeaway | <$0.01 |
| 주간 압축 + distill | 4회/월 | <$0.05 |
| 도구 외부 호출 | perplexity 0~5회 + firecrawl 0회 | <$0.05 |
| **합계** | | **~$1/월** |

`insight_cost_daily` 테이블이 위 모든 항목을 자동 누적, `archive_query.py --insight-stats`로 점검.

### 보안

- archive_api는 `127.0.0.1`에 바인딩 → 공용 인터넷 노출 0
- app-server → db-server: SSH 터널만 사용 (`-L 8765:127.0.0.1:8765`), `command="echo tunnel-only", permitopen="127.0.0.1:8765"` 권한 제한
- Bearer 토큰 (`ARCHIVE_API_KEY`) 추가 인증
- Telegram Reply TTL 30분, 일일 쿼터로 폭주 방지

---

## 8. 디자인 결정 기록

설계 과정에서 한 6가지 핵심 결정:

| 결정 | 선택 | 근거 |
|---|---|---|
| 저장 위치 | `archive.db` (단일 SQLite) | 리포트와 한 DB 안에서 JOIN, FTS5 인프라 재활용 |
| 저장 단위 | Q&A 원문 + key_takeaways (hybrid) | 원본 보존 + 검색 효율 둘 다 |
| 검색 전략 | FTS5 → embedding rerank + 주간 요약 티어 | 정확 매칭(종목명) + 의미 유사도 + 컨텍스트 압축 |
| UX | 단일 `/insight` + function calling | 사용자 편의성, 질문 유형 경계 없음 |
| 비용 가드레일 | 프롬프트 + 일일 쿼터 (관찰 우선) | 초기 단계엔 rate-limit 오버엔지니어링 |
| 프라이버시 | 공용 풀, user_id만 기록 | 텔레그램 그룹 컨텍스트와 자연스럽게 일치 |

상세는 `docs/superpowers/specs/2026-04-21-persistent-insight-agent-design.md` 참고.

---

## 9. 한계와 향후 작업

- **임베딩 검색은 브루트포스 cosine** — 10k insights 이하에서 충분 (~5ms). 50k+ 초과 시 sqlite-vec 도입 권장
- **단일 인스턴스 SQLite** — 동시 쓰기 부하 낮음 (지금 워크로드엔 충분)
- **Knowledge graph 미도입** — Neo4j 같은 구조화 그래프는 복잡도 대비 ROI 낮다고 판단. ticker_semantic_facts로 light KG 흉내
- **outcome 라벨링 자동화 없음** — "이 인사이트가 N개월 후 정확했는가?"를 사람이 평가하는 외부 프로세스 필요
- **개인화 미지원** — 모든 사용자가 공용 풀 공유. user_id별 개인 메모리는 향후 작업

---

## 10. 추가 자료

- 운영자: [DEPLOYMENT.md](DEPLOYMENT.md)
- 사용자: [USAGE.md](USAGE.md)
- 설계 의사결정 원본: `docs/superpowers/specs/2026-04-21-persistent-insight-agent-design.md`
- 구현 계획: `docs/superpowers/plans/2026-04-21-persistent-insight-agent.md`
