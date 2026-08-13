# 후보 선별·배치 알고리즘

> KR/US 시장의 Market Pulse, 시장 체제, 트리거, 재점수, 주도주 조사, 배치 진입·청산 제어를 코드 기준으로 설명합니다.

**소스 검증 기준**: 2026-07-29
**주요 구현**: `trigger_batch.py`, `prism-us/us_trigger_batch.py`, `cores/market_pulse.py`, `cores/regime_policy.py`

이 문서의 수치는 코드 기본값입니다. 배포 환경 변수, 외부 데이터 품질, 기능 플래그에 따라 실제 동작은 달라질 수 있습니다.

## 1. 전체 흐름

```text
지수/시장 데이터
  ├─ Market Pulse 3상태 -> 배치 실행/휴지
  └─ 선별 체제 5상태 -> 트리거·재점수·슬롯 정책

거시경제/주도 섹터 조사
  -> KR/US 트리거 후보 합집합
  -> 절대·유동성·가격구조 필터
  -> agent fit + RS + extension 재점수
  -> 탑다운/바텀업 통합
  -> 기본 최대 3종목
  -> 종목별 최근 뉴스·동종 주도주 조사와 6개 분석
  -> 매수 점수·포트폴리오·재진입·피라미딩 게이트

별도 상시 루프
  -> hard stop
  -> trend exit
  -> pending order reconciliation
```

Market Pulse와 선별 체제는 서로 다른 상태 체계입니다.

| 체계 | 상태 | 목적 |
|---|---|---|
| Market Pulse | `UPTREND`, `UNDER_PRESSURE`, `CORRECTION` | 오전/오후 배치를 실행할지 결정 |
| 선별 체제 | `strong_bull`, `moderate_bull`, `sideways`, `moderate_bear`, `strong_bear` | 후보·재점수·탑다운 슬롯·진입 바닥점수 결정 |

## 2. Market Pulse와 배치 제어

### 2.1 기본 모드

`MARKET_PULSE_MODE`의 기본값은 `shadow`입니다.

- `off`: 계산·정책 적용 안 함
- `shadow`: 상태와 권고를 기록하지만 배치를 막지 않음
- `live`: 배치 실행/휴지 정책을 실제 적용

오류나 상태 미확정은 fail-open으로 처리합니다.

### 2.2 Distribution Day

기본 관측 규칙:

- 전일 대비 지수가 `-0.2%` 이하
- 거래량이 전일보다 증가
- 최근 25거래일 창
- 분배일 종가보다 지수가 `+5%` 회복하면 해당 분배일 만료

상태 전이의 핵심 기본값:

| 조건 | 결과 |
|---|---|
| 분배일 4~5개 | `UNDER_PRESSURE` 후보 |
| 분배일 6개 이상 | `CORRECTION` 후보 |
| 최근 고점 대비 하락 10% 이상 | `CORRECTION` 후보 |

회복은 랠리 시도 4일차 이후 `+1.25%` 상승과 거래량 증가를 기본 확인으로 사용합니다. 거래량이 없으면 가격 조건만으로 폴백할 수 있습니다.

### 2.3 실제 배치 정책

| Market Pulse | 오전 | 오후 |
|---|---|---|
| `UPTREND` | 실행 | 실행 |
| `UNDER_PRESSURE` | 실행 | 실행 |
| `CORRECTION` | 휴지 | 실행 |
| 미확정/오류 | 실행 | 실행 |

KR과 US 모두 `live`일 때만 이 표를 강제합니다. `CORRECTION`이 모든 분석을 중지시키는 것은 아닙니다.

## 3. 결정론적 시장 체제와 거시 조사

### 3.1 KR 체제

KR은 120일 이동평균을 1차 기준으로, 60/120 정렬과 최근 2주 수익률을 함께 봅니다.

- `strong_bull`: 120일선 위, 60일선 위, 정배열, 2주 수익률 `> 5%`
- `strong_bear`: 120일선 아래, 역배열, 2주 수익률 `< -5%`
- 중간 상태는 동일한 지표와 20일선 폴백으로 구분

### 3.2 US 체제

US는 200일 이동평균, 50/200 정렬, 최근 4주 수익률, VIX를 사용합니다.

