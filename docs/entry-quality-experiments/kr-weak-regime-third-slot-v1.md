# KR 약세·횡보장 3순위 슬롯 SHADOW 사전등록

- `experiment_id`: `kr-weak-regime-third-slot-v1`
- 등록 시각: 2026-09-04 KST
- 데이터 시작점: 이 실험 코드와 운영 플래그가 db-server에 배포된 뒤 처음 완료된 KR 배치
- 시장·배치: KR, morning·afternoon
- 거래 영향: 없음

## 가설

`REGIME_WEAK_NO_TOPDOWN=true`인 `sideways` 또는 `moderate_bear`에서 현재 2종목
hard cap 때문에 탈락하는 세 번째 bottom-up 후보가 실제 선택 2종목의 평균보다 나은
단기 성과를 반복해서 보인다면, 새 트리거를 추가하기 전에 3번째 슬롯 복원을 검토할
근거가 된다.

## 후보 계약

다음 조건을 모두 만족할 때만 실험 1건을 기록한다.

1. `REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED=true`
2. `REGIME_WEAK_NO_TOPDOWN=true`
3. 계산 레짐이 `sideways|moderate_bear`
4. 실제 선택 계획이 top-down 0, bottom-up 2, 총 2종목
5. 서로 다른 적격 후보가 최소 3종목 존재

순위는 현행 bottom-up 선택과 같은 결정론적 순서로 고정한다.

1. trigger 등록 순서대로 각 trigger의 최고점 미선택 종목 1개
2. 세 자리를 채우지 못하면 전체 잔여 후보를 최종 점수 내림차순으로 보충
3. 같은 종목이 여러 trigger에 있어도 최초 1회만 인정

1·2순위는 실제 분석 대상으로 유지하고, 3순위는 SHADOW에만 기록한다. 3순위에 대해
추가 보고서, LLM 호출, 매매판단, watchlist 저장, 주문, 보유, 메시지를 만들지 않는다.

## 고정 입력과 outcome

- 기준가격: 해당 배치 screening snapshot의 `Close`
- outcome: 기준 거래일 다음부터 정확히 1·3·5·10번째 KRX 거래일의 종가수익률
- 경로 위험: 각 horizon까지의 고가·저가로 MFE·MAE 계산
- 실제 선택 1·2순위와 가상 3순위를 동일한 `experiment_ref`로 연결
- 이벤트: `screening.third_slot_shadow_evaluated`,
  `screening.third_slot_shadow_outcome`
- outcome 수집: 평일 16:10 KST 독립 추적기. exact KRX 거래일을 사후 조회하므로
  실행일이 늦어져도 horizon 날짜를 현재가로 대체하지 않음
- 후보 성과일 뿐이며 실제 체결·계좌수익·수수료 포함 성과로 해석하지 않음

## 평가 지표

Primary:

- 실험별 5거래일 `3순위 수익률 - 실제 1·2순위 평균 수익률`의 중앙값

Secondary:

- 같은 방식의 10거래일 중앙 초과수익
- 3순위 10거래일 MAE와 실제 1·2순위 평균 MAE의 차이

항상 3순위 절대 중앙수익률·상승 비율, 레짐별·배치별 분포, 최고 winner 한 건 제거
전후 방향을 함께 보고한다.

## 최소 표본과 판정

슬롯 복원 검토에는 다음이 모두 필요하다.

- prospective KR 거래일 20일 이상
- 성숙한 10거래일 실험 30건 이상
- 5거래일 중앙 초과수익 `>= +1.0%p`
- 3순위 10거래일 중앙수익률 `> 0%`
- 3순위 10거래일 MAE가 실제 선택 평균보다 `2.0%p` 넘게 악화되지 않음
- 최고 winner 한 건을 제거해도 5거래일 중앙 초과수익 방향이 양수
- 이벤트 ID 중복, 미래정보 누수, 입력가격 결측, 매매·메시지 영향 0

기준 미달이면 2종목 cap을 유지한다. threshold, 레짐 범위, 순위 방법을 바꾸면 새
실험 버전과 새 prospective 시작점이 필요하다. 어떤 결과도 자동으로 LIVE에 반영하지
않으며 슬롯 변경에는 별도 검토와 사용자 승인이 필요하다.

## 다중가설 family와 중단 조건

- family: weak-regime slot-count·top-down suppression 실험
- 현재 trial 수: 1
- hard anomaly: 실제 선택 종목·순서 변화, 추가 분석/LLM/주문/메시지, 원문 계좌·비밀값
  노출, 같은 배치 중복 이벤트
- hard anomaly가 하나라도 발생하면 즉시 플래그를 OFF하고 원인을 수정한다.
