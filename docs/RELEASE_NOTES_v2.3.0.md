# PRISM-INSIGHT v2.3.0

발표일: 2026년 2월 7일

## 개요

PRISM-INSIGHT v2.3.0은 **자기개선 매매 피드백 루프 완성**, **보안 강화**, **스폰서십/프론트엔드 개선**, **다수의 버그 수정**을 포함한 마이너 버전입니다. Performance Tracker 데이터가 KR/US 공통으로 매수 결정에 자동 반영되는 피드백 루프가 완성되었으며, 불필요한 시스템 메트릭을 제거하여 LLM 판단 편향을 방지합니다.

**주요 수치:**
- 총 14개 커밋
- 22개 파일 변경
- +803 / -151 라인

---

## 주요 변경사항

### 1. 자기개선 매매 피드백 루프 완성 ⭐ CORE

과거 매매 결과가 미래 매수 결정에 자동으로 반영되는 Self-Improving Trading Cycle이 KR/US 공통으로 완성되었습니다.

#### 1.1 Performance Tracker → LLM 프롬프트 주입 (US 신규)

기존에 US `_extract_trading_scenario`에서 journal context가 LLM 프롬프트에 주입되지 않던 버그를 수정했습니다. 이제 KR과 동일하게 트리거별 승률과 과거 경험이 매수 결정에 반영됩니다.

```python
# prism-us/us_stock_tracking_agent.py - _extract_trading_scenario()
journal_context = self.get_journal_context(ticker, sector, trigger_type)
adjustment, reasons = self.get_score_adjustment(ticker, sector, trigger_type)

# LLM 프롬프트에 주입
prompt = f"""
### Current Portfolio Status:
{portfolio_info}
### Trading Value Analysis:
{rank_change_msg}
{score_adjustment_info}    ← Score 조정 제안 (NEW)
{journal_context}          ← 트리거 승률 + 과거 경험 (NEW)
### Report Content:
{report_content}
"""
```

#### 1.2 KR trigger_type 전달 수정

KR `_get_relevant_journal_context`와 `_get_score_adjustment_from_context`에 `trigger_type` 파라미터가 전달되지 않던 버그를 수정했습니다. 이제 트리거별 승률이 정확히 조회됩니다.

```python
# stock_tracking_agent.py
# Before: _get_relevant_journal_context(ticker, sector, market_condition=None)
# After:  _get_relevant_journal_context(ticker, sector, market_condition=None, trigger_type=trigger_type)

# Before: _get_score_adjustment_from_context(ticker, sector)
# After:  _get_score_adjustment_from_context(ticker, sector, trigger_type)
```

#### 1.3 LLM 프롬프트 노이즈 제거

`_format_performance_context`에서 개별 매수 판단에 무관한 시스템 메트릭 2개를 제거했습니다:

| 제거 항목 | 제거 이유 |
|-----------|----------|
| `missed_opportunities` | "놓친 기회 N건" → LLM에 FOMO 유발, 매수 공격성 증가 위험 |
| `traded_vs_watched` | "매수 평균 vs 관망 평균" → 일방적 편향 (과신 또는 위축) |

유지 항목:
| 유지 항목 | 유지 이유 |
|-----------|----------|
| **Trigger Win Rate** | 현재 트리거의 과거 승률 — 직접적으로 관련 |
| **Trigger Ranking** | 트리거별 성과 순위 — 상대적 참고 |

#### 1.4 예상 효과

| 트리거 승률 | LLM이 보는 정보 | 예상 효과 |
|------------|----------------|-----------|
| >65% (좋은 트리거) | "Win rate 72% (n=15)" | 매수 장려 |
| 35-65% (보통) | "Win rate 48% (n=20)" | 중립 |
| <35% (나쁜 트리거) | "Win rate 28% (n=8)" | 매수 억제 |
| n<3 (데이터 부족) | (표시 안함) | 변화 없음 |

> 상세 문서: [docs/TRADING_JOURNAL.md - Performance Tracker 피드백 루프](TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)

