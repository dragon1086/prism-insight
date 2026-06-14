# PRISM-BTC 매매일지+부검 자가개선 파이프라인 설계 — 2026-06-12

> 핸드오프 §3-1 구현. 핵심 원칙: **LLM은 주문 경로 밖** (§4 동결 규칙).
> 트레이딩 기어(동결)와 학습 기어(LLM)가 DB를 통해서만 맞물린다.

## 톱니바퀴 구조

```
[트레이딩 기어 — 동결]                [학습 기어 — LLM]
core/ + engine/ + live/shadow ──▶ btc_trading_history (종결 트레이드)
        ▲                                │ tick 끝에서, 비차단
        │                                ▼
        │                     journal.extract_facts()   ← 순수 결정적 (LLM 없음)
        │                                │ facts JSON — 모든 숫자의 유일한 출처
        │                                ▼
        │                     postmortem.analyze()      ← LLM (해석만, 숫자 생성 금지)
        │                                ▼
        │                     btc_journal / btc_lessons (교훈 수명주기)
        │                                │ 주간 압축 (--weekly)
        │                                ▼
        │                     가설 백로그 (suggested_backtest 포함)
        │                                │
        └── 사람 승인 + 백테스트 합격 시에만 ── 룰 변경 (자동 반영 절대 금지)
```

## 불변 조건 (Invariants)

1. **LLM 격리**: postmortem 은 btc_journal/btc_lessons 에만 쓴다.
   btc_positions/btc_meta/주문 경로는 읽기조차 안 한다 (facts 를 통해서만 본다).
2. **숫자 단일 출처**: 모든 수치는 extract_facts 가 계산. LLM 은 해석 텍스트만.
   R분해는 `gross_r - fee_r - funding_r ≈ net_r` 자가검증 필드 포함.
3. **비차단**: runner.tick 의 트레이딩 처리가 모두 끝난 뒤 실행.
   예외는 이벤트 로그로 흡수 — 부검 실패가 데몬을 멈출 수 없다.
4. **데이터 무손실**: facts 는 LLM 호출 전에 먼저 저장(status='facts_only').
   LLM 실패 시 다음 틱 재시도 (최대 3회), 실패해도 facts 는 영구 보존.
5. **교훈 수명주기 강제**: observation → hypothesis → (백테스트) validated/rejected.
   validated + Rocky 승인 없이는 어떤 교훈도 룰이 될 수 없다.

## 결정적 사실 (extract_facts)

- identity: trade_id/side/tranche/entry·exit time·price/reason/leverage/num_legs
- r_decomposition: gross_r, fee_r, funding_r, net_r + 자가검증 잔차
- excursion (30m 봉, 보유구간): MFE_R/MAE_R (초기 스탑거리 기준),
  time_to_mfe_h, holding_hours, capture_ratio(net_r/mfe_r)
- entry_context/exit_context: `_build_snapshot_at` 재구성 (결정적 — 같은 klines → 같은 스냅샷):
  alignment_score, ts_4h/ts_1d(trend_strength), TF별 trend
- baseline: 백테스트 6년 R분포 백분위 (backtest/results/*_trades.csv) + 동결 스펙 기대치

## LLM 게이트웨이 (postmortem)

프로바이더 체인: ① anthropic SDK(키 있으면) → ② `claude -p` CLI(설치 확인됨 v2.1.160)
→ ③ 둘 다 불가 시 facts_only 보류. 타임아웃 120s, 모델 env `BTC_POSTMORTEM_MODEL` (기본 sonnet).
프롬프트 계약: facts 의 숫자만 인용 / 전략 룰 동결 명시 / 출력 JSON 스키마 고정
(situation_analysis, judgment_evaluation, execution_quality, lessons[{category,text,testable,suggested_backtest}],
pattern_tags, one_line_summary, confidence_score).

## 테이블 (btc_* 만 생성 — 주식 테이블 불간섭)

- btc_journal: trade_id, facts(json), analysis(json), one_line, pattern_tags,
  confidence, status(facts_only|analyzed|failed), llm_provider, llm_ms, attempts
- btc_lessons: source_journal_id, category, lesson, status(observation|hypothesis|
  validated|rejected|retired), suggested_backtest, evidence(json)

## 적용 지점

- runner.tick 마지막 (하트비트 직전): `journal.process_pending(root_conn, tf_data, mode, limit=1)`
- CLI: `python -m live.journal --backfill` (미처리 전체) / `--weekly` (주간 압축→가설)
- 주간 압축 LaunchAgent 등록은 수동 검증 후 별도 (crontab 금지 규칙 준수)
