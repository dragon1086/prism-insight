# PRISM Archive — 운영 서버 배포 가이드

> **Updated**: 2026-04-16
> PR #262 머지 후 이 가이드대로 순서대로 실행하세요.

---

## 체크리스트

- [ ] Step 1: PR #262 머지
- [ ] Step 2: 코드 pull + 의존성 설치
- [ ] Step 2.5: persistent insight 테이블 마이그레이션 (자동)
- [ ] Step 3: KR 리포트 인제스트
- [ ] Step 4: US 리포트 인제스트 (해당 시)
- [ ] Step 5: DB 확인
- [ ] Step 6: 장기 가격 히스토리 백필
- [ ] Step 7: crontab 등록
- [ ] Step 8: logs 디렉토리 확인
- [ ] Step 9: archive_api 서버 실행 (양서버 모드 시)
- [ ] Step 10: 텔레그램 봇 재시작 (BotFather 메뉴 자동 동기화)
- [ ] Step 11: /insight 동작 확인 (멀티턴 + 쿼터)

---

## Step 1. PR #262 머지

GitHub에서 PR #262 → **"Merge pull request"** 클릭

---

## Step 2. 코드 pull + 의존성 설치

```bash
cd /root/prism-insight
git pull origin main

source venv/bin/activate
pip install -r requirements.txt
```

> `fastapi`, `uvicorn`, `aiosqlite`, `yfinance` 모두 requirements.txt에 포함됨

---

## Step 2.5. persistent insight 테이블 마이그레이션 (자동)

신규 5 테이블 (+ FTS + 트리거 + View) 생성. `init_db()`가 idempotent 이므로 1회 실행:

```bash
python -c "import asyncio; from cores.archive.archive_db import init_db; asyncio.run(init_db())"
```

확인:
```bash
sqlite3 archive.db ".tables" | tr ' ' '\n' | grep -iE 'insight|weekly|quota|cost'
```
Expected 최소 목록:
```
insight_cost_daily
insight_tool_usage
persistent_insights
persistent_insights_fts
user_insight_quota
weekly_insight_summary
```

---

## Step 3. KR 리포트 인제스트

```bash
# dry-run 먼저 — 파싱 오류 확인
python -m cores.archive.ingest --dir reports/ --market kr --dry-run

# 이상 없으면 실제 인제스트
python -m cores.archive.ingest --dir reports/ --market kr
```

> KIS API 없으면 enrichment(수익률 계산)는 skip되고 raw MD만 저장됨 — 정상 동작

---

## Step 4. US 리포트 인제스트 (해당 시)

```bash
python -m cores.archive.ingest --dir prism-us/reports/ --market us --dry-run
python -m cores.archive.ingest --dir prism-us/reports/ --market us
```

---

## Step 5. DB 확인

```bash
python3 - <<'EOF'
import sqlite3, os
c = sqlite3.connect('archive.db')
print("report_archive :", c.execute('SELECT COUNT(*) FROM report_archive').fetchone()[0], "건")
print("enrichment     :", c.execute('SELECT COUNT(*) FROM report_enrichment').fetchone()[0], "건")
print("DB size        :", round(os.path.getsize('archive.db') / 1024 / 1024, 2), "MB")
EOF
```

**기대값**: report_archive 38건 이상, DB 20~60 MB

---

## Step 6. 장기 가격 히스토리 백필

> ⚠️ **새벽(01:00~05:00)에 실행 권장** — 기존 분석 파이프라인과 겹치면 OOM 위험

```bash
# concurrency 2 — 1코어 2GB 서버 안전 설정
python update_current_prices.py --concurrency 2
```

**소요시간**: 38종목 기준 약 20~40분 (KIS API 응답 속도에 따라 다름)

완료 후 확인:
```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('archive.db')
print("가격 히스토리:", c.execute('SELECT COUNT(*) FROM ticker_price_history').fetchone()[0], "행")
print("return_current 있는 리포트:", c.execute(
    'SELECT COUNT(*) FROM report_enrichment WHERE return_current IS NOT NULL'
).fetchone()[0], "건")
EOF
```

---

## Step 7. crontab 등록

```bash
crontab -e
```

아래 3줄 추가:

```cron
# PRISM Archive — 일간 인사이트 (매일 새벽 2시)
0 2 * * * cd /root/prism-insight && source venv/bin/activate && python -m cores.archive.auto_insight --mode daily --market both >> logs/auto_insight.log 2>&1

# PRISM Archive — 주간 인사이트 (매주 월요일 새벽 3시)
0 3 * * 1 cd /root/prism-insight && source venv/bin/activate && python -m cores.archive.auto_insight --mode weekly --market both >> logs/auto_insight.log 2>&1

# PRISM Archive — 장기 가격 업데이트 (매주 월요일 새벽 4시)
0 4 * * 1 cd /root/prism-insight && source venv/bin/activate && python update_current_prices.py --concurrency 2 >> logs/price_update.log 2>&1
```