---

### 2. 보안 강화

#### 2.1 민감 파일 제거 및 .gitignore 강화

Git 히스토리에 포함된 민감 파일을 제거하고 `.gitignore`를 강화했습니다:

| 항목 | 조치 |
|------|------|
| `trigger_results_us_morning_*.json` | 삭제 — 실제 분석 결과 데이터 |
| `youtube_cookies.txt` | 삭제 — 인증 쿠키 |
| `sqlite/stock_tracking_db` | 삭제 — 실제 거래 DB |
| `.gitignore` | +30줄 추가 (쿠키, DB 바이너리, 트리거 결과 등) |

---

### 3. 텔레그램 메시지 버그 수정

#### 3.1 KR 알림 한글 복구

v2.2.0 영문화 과정에서 KR 트리거 알림이 영어로 전송되던 문제를 수정했습니다:

```python
# Before: "🔔 Intraday Surge Alert: Samsung Electronics"
# After:  "🔔 장중 급등 알림: 삼성전자"
```

수정 파일:
- `stock_analysis_orchestrator.py` — KR 트리거 타입명 한글 복구
- `trigger_batch.py` — KR 알림 텍스트 한글 복구

#### 3.2 회사명 번역 모델 변경

회사명 번역에 사용되는 모델을 `gpt-4o-mini`로 변경하여 안정성을 높였습니다:

```python
# cores/company_name_translator.py
# Before: model="gpt-5-mini" (간헐적 빈 응답)
# After:  model="gpt-4o-mini" (안정적)
```

#### 3.3 Evaluator f-string 이스케이프 수정

`telegram_summary_evaluator_agent.py`에서 f-string 내 JSON 템플릿의 중괄호 이스케이프 오류를 수정했습니다.

---

### 4. 스폰서십 및 프론트엔드 개선

#### 4.1 AI3 Platinum Sponsor 배지

README(EN/KO), 랜딩 페이지, 대시보드에 AI3 Platinum Sponsor 배지를 추가했습니다:

| 위치 | 변경 |
|------|------|
| `README.md` / `README_ko.md` | Platinum Sponsor 섹션 + 랜딩/대시보드 링크 |
| `examples/landing/app/page.tsx` | AI3 스폰서 배지 + WrksAI 로고 |
| `examples/dashboard/components/dashboard-header.tsx` | 프리미엄 스폰서 바 |
| `examples/dashboard/components/project-footer.tsx` | 랜딩 페이지 링크 |

#### 4.2 Self-Improving 기능 소개

README Key Features에 자기개선 매매 항목을 추가했습니다:

- EN: "Self-Improving — Trading journal feedback loop"
- KO: "자기개선 매매 — 매매 일지 피드백 루프"

---

### 5. 문서화

| 문서 | 변경 |
|------|------|
| `docs/TRADING_JOURNAL.md` | +107줄 — Self-Improving Trading Cycle 섹션 추가 (다이어그램, 피드백 경로, KR/US 구현 비교) |
| `CLAUDE.md` | v2.2.2 버전 히스토리 추가 |
| `README.md` | Self-Improving 기능 + 상세 문서 링크 |
| `README_ko.md` | 자기개선 매매 + 상세 문서 링크 |

---

### 6. US 대시보드 AI보유 분석 수정

US 대시보드의 "AI보유 분석" 탭에 데이터가 표시되지 않던 문제를 수정했습니다.

| 항목 | 변경 |
|------|------|
| `get_ai_decision_summary()` 메서드 추가 | KR과 동일하게 AI 판단 통계(총 분석, 매도 신호, 보유 유지, 조정 필요, 평균 신뢰도) 계산 |
| `summary.ai_decisions` 하드코딩 제거 | 0으로 고정되어 있던 값을 실제 데이터 기반으로 계산 |
| `holding_decisions` 날짜 필터 개선 | `today`(KST) → `MAX(decision_date)` 변경, KST/EST 시차로 인한 데이터 누락 방지 |

