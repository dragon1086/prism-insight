# Archive & Insight Query System — 검증 시나리오 및 운영 요구사양

> **System**: `cores/archive/` + `archive_query.py`
> **Updated**: 2026-04-15

---

## 검증 시나리오

### 전제 조건

```bash
cd /root/prism-insight   # 프로젝트 루트
source venv/bin/activate
# .env에 OPENAI_API_KEY 설정 확인 (Step 5부터 필요)
```

---

### Step 1 — Dry-run (API 호출 없음, DB 쓰기 없음)

```bash
python -m cores.archive.ingest --dir reports/ --market kr --dry-run
python -m cores.archive.ingest --dir prism-us/reports/ --market us --dry-run
```

**기대값**:
- KR: `reports/` 에서 38개 내외 `.md` 파일 탐지
- US: `prism-us/reports/` 에서 24개 내외 `.md` 파일 탐지
- 각 파일에 대해 ticker/date/mode/market 파싱 결과 출력
- DB 변경 없음, API 호출 없음

**실패 시 확인**:
- `파일명 파싱 실패`: 파일명 형식 확인 (`TICKER_회사명_YYYYMMDD_mode_model.md`)
- `ModuleNotFoundError`: `venv` 활성화 확인

---

### Step 2 — FTS5 인제스트 (API 없이 raw MD 저장)

```bash
python -m cores.archive.ingest --dir reports/ --market kr
```

> 💡 KIS API 없으면 enrichment는 skip되고 raw MD만 저장됨 (정상 동작)

**기대값**:
- `archive.db` 생성 (프로젝트 루트)
- 로그에 `Inserted report` 메시지 반복 출력

**DB 확인**:
```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('archive.db')
print("report_archive:", c.execute('SELECT COUNT(*) FROM report_archive').fetchone()[0], "건")
print("enrichment:", c.execute('SELECT COUNT(*) FROM report_enrichment').fetchone()[0], "건")
print("market_timeline:", c.execute('SELECT COUNT(*) FROM market_timeline').fetchone()[0], "건")
print("DB size:", round(__import__('os').path.getsize('archive.db') / 1024 / 1024, 2), "MB")
EOF
```

**기대값**: `report_archive` 38건 이상, DB 크기 20~60 MB

---

### Step 3 — FTS5 검색 (LLM 없음, 즉시 응답)

```bash
# 한국어 검색
python archive_query.py --search "반도체" --market kr

# 영어 검색
python archive_query.py --search "semiconductor" --market us

# 종목별 조회
python archive_query.py --ticker 000660 --market kr

# 목록 조회
python archive_query.py --list --market kr --limit 10
```

**기대값**:
- 응답 1초 이내
- 관련 리포트 목록 (ticker, date, score) 반환
- 한국어/영어 모두 정상 검색

---

### Step 4 — 통계 확인

```bash
python archive_query.py --stats
```

**기대값**:
```
총 리포트: N건
KR: N건 / US: N건
최초 리포트: YYYY-MM-DD
최근 리포트: YYYY-MM-DD
enriched: N건
```

---

### Step 5 — LLM 자연어 쿼리 (OpenAI API 필요)

```bash
# 비용 최소 모델로 테스트
python archive_query.py "반도체 종목 중 수익률 높은 분석은?" --market kr --model gpt-4.1-mini

# US 쿼리
python archive_query.py "AI sector performance analysis" --market us --model gpt-4.1-mini

# KR/US 통합
python archive_query.py "한국과 미국 중 PRISM 정확도가 더 높은 시장은?" --market kr
```

**기대값**:
- 10초 이내 응답
- 근거 리포트 ID 목록 포함
- 두 번째 동일 쿼리: 캐시 히트 (100ms 이내)

---

### Step 6 — Auto-insight Dry-run

```bash
python -m cores.archive.auto_insight --mode weekly --market both --dry-run
python -m cores.archive.auto_insight --mode daily --market kr --dry-run
```

**기대값**:
- L2 주간 인사이트 내용 콘솔 출력
- DB `insights` 테이블에 저장 없음 (`--dry-run`)

---

### Step 7 — Backfill (기존 performance_tracker 연결)

```bash
python -m cores.archive.ingest --dir reports/ --market kr --backfill --dry-run
```

**기대값**:
- `analysis_performance_tracker.report_path` 역채움 대상 목록 출력
- `--dry-run` 제거 시 실제 UPDATE 실행

