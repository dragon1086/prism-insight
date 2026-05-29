# KR 종목 스크리닝 재설계 (#289) — 설계 문서

> **작성일**: 2026-05-29 | **이슈**: #289 한국시장 종목스크리닝 로직 수정
> **상태**: 설계 승인 완료 → 구현 계획(writing-plans) 대기
> **전제**: 이 시스템은 **백테스트 불가** → 점진적·되돌리기 쉬운 변경 + 라이브 관찰로 진행

---

## 1. 문제 정의

운영 실데이터 진단(메모리 `project_screening_buyagent_mismatch`)과 페르소나 패널 5인 만장일치 결론:

- **증상**: KR 폭등장에서 진입률이 낮음(4/10). 최근 30일 매수에이전트 score=2가 51건, 진입 2주째 0.
- **오해**: "상승률 필터 20→15로 낮추면 됨" → **cosmetic**. 주력 트리거(갭상승·일일상승)는 v1.16.6에서 **이미 15%**.
- **진짜 병목**: KR 스크리닝이 **"당일 raw 급등률"** 로만 거름. KR은 가격제한폭 ±30% + 개인 매수 광기 탓에 **같은 상승률이라도 US보다 훨씬 과열·climax(소진 급등) 종목**이 후보로 올라옴 → CAN SLIM 매수에이전트가 **정당하게 2/10**으로 거절. 매수에이전트는 정상이며 끄거나 min_score를 낮추면 안 됨.

### 코드로 확정된 근본 원인
1. **과열 가드 전무**: `trigger_batch.py:calculate_agent_fit_metrics`는 v1.16.6 이후 손절 고정 → `sl_score`가 **항상 1.0**, 목표가는 최근 N일 고가(min +15%). 따라서 이동평균에서 멀리 떨어진(extended) climax 종목도 `agent_fit_score` 만점.
2. **선정이 순수 모멘텀**: `normalize_and_score`의 composite_score = 거래량/갭/금액 비율. 과열 여부 무관.
3. **매수에이전트도 과열 보상**: `cores/agents/trading_agents.py:143` 모멘텀신호 #3 "52주 신고가 5% 이내"가 가점, 트리거 타입 자동 +1까지(`:147-148`).
4. **RS 단일일·비활성**: `trigger_batch.py:910`의 RelativeStrength는 *하루치* 변동률−시장평균(노이즈). 다주 RS 없음. `trigger_macro_sector_leader`는 strong_bull에서 비활성(`:1338`).

---

## 2. 철학 정합성 점검 (윌리엄 오닐 / CAN SLIM 학파)

본 시스템의 매매 철학은 **윌리엄 오닐 추세추종**(메모리 `user_trading_philosophy`). 수정 방향을 학파 원칙과 대조:

| 오닐 학파 원칙 | 본 설계 | 정합성 |
|---|---|---|
| 상대강도(RS)가 핵심 — 다개월 가중으로 주도주 선별, RS Rating ≥80 | Stage 3: 다주 RS 가점 | ✅ (단일일 RS 폐기) |
| 적정 피벗/베이스 돌파에서 매수, **extended 추격 금지** (피벗 +5% 초과 추격 금기) | Stage 2: extension 감점 | ✅✅ |
| 거래량 동반 돌파 | 기존 트리거 유지 | ✅ |
| 신고가 근접은 호재, 그러나 **climax top은 매도신호** | extension으로 climax 소진 구간 down-weight | ✅ |
| 시장방향(M): 확인된 상승장엔 주도주 적극 매수, 단 상승장 *말기*에 climax 출현 | regime 차등(strong_bull RS↑, 과열 완화하되 **0 아님**) | ✅ |

**ADR 정규화는 미너비니(오닐 학파) 정통** — 변동성/extension 평가에 ADR 배수를 명시 사용.

### 매수에이전트(CAN SLIM 매트릭스) 궁합
매수에이전트는 이미 오닐 로직 내장: Parabolic 활성(90일 ≥+30%, 30일 ≥+10%, `trading_agents.py:112-123`), Distribution Day Kill Switch(`:125-129`), CAN SLIM R/R·손절 매트릭스.

**핵심 통찰**: 현재 스크리닝은 climax 종목을 매수에이전트에 보내고 에이전트가 거절하는 구조 — 스크리닝이 에이전트의 오닐 판단과 **싸우며 슬롯/관심을 낭비**. 본 설계는 새 철학 추가가 아니라 **매수에이전트가 이미 가진 오닐 판단을 스크리닝 상류로 전파**하는 것 = 구조적 정렬.

