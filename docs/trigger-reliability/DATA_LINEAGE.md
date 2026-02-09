# Data Lineage — 트리거 신뢰도 카드 데이터 계보

> 코드 추적 완료 기준 (2026-02-08). 컨텍스트 유실 방지를 위해 기록.

## 데이터 흐름도

```
[매 분석 사이클]
stock_tracking_enhanced_agent.py :: save_to_watchlist()
  → watchlist_history INSERT (진입/관망 모든 종목)
  → analysis_performance_tracker INSERT (tracking_status='pending')
      ├── trigger_type, trigger_mode, analyzed_price, decision, was_traded
      ├── buy_score, min_score, target_price, stop_loss, risk_reward_ratio
      └── skip_reason (관망 사유)

[일간 배치 - 크론]
performance_tracker_batch.py :: PerformanceTrackerBatch
  → analysis_performance_tracker UPDATE
      ├── 7일 경과: tracked_7d_return, tracked_7d_price
      ├── 14일 경과: tracked_14d_return, tracked_14d_price
      ├── 30일 경과: tracked_30d_return, tracked_30d_price
      └── 30일 완료: tracking_status='completed'

[매도 시점]
tracking/journal.py :: JournalManager.create_entry()
  → GPT-5.2에게 매매 회고 분석 요청
  → trading_journal INSERT
      ├── situation_analysis (매수/매도 시점 컨텍스트 비교)
      ├── judgment_evaluation (매수/매도 판단 품질 평가)
      ├── lessons [{condition, action, reason, priority}, ...]
      ├── pattern_tags [태그 목록]
      └── one_line_summary
  → extract_principles(lessons, journal_id)
      → trading_principles UPSERT
          ├── 새 원칙: INSERT (confidence=0.5, supporting_trades=1)
          └── 기존 원칙: UPDATE (confidence += 0.1, supporting_trades++)

[주간 배치 - 크론]
compress_trading_memory.py → tracking/compression.py
  → Layer1(0-7일) → Layer2(8-30일): 요약 압축
  → Layer2 → Layer3(31일+): LLM이 trading_intuitions 생성
      └── category, condition, insight, confidence, success_rate

[다음 매매 판단 시 - 피드백 루프]
tracking/journal.py :: get_context_for_ticker()
  → LLM 프롬프트에 주입:
      1. performance_tracker_stats (트리거별 승률/수익률)
      2. universal_principles (supporting_trades >= 2인 것만)
      3. 같은 종목 과거 매매 이력 (최근 3건)
      4. trading_intuitions (confidence 상위 10개)
  → score_adjustment (경험 기반 점수 보정)
```

## 테이블별 상세

### analysis_performance_tracker (KR: 184건, US: 59건)

| 컬럼 | 설명 | 트리거 카드 사용 |
|------|------|-----------------|
| trigger_type | 분석 트리거 유형 | **GROUP BY 축** |
| analyzed_price | 분석 시점 가격 | - |
| tracked_7d_return | 7일 후 수익률 | 참고용 |
| tracked_14d_return | 14일 후 수익률 | 참고용 |
| tracked_30d_return | 30일 후 수익률 | **분석 승률/수익률** |
| was_traded | 실제 매매 여부 (0/1) | 필터용 |
| decision | entry/watch | - |
| tracking_status | pending/in_progress/completed | completed만 사용 |

**Writer**: `stock_tracking_enhanced_agent.py:638` (INSERT), `performance_tracker_batch.py:256` (UPDATE)
**US Writer**: `prism-us/us_stock_tracking_agent.py:1035` (INSERT), `prism-us/us_performance_tracker_batch.py` (UPDATE)

### trading_history (KR: 63건, US: 2건)

| 컬럼 | 설명 | 트리거 카드 사용 |
|------|------|-----------------|
| trigger_type | 진입 트리거 (2026-01-12~) | **GROUP BY 축** |
| profit_rate | 수익률 (%) | **실매매 승률/수익률** |
| sell_date | 매도일 | 기간 필터 |

**주의**: trigger_type은 2026-01-12부터 저장 시작. 이전 데이터는 NULL.

### trading_principles (KR: 96건)

| 컬럼 | 설명 | 트리거 카드 사용 |
|------|------|-----------------|
| condition | 조건 텍스트 | **키워드 매칭**으로 트리거 연결 |
| action | 행동 지침 | 표시용 |
| confidence | 신뢰도 (0-1) | 정렬 기준 |
| supporting_trades | 검증 매매 수 | 표시용 |
| source_journal_ids | 원본 저널 ID 목록 | 역추적 가능 |
| scope | universal/sector/market | universal만 사용 |

**Writer**: `tracking/journal.py:317` (INSERT/UPDATE)
**US Writer**: `prism-us/tracking/journal.py:356`

## 트리거 유형 목록

### KR (stock_tracking_enhanced_agent.py에서 확인)
- `intraday_surge` — 장 중 급등
- `volume_surge` — 거래량 급증
- `news_trigger` — 뉴스 기반
- `AI분석` — trigger_type이 NULL일 때 기본값 (대시보드 코드에서 COALESCE)

### US (prism-us/us_stock_tracking_agent.py에서 확인)
- 별도 확인 필요 — Phase 1에서 US DB 쿼리로 확인

## 원칙 ↔ 트리거 매칭 전략 (Phase 1)

직접적인 FK가 없으므로 2단계 접근:

1. **텍스트 키워드 매칭** (간단, Phase 1에서 사용)
   ```python
   TRIGGER_KEYWORDS = {
       "intraday_surge": ["급등", "장중", "surge", "intraday"],
       "volume_surge": ["거래량", "volume", "수급"],
       "news_trigger": ["뉴스", "news", "공시", "disclosure"],
   }
   ```

2. **journal_id 역추적** (정확, Phase 1 이후 개선 시)
   ```sql
   -- principles.source_journal_ids → journal.ticker → history.trigger_type
   SELECT p.condition, p.action, h.trigger_type
   FROM trading_principles p
   JOIN trading_journal j ON j.id IN (원칙의 source_journal_ids 파싱)
   JOIN trading_history h ON h.ticker = j.ticker
   ```