수정 파일: `examples/generate_us_dashboard_json.py`

---

### 7. LLM 프롬프트 주입 최적화

보편적 원칙(Universal Principles)이 LLM 프롬프트에 과다 주입되는 문제를 개선했습니다.

| 항목 | Before | After |
|------|--------|-------|
| `LIMIT` | 10개 | 5개 |
| 필터 조건 | `is_active = 1` 만 | `is_active = 1 AND supporting_trades >= 2` |
| 예상 토큰 | ~1,200 tokens | ~800 tokens (32% 감소) |

검증되지 않은 원칙(거래 1건으로 생성된 원칙)이 LLM에 주입되어 잘못된 판단을 유도할 위험이 있었으며, 이를 `supporting_trades >= 2` 필터로 방지합니다.

수정 파일:
- `tracking/journal.py` — `get_universal_principles()` 필터 강화
- `prism-us/tracking/journal.py` — 동일 적용

---

### 8. 테스트 수정

| 테스트 | 변경 |
|-------|------|
| `tests/test_trading_journal.py` | `test_context_includes_universal_principles`에서 v2.2.0 영문화 이후 한글/영문 모두 허용하도록 수정 |

---

## 변경된 파일

### 신규 파일

| 파일 | 설명 |
|------|------|
| `examples/dashboard/public/wrks_ai_logo.png` | AI3 WrksAI 로고 이미지 |
| `examples/landing/public/wrks_ai_logo.png` | AI3 WrksAI 로고 이미지 |

### 주요 수정 파일

| 파일 | 주요 변경 |
|------|----------|
| `prism-us/us_stock_tracking_agent.py` | **_extract_trading_scenario에 journal context 주입** |
| `prism-us/tracking/journal.py` | **_format_performance_context 노이즈 제거 + get_universal_principles 필터 강화** |
| `stock_tracking_agent.py` | **trigger_type 전달 수정** |
| `tracking/journal.py` | **_format_performance_context 노이즈 제거 + get_universal_principles 필터 강화** |
| `docs/TRADING_JOURNAL.md` | **Self-Improving Trading Cycle 문서 추가** |
| `stock_analysis_orchestrator.py` | KR 트리거 타입명 한글 복구 |
| `trigger_batch.py` | KR 알림 텍스트 한글 복구 |
| `cores/company_name_translator.py` | 번역 모델 gpt-4o-mini로 변경 |
| `cores/agents/telegram_summary_evaluator_agent.py` | f-string 이스케이프 수정 |
| `examples/generate_us_dashboard_json.py` | **US AI보유 분석 탭 데이터 누락 수정** (ai_decision_summary 추가, 날짜 필터 개선) |
| `.gitignore` | 민감 파일 패턴 30줄 추가 |

---

## 업데이트 방법

```bash
# 1. 코드 업데이트
git pull origin main

# 2. 변경사항 확인
# - 추가 의존성 없음
# - DB 마이그레이션 없음
# - 설정 변경 없음
```

---

## 테스트

```bash
# 매매 일지 테스트 (28 tests)
pytest tests/test_trading_journal.py -v

# KR journal context 포맷 검증
python3 -c "
import sqlite3
conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
from tracking.journal import JournalManager
jm = JournalManager(cursor=cursor, conn=conn, enable_journal=True)
stats = {
    'current_trigger': {'trigger_type': 'test', 'win_rate': 0.7, 'total': 10, 'avg_30d': 0.03},
    'missed_opportunities': {'missed_gains_count': 3, 'avg_missed_gain': 0.08}
}
parts = jm._format_performance_context(stats)
assert 'Missed opportunities' not in '\n'.join(parts)
print('PASSED: missed_opportunities removed')
"

# US journal context 포맷 검증
python3 -c "
import sys; sys.path.insert(0, 'prism-us')
import sqlite3
conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
from tracking.journal import USJournalManager
jm = USJournalManager(cursor=cursor, conn=conn, enable_journal=True)
stats = {
    'current_trigger': {'trigger_type': 'test', 'win_rate': 0.5, 'total': 8, 'avg_30d': 0.01},
    'traded_vs_watched': {'traded': {'avg_30d': 0.01, 'count': 5}, 'watched': {'avg_30d': 0.03, 'count': 8}}
}
parts = jm._format_performance_context(stats)
assert 'Decision quality' not in '\n'.join(parts)
print('PASSED: traded_vs_watched removed')
"
```