### 설계에 박은 3가지 보정
1. **RS = 다주/다개월 가중** (단일일 RS 폐기·격상).
2. **Extension = MA/베이스 이격 + ADR 정규화** (52주 고가 기준 금지 → 매수에이전트 신호 #3과 **이중 평가 충돌 방지**, 상호보완). 건강한 돌파(신고가 근접·저extension)는 양쪽 통과, climax(신고가 근접·고extension)만 스크리닝에서 down-weight.
3. **strong_bull에서도 climax/소진 보호 유지**, RS 비중만 상향 (오닐의 "상승장 말기 climax" 경고 반영).

---

## 3. 설계

### 핵심 아이디어
후보를 **자르지 않고**(soft) `final_score`에 **RS 가점 + 과열 감점**을 추가해 **국면별 가중치**로 재정렬한다. "시장보다 꾸준히 강하면서(고RS) 아직 너무 멀리 안 뜬(저extension)" 종목을 위로, "막판 불꽃(climax)"은 아래로.

현재: `final_score = composite_norm·0.3 + agent_fit·0.7` (`trigger_batch.py:1183-1186`)
신규:
```
final_score = w_comp·composite_norm + w_agent·agent_fit + w_rs·rs_score + w_ext·extension_score
```

### Stage 1 — 보조 트리거 임계 통일 (저위험, **KR only**)
`≤20%` → `≤15%`:
- KR: `trigger_batch.py:403`, `:587`, `:725`

주력 트리거(이미 15%)와 일관성. 롤백 = 숫자 3개 되돌림.

> **US 제외 결정 (2026-05-29)**: #289의 전제가 "KR과 US는 구조가 다르다"이다. US는 ±제한폭이 없고 개인 과열이 적어 15~20% 상승이 정당한 돌파일 때가 많으며, 진단상 US 병목은 스크리닝 품질이 아니라 슬롯/섹터 캡이다. US 보조 트리거를 조이면 멀쩡한 후보만 줄어듦 → **#289는 전 단계 KR 전용**, `us_trigger_batch.py` 미수정.

### Stage 2 — Extension(과열) soft score (KR only)
종목별 지표:
- `MA20` = 최근 20거래일 종가 평균
- `ADR_pct` = 최근 20거래일 `(High/Low − 1)×100`의 평균 (미너비니 ADR)
- `extension_in_adr = ((Close − MA20) / MA20 × 100) / ADR_pct`
  → "현재가가 MA20에서 평소 하루 변동폭(ADR)의 몇 배만큼 떠 있나"
- `extension_score`(0~1, 높을수록 건강): `T_low`(예 2 ADR) 이하=1.0(무감점), `T_high`(예 6 ADR) 이상=0.0(최대감점), 사이 선형. 임계는 모듈 상수로 튜닝 가능.

데이터: 기존 `get_multi_day_ohlcv`(Open/High/Low/Close/Volume/Amount 반환)를 **lookback 10→20일**로 확대해 산출. 이미 `score_candidates_by_agent_criteria` 루프에서 종목별 호출 중 → 추가 네트워크 비용 최소.

### Stage 3 — RS(다주) 가점 (KR only) + strong_bull 재활성화
- `rs_relative = 종목 ~60거래일(약 3개월) 수익률 − 벤치마크 동기간 수익률`
  벤치마크 = 해당 종목 시장 지수(코스피/코스닥 N일 수익률). `get_multi_day_ohlcv(days≈60)` + 지수 시계열 1회.
- `rs_score` = 후보군 내 `rs_relative` 정규화(0~1).
- `trigger_macro_sector_leader`:
  - strong_bull 비활성(`:1338`) 해제 — 폭등장에서도 섹터 주도주 후보 진입.
  - **단일일 RelativeStrength(`:910`)는 섹터 shortlist용으로 유지**(top100 종목에 60일 fetch는 과비용). 실제 다주 RS 재정렬은 **downstream `final_score`(아래 통합)** 에서 모든 트리거 후보에 일괄 적용 — macro 트리거 후보도 여기서 60일 RS·과열점수로 재정렬됨.

> **구현 정교화**: `calculate_agent_fit_metrics`의 target price는 10일 고가 기반이므로 그 lookback을 바꾸면 R/R(=agent_fit)이 변동(스코프 외). 따라서 MA20·ADR·60일수익률은 **별도 함수 `calculate_screening_signals`** 에서 60일 OHLCV 1회 fetch로 산출(에이전트 스코어 불변). 대상은 최종 후보군(~30-60종목)뿐.

### 국면별 가중치 (모듈 상수 1곳, 재배포 없이 튜닝)
| 국면 | w_comp(모멘텀) | w_agent(R/R) | w_rs(RS) | w_ext(과열) |
|---|---|---|---|---|
| strong_bull | 0.20 | 0.35 | **0.30** | **0.15** |
| moderate_bull | 0.25 | 0.35 | 0.20 | 0.20 |
| sideways | 0.20 | 0.35 | 0.15 | **0.30** |
| moderate_bear / strong_bear | 0.15 | 0.35 | 0.15 | **0.35** |

- 합 = 1.0. `agent_fit`(R/R) 비중 유지로 손익비 안전망 보존.
- strong_bull: RS↑(주도주 선별)·과열 완화하되 **0 아님**(보정3, climax 보호).
- 평온/하락장: 과열 감점 강화(추격이 가장 위험한 구간).
- macro_context의 `market_regime`로 분기(기존 `_get_regime_slots` 패턴 재사용).

---

## 4. 범위 결정

- **전 단계(Stage 1·2·3) KR 전용.** 진단상 US는 스크리닝이 건강(매수에이전트 avg 5~7점, 슬롯/섹터 캡에 막힘). 과열감점을 US에 넣으면 멀쩡한 파이프라인 훼손 위험. Stage 1 임계 통일도 US에는 불필요(위 결정).
- **US 캡 완화는 #289와 별개** — 별도 이슈로 분리(out of scope).
- **매수에이전트(`trading_agents.py`)는 미수정** — 구조적으로 호환되게 스크리닝만 정렬(스코프 최소). 보정2로 신호 #3과 충돌 없음.
- **극단 climax 하드컷은 v1 제외** — soft 재정렬만으로 시작, 라이브 관찰 후 필요 시 도입(reversibility 우선).

---

## 5. 관측 (백테스트 대체)

- 선정 JSON(`run_batch` 출력, `trigger_batch.py:1402` 부근)의 `stock_info`에 `extension_in_adr`, `rs_relative`, `rs_score`, `extension_score`, 각 sub-score 기록 → 매일 가시 확인.
- #294 매매일지 영향추적 / 주간 리포트로 **KR 진입수·평균 buy_score** 변화를 2~4주 관찰.
- 가중치·임계는 모듈 상수 → 재배포 없이 튜닝/롤백.

## 6. 롤백 전략
- Stage 1: 숫자 5개 되돌림.
- Stage 2·3: 국면 가중치에서 `w_rs=w_ext=0`으로 두면 `final_score`가 composite·agent 재정렬로 환원(RS/과열 영향 제거). 즉 **킬 스위치 = 상수 0**. 완전한 기존 동작 복원이 필요하면 같은 상수 테이블에서 comp/agent를 0.3/0.7로 되돌림(가중치는 활성 항목 합으로 정규화).
- `trigger_macro_sector_leader` strong_bull 재활성화는 `:1338` 한 줄 복원으로 되돌림.

## 7. 리스크 / 미해결
- **RS 벤치마크 선택**(코스피 vs 코스닥 vs 종목별 소속 지수): 구현 계획에서 종목 시장 구분 매핑 확인 필요.
- **데이터 비용**: 후보 ~30-60종목 × 60일 OHLCV. 기존 루프에 흡수되나 RS용 추가 일수만큼 호출 비용 증가 — 캐싱/배치 검토.
- **soft score 효과 검증**: 백테스트 불가 → 2~4주 라이브 관찰로만 판단. 효과 미미 시 가중치 상향 또는 하드컷 도입(2차).
- **KR/US 미러 주의**: Stage 1만 양쪽, Stage 2·3 KR 전용 — US 파일에 과열/RS 로직 넣지 말 것.

## 8. 작업 규약
- 코드(.py) 변경 → feature 브랜치 + PR (예: `feat/issue-289-kr-screening-rs-extension`).
- 커밋 끝: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- 파일 이동/생성 없음(기존 함수 확장 위주) → crontab import 깨짐 없음. 그래도 머지 전 `python -c "import trigger_batch"` 검증.
