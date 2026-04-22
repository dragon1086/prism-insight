# Usage

PRISM Archive를 사용하는 세 가지 인터페이스: **텔레그램 봇**, **HTTP API**, **CLI**.

---

## 1. 텔레그램 `/insight` 명령어

가장 자연스러운 인터페이스. 그룹 채팅이든 개인 채팅이든 동일하게 동작합니다.

### 기본 흐름

```
사용자: /insight
봇:    🗂 PRISM 아카이브 ... 질문을 입력해주세요:
       예: 하락장에서 분석된 반도체 종목 30일 수익률은?
       예: 손절 발동 후 회복한 종목 비율은?

사용자: 셀트리온 장기투자 적합한가?
봇:    🧭 PRISM 장기 인사이트
       
       셀트리온(068270)의 실제 수익률 데이터를 기준으로 ...
       ■ 30거래일 수익률: -0.97%
       ■ 90거래일 수익률: +0.74%
       ■ 365일 수익률: +34.4%
       ■ MDD: -18.7%
       ...
       
       [👍 도움됨]  [👎 부정확]
       
       📊 오늘 남은 /insight: 19회
       ⚠️ 본 내용은 투자 참고용입니다.
```

### Reply 멀티턴 (30분 TTL)

봇이 답한 메시지에 **Reply**(답장 인용)로 후속 질문을 던지면 컨텍스트가 유지됩니다.

```
사용자: (위 답변에 Reply) 그럼 공매도 리스크는?
봇:    🧭 (이전 컨텍스트 + 추가 질문 처리한 답변)
사용자: (또 Reply) AI 섹터 다른 대장주는?
봇:    🧭 (계속 멀티턴 유지)
```

- 30분간 활동 없으면 만료 → "이전 /insight 세션이 만료되었습니다" 안내
- 새로 시작하려면 `/insight` 다시

### 👍/👎 피드백

각 답변 아래 inline keyboard 버튼:

| 버튼 | 효과 |
|---|---|
| **👍 도움됨** | 해당 인사이트의 `confidence_score` +0.2 (clamped to +1.0) |
| **👎 부정확** | `confidence_score` -0.2 (clamped to -1.0). **다음 검색에서 deboost 또는 완전 제외** |

피드백은 **사용자 1인 1표** (재투표 시 갱신). 그룹 채팅이면 모두가 투표 가능 — 의견이 갈리면 +/- 합산.

### 일일 사용 한도

기본 20회/사용자/일 (KST 자정 리셋). `INSIGHT_DAILY_LIMIT` 환경변수로 조정.

```
사용자: /insight (한도 초과)
봇:    일일 /insight 호출 한도를 초과했습니다. 자정(KST) 이후 초기화됩니다.
```

### 좋은 질문 패턴 vs 나쁜 패턴

```
👍 좋은 질문:
  - "하락장에서 1년 후 50% 이상 오른 종목들의 공통점은?"
  - "MDD 10% 이하로 30% 이상 수익 낸 종목"
  - "외국인 순매수 지속된 반도체 섹터 종목"
  - "장기투자 적합한 가치주 특징"
  - "SK하이닉스 30일 수익률은?"

👎 답변하기 어려운 질문:
  - "삼성전자 내일 오를까?" (단기 예측, 시스템 영역 외)
  - "AAPL 좀 알려줘" (모호함 — 무엇을?)
  - "부자 되는 법" (도메인 외)
```

---

## 2. HTTP API (양 서버 또는 직접 호출)

archive_api.py가 노출하는 6개 엔드포인트.

### 인증

기본은 Bearer 토큰 (`ARCHIVE_API_KEY`). `/health`만 무인증.

```bash
KEY=$ARCHIVE_API_KEY    # .env에서 로드
```

### `GET /health`

서버 상태 확인. 인증 없음.

```bash
curl http://127.0.0.1:8765/health
# {"status":"ok","archive_db":true,"archive_db_size_mb":1018.53}
```

### `GET /stats`

리포트 통계.

```bash
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8765/stats
```

### `GET /search?keyword=&market=&limit=`

FTS5 키워드 검색 (LLM 합성 없음).

```bash
curl -H "Authorization: Bearer $KEY" \
  "http://127.0.0.1:8765/search?keyword=반도체&market=kr&limit=10"
```

### `POST /query` (단순 자연어 질문)

기존 단일 LLM 합성 경로. `/insight_agent`보다 가벼움.

```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  http://127.0.0.1:8765/query \
  -d '{"question":"하락장 반도체 30일 수익률은?","market":"kr"}'
```

### `POST /insight_agent` ⭐ (메인 엔드포인트)

5계층 retrieval + Claude function calling + 자동 저장.

```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  http://127.0.0.1:8765/insight_agent \
  -d '{
    "question": "셀트리온 장기투자 적합한가?",
    "user_id": 12345,
    "chat_id": -100200300,
    "daily_limit": 20,
    "previous_insight_id": null
  }'
```

응답:
```json
{
  "answer": "...",
  "key_takeaways": ["...", "..."],
  "tickers_mentioned": ["068270"],
  "tools_used": ["kospi_kosdaq_get_stock_ohlcv"],
  "evidence_count": 3,
  "insight_id": 7,
  "remaining_quota": 19,
  "model_used": "claude-sonnet-4-6"
}
```

**파라미터**:
- `daily_limit` (default 20): 사용자당 일일 한도. `0` = 무제한 (권장 X).
- `previous_insight_id`: 멀티턴 체인 추적용. 텔레그램 봇이 자동 사용.

### `POST /feedback` ⭐ (자가개선 신호)

