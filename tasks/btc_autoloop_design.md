# PRISM-BTC 자가개선 자동 루프 설계 — 2026-06-12

> Rocky 지시: "내가 손 안대고도 개선이 일어나게" — 주식 시스템(직관→매매 반영)과 동일하게
> 교훈이 실제 매매 행동을 바꾸는 닫힌 루프. 단, 판정권은 LLM 이 아니라 **데이터(백테스트+OOS)**.

## 닫힌 루프 전체 그림

```
[매 트레이드]  부검(LLM) ──▶ 가설 {param, value} (화이트리스트 메뉴에서만 선택)
                                  │ btc_lessons (hypothesis)
[매주 자동]   연구공장 factory --run
                                  │ 챔피언 config 로 baseline 실행
                                  │ 챔피언 + 후보 오버라이드로 variant 실행
                                  │ train(2020~2024) + OOS(2025~) 이중 게이트
                                  ├─ 불합격 → rejected + evidence (기각 가설 누적 — 재시도 방지)
                                  └─ 합격   → validated + btc_overrides 자동 활성
[매 틱]       runner tick 시작 시 활성 오버라이드를 라이브에 적용
                                  │ → 다음 진입/청산부터 실제 행동이 바뀜 (사람 개입 0)
[매주 자동]   기존 활성 오버라이드 재검증 (최신 데이터 포함 재실행)
                                  └─ 더 이상 못 이기면 자동 은퇴(retired) — 자동 롤백
```

## 챔피언 config 정의

`유효 config = 동결 상수 (코드) + btc_overrides(status='active')`
- 코드는 영원히 동결 — 오버라이드는 DB 레이어. `git diff` 없이 행동 변화 추적 가능.
- 모든 비교의 baseline 은 "현재 챔피언" — 개선이 누적되어도 항상 현역 대비로만 승격.

## 화이트리스트 (LLM 이 넘을 수 없는 울타리)

| param | 모듈 타깃 | 동결값 | 허용범위 |
|---|---|---|---|
| ENTRY_SCORE_MIN | engine.config | 70 | 55~90 |
| TS_MIN | engine.config | 2.0 | 1.5~4.0 |
| BE_TRAIL_ACTIVATE_R | backtest.engine + live.shadow | 1.5 | 1.0~3.0 |
| TRAILING_TF | backtest.engine + live.shadow | 12h | {4h,12h,1d} |

- 멀티 타깃인 이유: backtest.engine 은 런타임 글로벌 참조, live.shadow 는 import 시 값 복사
  → 두 네임스페이스 모두 패치해야 백테스트와 라이브가 같은 행동.
- engine.config 게이트는 signal.py 가 함수-로컬 임포트 → config 모듈 패치만으로 충분 (검증 테스트 필수).
- 범위 밖 가설 / 화이트리스트 외 param / 구조 변경(코드) 가설 → 자동검증 불가로 hypothesis 에 머묾
  (사람 리뷰 대기열 — 자동 루프의 의도적 경계).

## 합격 게이트 (결정적 — LLM 무관여)

train(2020-01-01~2024-12-31) & OOS(2025-01-01~현재) 각각:
1. liq_approach_count == 0 (절대)
2. trade_count >= 40 (train) / >= 8 (OOS) — 표본 미달 = 기각
3. PF_variant >= PF_champion × 1.05 (train — 의미있는 개선)
4. MDD_variant <= MDD_champion × 1.10 (악화 상한)
5. OOS: PF_variant >= max(1.3, PF_champion_oos × 0.9) — 과적합 차단
6. total_return_variant >= total_return_champion × 0.95 (train)

전부 통과 시에만 활성. **동률·애매 = 기각** (변경은 비용이다).

## 자동 안전장치 (사람 없이 안전한 이유)

1. **울타리**: 화이트리스트 + 범위 — 어떤 가설도 울타리 밖 행동 불가
2. **복잡도 예산**: 동시 활성 오버라이드 최대 2개 (초과 시 성능순 교체 평가)
3. **주간 재검증**: 활성 오버라이드가 최신 데이터로 게이트 재통과 못 하면 자동 은퇴
4. **전량 기록**: 모든 판정의 evidence(양쪽 메트릭 전문) DB 보존 — 사후 감사 가능
5. **격리**: 연구공장 실패는 데몬과 무관 (별도 LaunchAgent, 예외 흡수)
6. **기각 메모리**: rejected 가설은 영구 기록 — 동일 (param,value) 재검증 스킵 (진동 방지)

## 실행 주체 (전부 자동)

- 부검+가설: 데몬 tick (기존)
- 주간 파이프라인: LaunchAgent `com.prism.btc-research` (일요일 18:00 KST)
  → `live.journal --weekly` (기억압축·가설 생성) → `research.factory --run` (검증·활성·은퇴)
- 라이브 반영: 데몬 tick 시작 시 `overrides.apply_active()` — 재시작 불요

## 단계 구분 (이번 세션 = Phase 1)

- Phase 1 (지금): 위 전체 — 파라미터 오버라이드 자동 루프
- Phase 2 (데이터 축적 후): LLM 사이즈 게이트 (진입 시 0.5~1.2x, §4 허용 역할) + 반사실 추적
- Phase 3 (데모 이후): 챌린저 모드 병렬 전진검증 (구조 변경용)
