# 매매일지·메모리·재진입 피드백

> 완료된 거래를 회고하고, 원칙·직관·성과 통계를 다음 진입 판단에 제공하는 KR/US 시스템입니다.

**소스 검증 기준**: 2026-07-29
**주의**: 이 시스템은 자율 강화학습이 아닙니다. LLM 회고와 결정론적 규칙·기능 플래그를 결합합니다.

## 1. 전체 구조

```text
거래 청산 완료
  -> JournalManager.create_entry()
  -> LLM 회고 JSON
  -> trading_journal 저장
  -> lessons에서 trading_principles 추출
  -> 성과·원칙·직관·동일 종목 이력을 다음 매수 프롬프트에 제공
  -> score adjustment와 재진입 쿨다운

주간 압축
  -> Layer 1 상세 일지
  -> Layer 2 요약
  -> Layer 3 직관
  -> 오래된 원칙·직관 정리
```

주요 진입점:

| 기능 | KR | US |
|---|---|---|
| 일지 관리 | `tracking/journal.py` | `prism-us/tracking/journal.py` |
| 압축 | `tracking/compression.py` | `prism-us/tracking/compression.py` |
| 청산 호출 | `stock_tracking_agent.py` | `prism-us/us_stock_tracking_agent.py` |
| 공통 CLI | `compress_trading_memory.py` | 같은 CLI가 US pass도 호출 |
| 재진입 | `reentry_cooldown.py` | 공용 |

## 2. 활성화와 청산 시점

`ENABLE_TRADING_JOURNAL`은 기본 비활성입니다.

```dotenv
ENABLE_TRADING_JOURNAL=true
```

### KR

KR pending-order 경로는 DB 상태가 내구성 있게 `CLOSED`가 된 뒤 exit-effect outbox를 통해 일지를 생성합니다. `exit_intent_id`로 LLM 실행 전후 중복을 억제합니다.

레거시 청산 경로도 매도 DB 처리 후 일지를 호출하지만, 신규 pending-order 경로가 더 강한 순서·멱등성 보장을 가집니다.

### US

US는 매도·이력·보유 상태 트랜잭션 커밋 후 일지를 생성합니다. `market='US'`를 저장하지만 KR의 exit-effect outbox/`exit_intent_id`와 동일한 멱등성 경로는 없습니다.

따라서 “KR/US 완전 동일 구현”으로 설명하면 안 됩니다.

## 3. 일지 응답 계약

`create_trading_journal_agent(language, market)`는 거래 전후 데이터를 받아 구조화된 회고를 생성합니다.

```json
{
  "situation_analysis": {
    "market_at_buy": "...",
    "market_at_sell": "...",
    "what_happened": "...",
    "key_changes": ["..."],
    "unexpected_events": ["..."]
  },
  "judgment_evaluation": {
    "buy_decision_quality": "good|neutral|poor",
    "buy_quality_reason": "...",
    "sell_decision_quality": "good|neutral|poor",
    "sell_quality_reason": "...",
    "what_was_right": ["..."],
    "what_was_wrong": ["..."],
    "missed_signals": ["..."]
  },
  "lessons": {
    "key_lesson": "...",
    "what_to_repeat": ["..."],
    "what_to_avoid": ["..."],
    "improved_rule": "..."
  },
  "pattern_tags": ["..."],
  "one_line_summary": "..."
}
```

파싱·저장은 `JournalManager.create_entry()`가 담당합니다. 과거 문서의 `write_trading_journal()` 함수는 존재하지 않습니다.

## 4. 데이터베이스

### 4.1 `trading_journal`

핵심 필드:

| 그룹 | 필드 |
|---|---|
| 식별 | `id`, `ticker`, `company_name`, `exit_intent_id` |
| 거래 | `buy_date`, `sell_date`, `buy_price`, `sell_price`, `quantity`, `profit_rate`, `holding_days` |
| 근거 | `buy_reason`, `sell_reason`, `trigger_type`, `buy_score` |
| 회고 | `situation_analysis`, `judgment_evaluation`, `lessons`, `pattern_tags`, `one_line_summary` |
| 압축 | `compression_layer`, `compressed_summary`, `created_at`, `last_compressed_at` |

KR canonical 선언에는 `market`이 처음부터 존재하지 않습니다. US 초기화가 공유 테이블에 `market` 열을 추가하고 기존 기본값을 `KR`로 보정합니다.

`exit_intent_id`는 값이 있을 때만 유일한 partial index를 사용해 KR pending-exit 일지 중복을 막습니다.

### 4.2 `trading_principles`