---

## 알려진 제한사항

1. **피드백 루프 데이터 최소량**: n<3인 트리거는 피드백 정보가 표시되지 않음 (의도적 설계)
2. **LLM 앵커링 가능성**: 트리거 승률 수치에 LLM이 과도하게 의존할 수 있음 — 모니터링 필요
3. **롤백 방법**: 문제 발생 시 `enable_journal=False`로 즉시 비활성화 가능

---

## 기여자

- PRISM-INSIGHT Development Team
- Claude Opus 4.6 (AI Pair Programmer)

---

**Document Version**: 2.3.0
**Last Updated**: 2026-02-07

---

## 📢 텔레그램 구독자용 요약

> 아래 내용을 텔레그램 채널에 공유할 수 있습니다.

---

PRISM-INSIGHT v2.3.0 업데이트 안내

발표일: 2026년 2월 7일

안녕하세요, 프리즘 인사이트 구독자 여러분!
v2.3.0 버전이 출시되었습니다.

[핵심 업데이트: 자기개선 매매 시스템]

AI가 과거 매매 결과를 학습하여 미래 매수 결정에 반영합니다.

이번 업데이트의 핵심은 "매매 피드백 루프"입니다. 과거에 특정 트리거(급등, 거래량 급증 등)로 매수한 결과가 어땠는지를 AI가 기억하고, 같은 트리거가 다시 발생했을 때 이 경험을 참고합니다.

모든 분석 종목은 7일 / 14일 / 30일 수익률이 자동 추적되며, 이 데이터를 기반으로 트리거별 승률이 계산됩니다.

예를 들어:
- "장중 급등" 트리거의 30일 승률이 72%라면 AI가 더 적극적으로 매수 판단
- "거래량 급증" 트리거의 30일 승률이 28%라면 AI가 더 보수적으로 판단
- 데이터가 3건 미만이면 기존과 동일하게 판단 (충분한 데이터가 쌓일 때까지)

이 기능은 한국/미국 시장 모두에 적용됩니다.

또한, 과거 매매에서 추출된 보편적 원칙이 AI에게 전달될 때 2건 이상 검증된 원칙만 전달되도록 필터를 강화했습니다. 검증되지 않은 원칙이 AI 판단을 흐리는 것을 방지하고, 전달량도 약 32% 줄여 효율성을 높였습니다.

[버그 수정]

- US 대시보드 AI보유 분석 수정: 미국주식 "AI보유 분석" 탭에 데이터가 표시되지 않던 문제가 수정되었습니다. 한국/미국 시차로 인해 날짜가 맞지 않아 데이터가 누락되던 문제와, 통계가 계산되지 않던 문제를 함께 해결했습니다.
- 한글 알림 복구: KR 트리거 알림이 영어로 나오던 문제가 수정되었습니다.
- 회사명 번역 안정화: 간헐적으로 회사명이 비어있던 문제가 수정되었습니다.
- 보안 강화: 민감 파일이 저장소에서 제거되었습니다.

[스폰서]

AI3 (WrksAI) 플래티넘 스폰서 배지가 대시보드와 랜딩 페이지에 추가되었습니다.

[대시보드에서 확인하세요]

https://analysis.stocksimulation.kr/?tab=insights

매매 성과가 실시간으로 반영됩니다. AI의 자기개선 효과가 승률에 어떤 영향을 미치는지 함께 지켜봐주세요!

문의사항은 언제든 봇에게 메시지 남겨주세요!
