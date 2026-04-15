# PRISM Archive — 운영 서버 배포 가이드

> **Updated**: 2026-04-16
> PR #262 머지 후 이 가이드대로 순서대로 실행하세요.

---

## 체크리스트

- [ ] Step 1: PR #262 머지
- [ ] Step 2: 코드 pull + 의존성 설치
- [ ] Step 3: KR 리포트 인제스트
- [ ] Step 4: US 리포트 인제스트 (해당 시)
- [ ] Step 5: DB 확인
- [ ] Step 6: 장기 가격 히스토리 백필
- [ ] Step 7: crontab 등록
- [ ] Step 8: logs 디렉토리 확인
- [ ] Step 9: 텔레그램 봇 재시작
- [ ] Step 10: /insight 동작 확인

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

## Step 9. 텔레그램 봇 재시작

기존 방식대로 봇 프로세스 재시작. `/insight` 명령어가 새로 추가됨.

---

## Step 10. /insight 동작 확인

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

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 인제스트 파싱 실패 | 파일명 형식 불일치 | `TICKER_회사명_YYYYMMDD_mode_model.md` 형식 확인 |
| enrichment 0건 | KIS API 없음 | 정상 — raw MD만 저장됨, 수익률 계산 skip |
| `/insight` 응답 없음 | `ARCHIVE_API_URL` 미설정 | 단일서버 모드로 동작 중 (정상), OpenAI API 키 확인 |
| 가격 업데이트 실패 | KIS API 키 미설정 | KR은 skip, US는 yfinance로 자동 fallback |
| OOM (Out of Memory) | concurrency 너무 높음 | `--concurrency 1`로 낮추기 |
| `archive.db not found` | 인제스트 미실행 | Step 3~4 실행 |

---

## 관련 문서

- [`ARCHIVE_API_SETUP.md`](ARCHIVE_API_SETUP.md) — 두 서버 분리 운영 방법 (선택)
- [`ARCHIVE_VALIDATION.md`](ARCHIVE_VALIDATION.md) — 로컬 검증 시나리오 7단계