---

## 운영 요구사양 분석

### 현재 서버 환경

```
CPU: 1 vCore
RAM: 2 GB
기존 프로세스: PRISM 봇 + 분석 파이프라인 + Telegram 봇 + 스케줄러
```

---

### 컴포넌트별 리소스 예측

| 컴포넌트 | CPU | RAM | 주기 | 비고 |
|---------|-----|-----|------|------|
| `archive.db` (SQLite FTS5) | 상시 0% | 0 MB (idle) | — | 파일 기반, 별도 프로세스 없음 |
| 인제스트 훅 (fire-and-forget) | 순간 5~15% | +50~100 MB | 분석 1회당 | Semaphore(5), 분석 후 백그라운드 |
| FTS5 검색 (`--search`) | 순간 10% | +30 MB | 쿼리 시 | 1초 이내 완료 |
| LLM 쿼리 (`archive_query.py`) | 순간 5% | +50 MB | 요청 시 | 네트워크 대기 위주, CPU 부담 적음 |
| Auto-insight L2 (주간) | 10~30% | +150 MB | 주 1회 | 가장 무거운 작업 |
| 최초 백필 (38 KR + US 전량) | 30~60% | +200 MB peak | 1회만 | KIS API Semaphore(5) |

---

### ⚠️ 1코어 2GB 서버 주의사항

#### 문제 1: 최초 백필 시 메모리 압박

- 현재 코드: `Semaphore(5)` — 5개 리포트 동시 처리
- KIS API OHLCV × 5 동시 호출 시 **+200 MB** 순간 사용
- 기존 프로세스와 합산 시 **OOM 위험**

**권장 조치**:
```python
# cores/archive/ingest.py 의 Semaphore 값을 서버에서는 낮춤
# 운영 서버 실행 시:
python -m cores.archive.ingest --dir reports/ --market kr --concurrency 2
```
> `--concurrency` 옵션이 없으면 코드에서 `Semaphore(2)`로 하드코딩 변경 필요

#### 문제 2: Auto-insight L2 (주간) 스케줄 충돌

- L2는 LLM 합성 포함, **~150 MB** 추가 사용
- 기존 `weekly_firecrawl_intelligence.py`와 시간대 겹치면 위험

**권장 스케줄** (crontab):
```cron
# Auto-insight 주간 (매주 월요일 새벽 3시 — 기존 일요일 11시와 분리)
0 3 * * 1 cd /root/prism-insight && python -m cores.archive.auto_insight --mode weekly --market both >> logs/auto_insight.log 2>&1

# Auto-insight 일간 (매일 새벽 2시 — 아침 분석과 4시간 전)
0 2 * * * cd /root/prism-insight && python -m cores.archive.auto_insight --mode daily --market both >> logs/auto_insight.log 2>&1
```

#### 문제 3: `archive.db` 크기 증가

- 현재 38 KR 리포트 기준: **~50 MB**
- 1년 누적 (250 리포트 기준): **~350 MB**
- 서버 디스크 여유 공간 확인 필요

**권장**:
```bash
# 분기별 FTS5 최적화 (cron 추가)
0 4 1 */3 * cd /root/prism-insight && python3 -c "import sqlite3; c=sqlite3.connect('archive.db'); c.execute('INSERT INTO report_archive_fts(report_archive_fts) VALUES(\"optimize\")')" >> logs/fts_optimize.log 2>&1
```

---

### 결론: 1코어 2GB에서의 운영 가능 여부

| 기능 | 가능 여부 | 조건 |
|------|----------|------|
| 인제스트 훅 (fire-and-forget) | ✅ 가능 | 분석 중 아닌 시간대에는 영향 없음 |
| FTS5 검색 | ✅ 가능 | 상시 30 MB 이하 |
| LLM 쿼리 (Telegram /insight) | ✅ 가능 | 동시 요청 1~2건 이하 |
| Auto-insight 일간 | ⚠️ 주의 | 새벽 2시 실행, 기존 분석과 시간 분리 |
| Auto-insight 주간 | ⚠️ 주의 | 새벽 3시 실행, 메모리 여유 시 |
| 최초 백필 (전량) | ⚠️ 주의 | **Semaphore(2)로 낮추고 새벽에 실행** |

**핵심 권장사항**: 최초 백필은 서버 트래픽이 없는 새벽 1~4시에 `--concurrency 2` 옵션으로 실행