새 일지의 `lessons`에서 즉시 원칙을 추출합니다.

주요 정보:

- 원칙 유형과 내용
- 적용 조건
- 근거 거래 수
- 신뢰도·활성 여부
- 생성·갱신 시각
- US 초기화 후 `market`

원칙은 Layer 2→3 압축 때만 생기는 것이 아닙니다.

### 4.3 `trading_intuitions`

반복 패턴을 압축한 카드입니다.

| 필드군 | 예 |
|---|---|
| 분류 | `category`, `scope`, `pattern_description` |
| 적용 | `conditions`, `recommended_action`, `avoid_action` |
| 근거 | `supporting_trades`, `success_rate`, `confidence` |
| 상태 | `is_active`, `created_at`, `updated_at` |

### 4.4 `user_memories`와의 구분

텔레그램 사용자별 `user_memories`는 별도 시스템입니다. 사용자 ID·메모리 유형·JSON 내용·중요도·레이어·메시지 메타데이터를 저장하며, 결정론적 요약 축약을 사용합니다. 거래 저널의 `trading_intuitions`와 혼동하지 마십시오.

## 5. 다음 매수에 제공되는 컨텍스트

KR/US 매니저는 대체로 다음을 제공합니다.

1. 현재 트리거의 과거 성과
2. 성과 상위 트리거 5개
3. 근거 거래가 2개 이상인 보편 원칙
4. 동일 종목 최근 일지 3개
5. 활성 직관 최대 10개
6. 최근 청산 경고

KR은 직관을 카테고리당 최대 3개씩 먼저 분배한 뒤 남는 자리를 채웁니다. US는 상위 10개를 단순 선택합니다.

동일 종목 이력 쿼리는 `compression_layer`를 제한하지 않습니다. Layer 2/3 행도 선택될 수 있으며, 현재 프롬프트는 `compressed_summary`보다 원래의 `one_line_summary`, `lessons` 등을 사용합니다.

### Performance Tracker 피드백 루프 (Self-Improving Trading)

`get_performance_tracker_stats()`는 트리거별 승률·평균 수익률 등을 조회합니다.

```text
trigger performance
  -> 프롬프트 컨텍스트
  -> LLM 매수 시나리오
  -> 정수 score adjustment
  -> KR/US별 결정 게이트
```

점수 조정은 정수이며 `-3..+3`으로 제한됩니다. 과거 문서의 `-0.5` 예시는 현재 계약과 맞지 않습니다.

KR과 US 적용 차이:

- KR: 조정치를 LLM 프롬프트의 제안으로 넣고 시나리오에 저장합니다. 최종 핵심 매수 조건은 `decision == "Enter"`입니다.
- US: 조정치를 넣은 뒤 `adjusted_score = buy_score + score_adjustment`를 다시 계산해 최소점수 게이트에 직접 사용합니다.

`missed_opportunities`, `traded_vs_watched`는 내부 통계로 계산될 수 있지만 현재 매수 컨텍스트 문자열에는 주입하지 않습니다.

## 6. 최근 청산과 재진입 제한

### 6.1 프롬프트 경고

동일 종목이 최근 7일 안에 청산되었으면 재진입 주의를 제공합니다.

KR은 다음 상세까지 포함합니다.

- 매도 사유·매도 당시 맥락
- 핵심 변화
- 매도 판단 품질 사유
- 놓친 신호

US는 요약·교훈·최근성 중심입니다.

### 6.2 결정론적 쿨다운

기본값:

| 항목 | 기본값 |
|---|---|
| 기능 | enabled |
| 강제 방식 | SHADOW |
| 일반 이익 청산 | 0시간 |
| 손실 청산 | 24시간 |
| stop/trend exit | 수익이어도 risk exit로 분류 가능 |

`REENTRY_COOLDOWN_LIVE=true`일 때 일반 live 차단을 적용합니다. 위험 청산만 별도로 live 강제하는 플래그도 존재합니다.

동일 종목 추가 매수(pyramiding)는 재진입 쿨다운 예외입니다.

현재 KR/US 호출부는 `account_key`를 넘기지 않으므로 함수의 계좌 필터 기능과 달리 실제 게이트는 모든 계좌에서 가장 최근 종목 청산을 볼 수 있습니다.

### 6.3 최근 손실 점수 패널티

최근 48시간 안의 손실 또는 stop/trend exit는 순점수 조정이 음수 2점이 되도록 제한합니다. 데이터·쿼리 오류는 fail-open입니다.

## 7. 메모리 압축

### 7.1 CLI