> 기존 `weekly_firecrawl_intelligence.py` 스케줄(일요일 11시)과 시간대 분리됨

---

## Step 8. logs 디렉토리 확인

```bash
mkdir -p logs
```

---

## Step 9. archive_api 서버 실행 (양서버 모드 시)

db-server에서만. 단일 서버 모드라면 Step 10만.

```bash
# .env 에 없으면 키 생성 후 추가
grep -q ARCHIVE_API_KEY .env || echo "ARCHIVE_API_KEY=$(openssl rand -hex 32)" >> .env
grep -q ARCHIVE_API_HOST .env || echo "ARCHIVE_API_HOST=127.0.0.1" >> .env
grep -q ARCHIVE_API_PORT .env || echo "ARCHIVE_API_PORT=8765" >> .env

# 실행
nohup python archive_api.py >> logs/archive_api.log 2>&1 &
curl -s http://127.0.0.1:8765/health
```

app-server의 `.env`에 추가:
```
ARCHIVE_API_URL=http://127.0.0.1:8765    # SSH 터널 경유
ARCHIVE_API_KEY=<db-server와 동일값>
INSIGHT_DAILY_LIMIT=20
```

SSH 터널(권장):
```bash
# app-server (prism 유저)
sudo apt install -y autossh
sudo systemctl enable --now archive-tunnel  # systemd 유닛 미리 생성
```

---

## Step 10. 텔레그램 봇 재시작

```bash
pkill -f telegram_ai_bot
sleep 2
nohup python telegram_ai_bot.py >> logs/telegram_ai_bot.log 2>&1 &
```

기동 로그 확인:
```
INFO  Registered 14 bot commands via BotFather API
```
→ BotFather 메뉴에 `/insight` 가 자동 등록됩니다.

---

## Step 11. /insight 동작 확인

텔레그램에서:

```
/insight
→ 봇: "질문을 입력해주세요"
→ 삼성전자 분석 내용 요약해줘
→ 봇: [LLM 합성 답변]
```

---

## 장기 가격 데이터 쌓인 후 (1주일 뒤)

아래 쿼리가 가능해집니다:

```
/insight
→ 하락장에서 분석된 종목 중 현재 수익률 30% 이상인 것들의 공통점은?
→ 봇: [패턴 분석 답변]
```

---

## 환경변수 (신규)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ARCHIVE_API_URL` | (없음) | 설정 시 two-server 모드 (HTTP 호출). 미설정 시 single-server 모드 (direct import). |
| `ARCHIVE_API_KEY` | (없음) | 양 서버 공통 Bearer 토큰. `openssl rand -hex 32` 권장. |
| `ARCHIVE_API_HOST` | `0.0.0.0` | db-server의 archive_api 바인딩. SSH 터널 사용 시 `127.0.0.1`. |
| `ARCHIVE_API_PORT` | `8765` | db-server의 archive_api 포트. |
| `INSIGHT_DAILY_LIMIT` | `20` | 사용자당 일일 `/insight` 호출 한도. KST 자정 리셋. `0` = 무제한. |

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 인제스트 파싱 실패 | 파일명 형식 불일치 | `TICKER_회사명_YYYYMMDD_mode_model.md` 형식 확인 |
| enrichment 0건 | KIS API 없음 | 정상 — raw MD만 저장됨, 수익률 계산 skip |
| `/insight` 응답 없음 | `ARCHIVE_API_URL` 미설정 | 단일서버 모드로 동작 중 (정상), OpenAI API 키 확인 |
| 가격 업데이트 실패 | KIS API 키 미설정 | KR은 skip, US는 yfinance로 자동 fallback |
| OOM (Out of Memory) | concurrency 너무 높음 | `--concurrency 1`로 낮추기 |
| `archive.db not found` | 인제스트 미실행 | Step 3~4 실행 |
| `/insight` 일일 한도 메시지 | 쿼터 초과 | `INSIGHT_DAILY_LIMIT` 조정 또는 KST 자정 대기 |
| BotFather 메뉴에 /insight 안 보임 | 봇이 재기동 안 됐거나 post_init 실패 | 봇 로그에 `Registered N bot commands` 확인 |
| `/insight` 답변 품질 낮음 | 컨텍스트 부족 (첫 주 운영) | 리포트 인제스트 수량 + persistent_insights 축적 대기 |
| weekly 요약 미생성 | 주간 건수 <6 | 건수 충족 전엔 정상. 쿼터 풀어 더 쌓기. |
| reply 해도 반응 없음 | 30분 TTL 초과 | `/insight` 새로 시작 |

---

## 관련 문서

- [`ARCHIVE_API_SETUP.md`](ARCHIVE_API_SETUP.md) — 두 서버 분리 운영 방법 (선택)
- [`ARCHIVE_VALIDATION.md`](ARCHIVE_VALIDATION.md) — 로컬 검증 시나리오 7단계