- VIX 구간: `<15`, `<20`, `<25`, `>=25`
- `strong_bull`: 200일선·50일선 위, 정배열, 4주 `>3%`, VIX가 낮거나 보통
- `strong_bear`: 200일선 아래, 역배열, 4주 `<-5%`, VIX가 높음

### 3.3 LLM 거시경제 조사

오케스트레이터는 결정론적 체제와 사전 수집 데이터를 거시경제 에이전트에 전달합니다. 에이전트는 Perplexity 등으로 다음을 보강합니다.

- 주도·소외 업종
- 정책·금리·환율·원자재 리스크
- 시장 테마와 이벤트
- 업종별 신뢰도와 근거

프롬프트는 계산된 체제를 그대로 사용하도록 요구합니다. LLM은 체제 enum을 재판정하지 않습니다.

## 4. 공통 후보 필터

### 4.1 KR

대부분의 표준 트리거가 사용하는 기본 절대 조건:

| 조건 | 기본값 |
|---|---:|
| 거래대금 | 100억 원 이상 |
| 거래량 | 시장 평균의 20% 이상 |
| 시가총액 | 표준 트리거 대부분 5,000억 원 이상 |
| 표준 최대 등락률 | 15% |

과거 문서의 KR 상한 20%는 현재 코드·회귀 테스트와 다릅니다.

### 4.2 US

- S&P 500·Nasdaq 100 기반 주요 종목 유니버스
- 거래대금 1억 달러 이상
- 거래량 필터 적용
- 시가총액 필터는 현재 비활성

US의 `value_to_cap` 트리거는 `cap_df`가 필요하지만 현재 배치가 `cap_df=None`을 전달하므로 후보를 내지 못합니다. 문서상 활성 알고리즘이 아니라 알려진 배선 제약으로 취급해야 합니다.

## 5. 표준 트리거

### 5.1 오전

| 트리거 | 핵심 조건 | 트리거 내부 점수 |
|---|---|---|
| 거래량 급증 | 거래량 증가율 `>=30%`, 종가>시가 | 거래량 60 + 등락 40 |
| 갭 상승 모멘텀 | 갭 `>=1%`, 종가>시가 | 갭 50 + 등락 30 + 거래량 20 |
| 시총 대비 자금 유입 | 거래대금/시총 | 비율 50 + 거래대금 30 + 등락 20 |

### 5.2 오후

| 트리거 | 핵심 조건 | 트리거 내부 점수 |
|---|---|---|
| 일중 상승률 | KR `3~15%` | 등락 60 + 거래대금 40 |
| 마감 강도 | 양의 거래량, 종가>시가, `(종가-저가)/(고가-저가)` | 마감강도 50 + 등락 30 + 거래량 20 |
| 거래량 증가 횡보 | `|등락률|<=5%`, 거래량 `+50%`, 종가 `>=0.97*MA20` | 거래량 60 + 횡보 안정성 40 |

MA20을 계산하지 못한 경우 횡보 트리거는 해당 조건을 통과시키는 폴백이 있습니다.

US는 대체로 같은 구조지만 최대 등락률이 완전히 통일되어 있지 않습니다.

- gap/rise: 15%
- volume/value-to-cap 내부 경로: 20%

따라서 KR/US를 하나의 공통 상한으로 설명하면 안 됩니다.

## 6. 추가 후보: 주도 업종과 역발상 가치

### 6.1 Macro Sector Leader

거시경제 조사에서 나온 주도 업종과 거래대금 상위 후보를 연결합니다.

기본 구성:

- 거래대금 상위 100개 후보
- 업종 매칭
- 30일 상대강도 30%
- 거래대금 20%
- 거시 업종 신뢰도 30%
- 시가총액 20%

KR은 모든 선별 체제에서 실행합니다. US는 `strong_bull`에서 제외합니다.

### 6.2 Contrarian Value

다음 약세·횡보 체제에서만 실행합니다.

- `sideways`
- `moderate_bear`
- `strong_bear`

핵심 조건:

- 거래대금 상위 50개
- 당일 상승
- 52주 고점 대비 하락 `-40% ~ -15%`
- PER/PBR 양수

점수는 상승 30 + 낙폭 위치 20 + PER 30 + PBR 20입니다.

## 7. Agent fit과 재점수

### 7.1 Agent fit

트리거 유형별 고정 손절폭과 최근 고점을 사용합니다.

| 유형 | 기본 손절폭 |
|---|---:|
| 거래량·상승·마감 등 표준 유형 | 5% 또는 7% |
| Macro Sector Leader | 7% |
| Contrarian Value | 8% |