```bash
# 실제 압축
python3 compress_trading_memory.py

# 변경 없이 대상 확인
python3 compress_trading_memory.py --dry-run

# 임계값 조정
python3 compress_trading_memory.py \
  --layer1-age 7 \
  --layer2-age 30 \
  --min-entries 3

# 최소 건수 무시
python3 compress_trading_memory.py --force
```

추가 옵션:

- `--skip-cleanup`
- `--max-principles`
- `--max-intuitions`
- `--stale-days`
- `--archive-days`

Docker cron은 일요일 03:00 KST에 이 루트 스크립트를 한 번 실행합니다. 스크립트가 KR pass 뒤 `run_us_compression()`을 호출합니다.

### 7.2 KR 압축

KR은 LLM 기반입니다.

1. Layer 1→2 대상의 현재 가격을 수집해 사후 결과를 보강합니다.
2. 압축 에이전트가 그룹 요약과 교훈을 생성합니다.
3. Layer 2→3에서 반복 패턴을 직관으로 생성합니다.
4. 별도로 최근 90일·최대 40행·최소 5행 누적 코퍼스의 직관을 새로고침합니다.

기본 나이 임계값은 Layer 1 7일, Layer 2 30일이며 최소 3행입니다.

### 7.3 US 압축

US는 결정론적입니다.

1. Layer 1→2: `one_line_summary`를 복사하고 가능하면 yfinance 사후 메모를 덧붙입니다.
2. Layer 2→3: `pattern_tags`를 집계합니다.
3. 두 번 이상 나타난 태그를 통계적 직관 후보로 만듭니다.

따라서 “KR과 US가 같은 LLM 압축을 쓴다”는 설명은 잘못입니다.

## 8. 알려진 구현 제약

2026-07-29 코드 감사에서 다음 공유 스키마 위험을 확인했습니다.

1. 루트 KR 저널·압축 쿼리 다수가 `market='KR'` 조건을 명시하지 않습니다. US 초기화로 공유 DB에 `market` 열이 추가된 환경에서는 첫 KR pass가 US 행을 먼저 압축할 가능성이 있습니다.
2. US 압축 일부는 `trading_intuitions.supporting_count`를 사용하지만 canonical KR 스키마는 `supporting_trades`를 선언합니다.
3. `tests/test_us_compression_wiring.py`의 최소 스키마는 `supporting_count`를 직접 만들어 실제 공유 스키마 불일치를 가립니다.
4. 현재 테스트는 CLI의 실제 KR→US 순차 pass를 한 DB에서 끝까지 검증하지 않습니다.

이 항목은 문서상의 개념 차이가 아니라 운영 데이터 정합성 위험입니다. 배포 DB의 마이그레이션 상태에 따라 증상이 달라질 수 있으므로, 스키마 통합과 시장별 쿼리 격리를 별도 수정으로 다뤄야 합니다.

## 9. 통계와 조회

```python
from tracking.compression import MemoryCompressor

compressor = MemoryCompressor("stock_tracking_db.sqlite")
stats = compressor.get_stats()
```

대표 키:

```json
{
  "entries_by_layer": {
    "layer1_detailed": 15,
    "layer2_summarized": 45,
    "layer3_compressed": 120
  },
  "active_intuitions": 28,
  "oldest_uncompressed": "2026-01-15",
  "avg_intuition_confidence": 0.72,
  "avg_intuition_success_rate": 0.68
}
```

## 10. 검증

집중 테스트:

```bash
pytest tests/test_trading_journal.py -v
pytest tests/test_journal_intuition_noise.py -v
pytest tests/test_journal_recent_loss_penalty.py -v
pytest tests/test_journal_exit_intent_idempotency.py -v
pytest tests/test_reentry_cooldown.py -v
pytest tests/test_exit_kind_churn_guard.py -v
pytest tests/test_us_compression_wiring.py -v
pytest prism-us/tests/test_journal_recent_loss_penalty.py -v
```

빠른 직접 실행도 지원합니다.

```bash
python3 tests/test_trading_journal.py
```

현재 집중 테스트는 핵심 계약을 보호하지만 [알려진 구현 제약](#8-알려진-구현-제약)의 실제 공유 스키마·순차 pass 결함을 완전히 재현하지 않습니다.

## 11. 관련 문서

- [파이프라인 아키텍처](PIPELINE_ARCHITECTURE_ko.md)
- [후보 선별·배치 알고리즘](TRIGGER_BATCH_ALGORITHMS.md)
- [AI 에이전트 시스템](CLAUDE_AGENTS_ko.md)