사용자 👍/👎 신호 기록.

```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  http://127.0.0.1:8765/feedback \
  -d '{"insight_id":7, "user_id":12345, "score":-1, "reason":"수익률 잘못 인용"}'
```

응답:
```json
{"ok": true, "insight_id": 7, "new_confidence": -0.2}
```

`score`는 `+1` / `-1` / `0`(중립). 같은 (insight_id, user_id) 재투표 시 덮어씀.

---

## 3. CLI (`archive_query.py`)

DB가 있는 머신에서 직접 실행하는 도구.

### `--stats` — 아카이브 기본 통계

```bash
python archive_query.py --stats
```

```
=== PRISM 아카이브 통계 ===
  KR    801건  2025-09-29 ~ 2026-04-21
  US    167건  2025-09-29 ~ 2026-03-31
  enriched:          968건
  cached insights:     0건
```

### `--insight-stats` ⭐ — 누적 인사이트 + 비용

```bash
python archive_query.py --insight-stats
```

```
=== /insight 통계 ===
  총 인사이트:     7
  고유 사용자:     4
  주간 요약:       0

  도구 사용 분포:
    kospi_kosdaq_get_stock_ohlcv  5회
    kospi_kosdaq_get_stock_fundamental  3회
    archive_search_insights    1회

  최근 7일 비용:
    2026-04-22  in=0  out=0  emb=0  perp=0  fc=0
```

### `--search "키워드"` — FTS5 검색

```bash
python archive_query.py --search "반도체" --market kr --limit 10
```

### `--list` — 리포트 목록

```bash
python archive_query.py --list --ticker 005930 --date-from 2026-01-01
```

### 자연어 질문 (LLM 합성)

```bash
python archive_query.py "하락장에서 분석된 반도체 종목 30일 수익률은?" --market kr
```

`--skip-cache`로 캐시 무시, `--model gpt-5.4-mini` 로 모델 지정 가능.

### `--json` — 모든 출력 JSON 형식

```bash
python archive_query.py --insight-stats --json
```

---

## 4. 백그라운드 잡

`auto_insight.py`의 cron 옵션:

```bash
# 일일 인사이트 (매일 새벽 2시 권장)
python -m cores.archive.auto_insight --type daily --market both

# 주간 압축 + 시맨틱 fact 증류 (매주 월 새벽 3시 권장)
python -m cores.archive.auto_insight --type all --narrative

# 수동: 특정 주 압축
python -m cores.archive.auto_insight --type compress --week-start 2026-04-14 --week-end 2026-04-20

# 수동: 30일치 인사이트로부터 fact 증류
python -m cores.archive.auto_insight --type distill --distill-window 30
```

`--type all`은 daily + leaderboard + stoploss + phase + weekly + 압축 + fact 증류를 모두 실행. 주간 cron 1번이면 자가개선 루프가 자동으로 돌아갑니다.

### 신규 리포트 인제스트

```bash
# dry-run으로 파싱 검증
python -m cores.archive.ingest --dir reports/ --market kr --dry-run

# 실제 인제스트 (SEASON2_START=2025-09-29 이후만 통과)
python -m cores.archive.ingest --dir reports/ --market kr
python -m cores.archive.ingest --dir prism-us/reports/ --market us
```

매일 분석 파이프라인이 자동 호출하므로 일반적으론 수동 실행 불필요.

### 장기 가격 백필

```bash
# return_30d / return_90d / return_365d / MDD 등 enrichment 채우기
python update_current_prices.py --concurrency 2
```

새벽(01~05시) 권장 — 분석 파이프라인과 시간대 분리.

---

## 5. 자주 쓰는 패턴

### 회고 분석 (백테스트 풍)

```
/insight 손절 발동 후 회복한 종목 비율은?
/insight 하락장에서 +50% 오른 종목 공통점
/insight MDD 10% 이내 + 30% 수익 종목 특징
```

### 종목 의사결정 보조

```
/insight 삼성전자 장기투자 적합한가?
/insight SK하이닉스 vs 마이크론, 어느 쪽 장기 베타가 낮나?
```

### 메타 분석

```
/insight 외국인 순매수 지속 시 1년 수익률 상관관계
/insight 시장 국면별 가장 안정적인 섹터는?
```

### 후속 질문 (Reply)

```
사용자: /insight + "반도체 winner 패턴은?"
봇:    [답변]
사용자: (Reply) 그 중에서 PER 15 이하인 종목만 추려줘
봇:    [필터링된 답변]
사용자: (Reply) 외국인 순매수 종목으로 좁혀줘
봇:    [더 좁혀진 답변]
```

---

## 6. 한계 — 이 시스템이 잘 못 하는 것

- ❌ **단기 예측** ("내일 오를까?") — 시스템 설계 영역 외
- ❌ **실시간 호가** — yahoo_finance/kospi_kosdaq MCP는 종가/일봉 기준
- ❌ **개인화 메모리** — 모든 사용자가 공용 풀 공유 (의도된 설계, 추후 user_id 격리 옵션 검토)
- ❌ **가짜 답변 무조건 차단** — Claude 환각 가능성 존재. **👎 피드백으로 점진적 학습** 필요
- ❌ **outcome 검증 자동화 없음** — 인사이트의 사후 정확도는 사람이 평가해야 (`/insight 6개월 전 인사이트가 맞았는지` 같은 메타 질문은 가능)

---

## 7. 다음 단계

- 운영자: [DEPLOYMENT.md](DEPLOYMENT.md)
- 내부 동작 이해: [ARCHITECTURE.md](ARCHITECTURE.md)