목표가는 최근 고점을 기본으로 하되 최소 `+15%`를 확보합니다.

```text
agent_fit = risk_reward_score * 0.60
          + stop_loss_score  * 0.40
```

### 7.2 기술·상대강도 입력

재점수는 과거의 “복합 30% + 에이전트 70%”가 아닙니다.

- 260거래일 데이터에서 O'Neil 원시 지표 계산
- 60일 수익률
- MA20 위치
- ADR 기반 과열·확장도
- 후보 간 상대강도

확장도 점수는 MA20 이격이 `<=2 ADR`이면 1, `>=6 ADR`이면 0으로 보간합니다.

`RS_RATING_ENABLED=false`가 기본입니다.

- 기본: 후보군 60일 수익률 min-max 상대강도
- `true`: percentile/99 방식 RS Rating을 실제 점수에 사용
- 비활성 상태에서도 shadow 계산·로그 가능

### 7.3 체제별 혼합 가중치

순서는 `trigger composite / agent fit / RS / extension`입니다.

| 체제 | Composite | Agent | RS | Extension |
|---|---:|---:|---:|---:|
| `strong_bull` | 0.20 | 0.35 | 0.30 | 0.15 |
| `moderate_bull` | 0.25 | 0.35 | 0.20 | 0.20 |
| `sideways` | 0.20 | 0.35 | 0.15 | 0.30 |
| `moderate_bear` | 0.15 | 0.35 | 0.15 | 0.35 |
| `strong_bear` | 0.15 | 0.35 | 0.15 | 0.35 |

강세에서는 RS 비중을, 약세에서는 과열 회피 비중을 높입니다.

## 8. 탑다운·바텀업 최종 선정

### 8.1 기본 슬롯

| 체제 | 탑다운 | 바텀업 | 기본 최대 |
|---|---:|---:|---:|
| `strong_bull` | 2 | 1 | 3 |
| `moderate_bull` | 1 | 2 | 3 |
| `sideways` | 1 | 2 | 3 |
| `moderate_bear` | 1 | 2 | 3 |
| `strong_bear` | 0 | 3 | 3 |

탑다운은 업종 정확 매칭 후 부분 문자열 매칭을 사용합니다.

```text
topdown_score = rerank_score * (1 + sector_confidence * 0.3)
```

바텀업은 트리거별 상위 1개로 다양성을 확보하고 전체 점수로 남은 자리를 채웁니다. 중복 종목은 통합합니다.

### 8.2 예외

“항상 3종목”이 아니라 기본 최대 3종목입니다.

- 후보 부족: 3개 미만
- `REGIME_WEAK_NO_TOPDOWN=true`: 횡보·약세 탑다운을 제거하고 총선정 수가 2개로 줄 수 있음
- post-FTD pilot 활성: 첫 5세션 동안 신규 진입을 1개로 제한하고 피라미딩 동결

## 9. 선택 후 최근 시장·주도주 조사

최종 후보 0~3개는 매매 전에 전체 보고서를 생성합니다.

KR `news_analysis`는 기준일 현재:

- 대상 종목의 최근 뉴스와 촉매
- 같은 업종의 주도주 2~3개
- 업종 추세
- 종목과 주도주의 상대적 위치

를 Perplexity/Firecrawl 근거로 조사하도록 요구합니다.

US도 Perplexity 설정 시 같은 목적의 조사를 수행합니다. 미설정이면 뉴스 섹션을 건너뛰고 플레이스홀더를 넣을 수 있습니다.

이 조사는 매수 판단의 질적 근거입니다. 트리거 재점수에 직접 숫자로 합산되는 것은 거시 주도 업종, RS, extension 등 별도 입력입니다.

## 10. 신규 진입 게이트

보고서 생성 뒤 추적 에이전트가 보유 종목 매도를 먼저 처리하고 신규 진입을 평가합니다.

공통 핵심:

- LLM 판단이 `Enter`
- 매수 점수가 최소 기준 이상
- 섹터/포트폴리오 제한 통과
- live 재진입 쿨다운 위반 없음
- 미체결·중복·주문 상태 정합성

KR은 시나리오의 `buy_score`, US는 저널 조정까지 반영한 `adjusted_score`를 결정론적 게이트에 사용합니다.

