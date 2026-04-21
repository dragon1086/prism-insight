# PRISM Archive API — 셋업 가이드

> **파일**: `archive_api.py` (파이프라인 서버), `/insight` 명령 (`telegram_ai_bot.py`)
> **Updated**: 2026-04-15

---

## 아키텍처

```
[사용자]
  → Telegram /insight "하락장 반도체 종목 수익률은?"
    → [봇/대시보드 서버] telegram_ai_bot.py
        → HTTP POST /query   (ARCHIVE_API_URL 설정 시)
          → [파이프라인 서버] archive_api.py
              → archive.db (FTS5 + LLM 합성)
              → 응답 반환
        ← answer text
    ← Telegram 메시지
```

**단일 서버 모드** (`ARCHIVE_API_URL` 미설정):
- `telegram_ai_bot.py`가 `cores.archive.query_engine`을 직접 임포트
- 별도 API 서버 불필요

---

## 파이프라인 서버 설정

### 1. 의존성 설치

```bash
pip install fastapi uvicorn aiosqlite
```

### 2. `.env` 설정

```bash
# .env (파이프라인 서버)
ARCHIVE_API_KEY=랜덤_시크릿_32자_이상   # openssl rand -hex 32
ARCHIVE_API_HOST=0.0.0.0               # 또는 127.0.0.1 (SSH 터널 시)
ARCHIVE_API_PORT=8765
```

> **API 키 생성**: `openssl rand -hex 32`

### 3. 서버 실행

```bash
# 직접 실행
python archive_api.py

# 또는 uvicorn으로 실행
uvicorn archive_api:app --host 0.0.0.0 --port 8765

# 백그라운드 실행 (프로덕션)
nohup python archive_api.py >> logs/archive_api.log 2>&1 &
```

### 4. 방화벽 설정 (봇 서버 IP만 허용)

```bash
# 봇 서버 IP만 8765 포트 허용
ufw allow from <봇서버_IP> to any port 8765
ufw deny 8765
```

### 5. systemd 서비스 등록 (선택)

```ini
# /etc/systemd/system/prism-archive-api.service
[Unit]
Description=PRISM Archive API
After=network.target

[Service]
User=root
WorkingDirectory=/root/prism-insight
EnvironmentFile=/root/prism-insight/.env
ExecStart=/root/.pyenv/shims/python archive_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable prism-archive-api
systemctl start prism-archive-api
```

---

## 봇 서버 설정

### `.env` 설정

```bash
# .env (봇/대시보드 서버)
ARCHIVE_API_URL=http://<파이프라인서버_IP>:8765
ARCHIVE_API_KEY=랜덤_시크릿_32자_이상   # 파이프라인 서버와 동일 값
```

> `ARCHIVE_API_URL` 미설정 시 **단일 서버 모드** (직접 query_engine 호출)

---

## 엔드포인트

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| `GET` | `/health` | 서버 상태 + DB 존재 여부 | 없음 |
| `GET` | `/stats` | 리포트 수, 날짜 범위, 캐시 수 | Bearer |
| `GET` | `/search?keyword=반도체&market=kr&limit=10` | FTS5 키워드 검색 | Bearer |
| `POST` | `/query` | 자연어 질문 + LLM 합성 응답 (단순) | Bearer |
| `POST` | `/insight_agent` | 누적 인사이트 + 외부 도구 조합 장기 인사이트 엔진 (신규) | Bearer |

### POST /query 예시

```bash
curl -X POST http://localhost:8765/query \
  -H "Authorization: Bearer $ARCHIVE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "bear 시장 반도체 종목 30일 수익률은?", "market": "kr"}'
```

```json
{
  "answer": "...",
  "evidence_count": 5,
  "cached": false,
  "model_used": "gpt-5.4-mini"
}
```

### POST /insight_agent 예시 (신규, 누적 인사이트 + 외부 도구 조합)

```bash
curl -X POST http://localhost:8765/insight_agent \
  -H "Authorization: Bearer $ARCHIVE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "question": "삼성전자 장기투자 적합한가?",
        "user_id": 12345,
        "chat_id": -100200300,
        "daily_limit": 20
      }'
```

```json
{
  "answer": "...",
  "key_takeaways": ["반도체 사이클 회복 구간", "PER 저평가 지속"],
  "tickers_mentioned": ["005930"],
  "tools_used": ["archive_search_insights", "yahoo_finance"],
  "evidence_count": 3,
  "insight_id": 127,
  "remaining_quota": 19,
  "model_used": "gpt-5.4-mini"
}
```

- `daily_limit` (기본 20): 사용자당 일일 호출 한도. `0` = 무제한 (권장 X).
- `previous_insight_id`: Telegram Reply 멀티턴에서만 사용 (이전 턴 `insight_id`).
- `tools_used`: LLM이 실제로 호출한 MCP 도구 목록. 관측용.

---

## Telegram `/insight` 사용법

```
/insight
→ 봇: "질문을 입력해주세요"
→ 사용자: "하락장에서 분석된 반도체 종목들의 30일 수익률은?"
→ 봇: [PRISM 아카이브 인사이트 응답]
```

---

## SSH 터널 방식 (보안 강화)

파이프라인 서버를 `127.0.0.1`에만 바인딩하고, 봇 서버에서 SSH 터널로 접속:

```bash
# 봇 서버에서 SSH 터널 유지 (autossh 권장)
autossh -M 0 -N -L 8765:localhost:8765 user@pipeline-server &

# .env (봇 서버)
ARCHIVE_API_URL=http://127.0.0.1:8765
```

```bash
# 파이프라인 서버 .env
ARCHIVE_API_HOST=127.0.0.1   # 외부 포트 열지 않음
```

---

## 헬스체크

```bash
# 파이프라인 서버에서
curl http://localhost:8765/health

# 예상 응답
{"status":"ok","archive_db":true,"archive_db_size_mb":52.3}
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `401 Unauthorized` | API 키 불일치 | 양쪽 `.env`의 `ARCHIVE_API_KEY` 동일한지 확인 |
| `Connection refused` | 서버 미실행 또는 방화벽 | `ps aux | grep archive_api` 확인, UFW 규칙 확인 |
| `archive.db not found` | 인제스트 미실행 | `python -m cores.archive.ingest --dir reports/ --market kr` |
| `/insight`가 단일서버 모드 | `ARCHIVE_API_URL` 미설정 | 봇 서버 `.env` 확인 |