### 포트폴리오 기본 제한

- 최대 10개 보유 행/슬롯
- 동일 섹터 최대 3개
- 보유 4개 이상일 때 섹터 비중 30% 제한

`REGIME_MIN_SCORE_FLOOR`는 기본 비활성입니다. 활성 시 기본 바닥점수는 `strong_bear=9`, `sideways/moderate_bear=8`, 강세 체제는 추가 바닥 없음입니다. 단 레짐이 `sideways`여도 독립적인 `MARKET_PULSE=UPTREND`가 확인되면 상승 전환 지연을 보완하기 위해 바닥점수를 7로 낮춥니다. 이때 AI가 명시적으로 진입을 선택했고 원래 문턱이 6 이하인 정확히 6점 신호는 `[REGIME_REBOUND_PILOT]`로 기록하며 설정 주문액의 50%만 진입합니다. Pulse 조회 실패, AI `Skip`, 약세 레짐에는 이 예외를 적용하지 않습니다.

## 11. 피라미딩

동일 종목 추가 진입은 별도 행으로 관리되며 다음을 모두 요구합니다.

- 체제: `strong_bull` 또는 `parabolic`
- 기존 동일 종목 행 전체의 단순 평균 수익률 `>=5%`
- 기존 행 수 `<3`
- 정상 매수 판단·점수 게이트 통과
- post-FTD pilot 동결 상태가 아님

KR과 US의 세부 섹터 처리에는 차이가 있습니다. “수익 중·강세장·업종 한도”라는 그림만으로 실제 허용 조건을 대체하면 안 됩니다.

## 12. 매도와 독립 보호 루프

배치 매도는 LLM 판단과 O'Neil 폴백을 사용합니다. 결정론적 핵심 규칙:

| 규칙 | 기본값 |
|---|---|
| 시나리오 손절 | 손절선보다 추가 0.5% wick buffer |
| 절대 손절 | -7% |
| 손실 중 MA50 이탈 | 0.5% buffer |
| 트레일링 활성 | 고점 수익 +5% 이후 |
| 강세 live 트레일링 폭 | -8% |
| 약세 live 트레일링 폭 | -5% |
| stale 데이터 폭 | -10% |
| 목표가 청산 | 약세 체제에서만 |

별도 프로세스:

- `tools/hardstop_seller.py`: tier-1 위험만 평가, 기본 shadow/live off, 피라미딩 제외
- `tools/trend_exit_seller.py`: MA50·트레일링·목표가, 기본 2회 연속 일봉 이탈 또는 close-window 확인, 기본 shadow/live off, 피라미딩 제외
- 미체결 주문 감시: DB와 브로커 상태를 조정

피라미딩된 종목의 부분 청산은 배치 경로가 행 단위 수량을 계산합니다. US는 DB·브로커 정합성을 위해 큐잉된 부분 청산을 전체 청산으로 승격할 수 있습니다.

## 13. 실행 방법

```bash
# KR 오전/오후
python3 trigger_batch.py morning INFO --output trigger_results.json
python3 trigger_batch.py afternoon INFO --output trigger_results.json

# 전체 KR 안전 실행
python3 stock_analysis_orchestrator.py --mode morning --no-telegram

# 전체 US 안전 실행
python3 prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

실제 주문은 별도 KIS 설정과 실계좌 플래그가 필요합니다. 문서 검증에는 demo, `--no-telegram`, shadow 기능 플래그를 사용하십시오.

## 14. 검증 포인트

| 주장 | 대표 근거 |
|---|---|
| KR 15% 상한 | `tests/test_issue_289_screening.py` |
| RS/extension 체제별 가중치 | `trigger_batch.py`, `prism-us/us_trigger_batch.py` |
| Market Pulse 전이 | `cores/market_pulse.py` |
| 배치 휴지 정책 | `cores/regime_policy.py` |
| post-FTD 1종목 pilot | `tests/test_pulse_pilot_reexposure.py` |
| 재진입 쿨다운 | `tests/test_reentry_cooldown.py` |
| 피라미딩 | `tracking/helpers.py`, `prism-us/tracking/db_schema.py` |
| 매도 폴백 | `cores/oneil_fallback.py` |

관련 시각 자료와 코드 차이 설명은 [PIPELINE_ARCHITECTURE_ko.md](PIPELINE_ARCHITECTURE_ko.md)를 참조하십시오.
