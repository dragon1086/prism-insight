# 미국 스크리닝 적합성: 1차 구현 기록

## 범위

시총 필터를 포함한 앞선 개별주 확대 구현을 보존하고, 독립적인 ATR 전달 오류 수정 및 추세 안정성 관측 전달을 구현했다. 새로운 선별 점수, 손절/청산 정책, 시총 운영값, SHADOW/LIVE 활성화는 이번 변경에 포함하지 않는다.

## 독립 오류 수정

`cores/buy_gate.py`가 KR/US 생산자가 만드는 `ATR20=+3.2% / ADR20=+4.1%`를 해독하도록 수정했다. 기존 unsigned/단위 없는 숫자 형식은 보존하고 음수·NaN/Inf·불완전 숫자·잘못된 단위 접두부는 거부한다. lifecycle과 손절 상한을 변경하지 않았다.

- 수정 전 신규 회귀: 22 실패 / 20 통과.
- 수정 후 신규·기존 gate + 실제 KR/US producer→gate: 58 통과.
- 별도 커밋 84c39fb0, PR #768. 전시장 확대 및 quality 관측은 이 PR에 없다.
- US 생산자 시험은 공통 lifecycle 모듈을 명시적으로 연결해 모드별 파싱을 검증했다. 실제 운영의 `cores` namespace가 lifecycle을 찾는지까지 검증했다는 뜻은 아니다. 기존 import 실패의 shadow fallback은 변경하지 않았다.

## 관측 전용 구현

`US_SCREENING_QUALITY_CAPTURE_ENABLED` 기본 false. 켜도 기존 260일 이력 요청을 재사용할 뿐 새 시세 요청·LLM 호출·점수 가중치·진입 게이트를 추가하지 않는다.

- `prism_core/screening_quality.py`: 완료일 라벨의 21종가에서 signed D20와 단일 상승일 기여율을 계산한다. 당일·미래 봉 제외, 중복/역순/잘못된 종가 거절, FLAT/MISSING 분리, 입력 hash를 기록한다.
- source, 기준일, 마지막 완료 세션, 조정 기준, freshness/calendar 확인 상태를 명시한다. OK는 산술 계산 성공이며 거래일 연속성·조정주가·수익성 보증이 아니다. 조정 기준 UNCONFIRMED와 calendar UNKNOWN을 숨기지 않는다.
- `us_trigger_batch.py`: native 후보 pool의 관측을 기존 결과 JSON metadata에 저장한다. 기존 최종 후보·점수·risk_reward_ratio는 바꾸지 않는다. 현재가가 유효하지 않아 기존 함수가 즉시 반환하는 경우 등은 관측이 없을 수 있다.
- `us_stock_tracking_agent.py`: 동일 거래일·버전의 whitelist 관측만 기존 candidate.evaluated의 decision_context에 전달한다. BUY/SELL 프롬프트나 scenario에는 넣지 않는다.
- 선택 관측 로딩 실패는 기존 trigger_info_map 로딩과 분리했다. 빈/잘못된 관측이 BUY의 trigger 정보를 지우지 않는다. capture 오류도 같은 스키마의 MISSING으로 보존한다.
- 새로운 관측 서버·DB·스케줄·별도 이벤트 종류는 만들지 않는다. 탈락한 모든 후보의 장기 보존/성과 연결은 현재 JSON 저장만으로 보장되지 않으며 후속 Evidence Packet 입력 계약 검증이 필요하다.

## 검증

- quality unit/handoff + gate/실제 생산자: 81개 통과.
- US 기존·확대 batch 및 시세·종목 분류: 149개 통과.
- KR 시세 consumer·기업행동 및 KR/US full pipeline: 55개 통과.
- 실제 US tracker의 정상/MISSING/고장/비활성 관측 로딩: 4개 통과.
- 추가 on/off 비교: 관측 metadata만 제외하면 오전·오후 후보 JSON과 요청 목록이 동일하다. capture 계산 오류도 점수/후보 차이 0이다.
- Ruff E9/F63/F7/F82, 변경 runtime Python 구문 검사, git diff --check 통과. 전체 repo 타입 검사나 신규 품질 전략 성과를 입증한 것이 아니다.
- 실제 모델·계정·주문·채널 전송은 테스트 경계에서 차단했다. tracker 로딩 시험은 초기화 직전 종료하며 실제 체결까지 검증하지 않는다.

## 단순화와 남은 게이트

기존 시세·트리거·JSON·candidate event를 재사용했다. 시총은 필수 기본 적격성이고 10억/20억 USD는 아직 연구 비교안이다. 운영 금액은 승인 없이 선택하지 않았다.

전시장 실제 metadata 수집 시간·결측·캐시·원천 지연·KIS 거래 가능 종목 대조, 부분 수집 시 매매 정책, 일반 종가 청산과 장중 보호성 손절의 계약, 실제 lifecycle namespace 및 운영 모드 확인이 남는다. 새 D20 점수 프로필은 구현/활성화하지 않았다. 실제 성과 분석·replay·SHADOW·LIVE 승격도 하지 않았다.

이전 설계의 P0-B(가격 기준·SELL 역할 차이)는 정책 확인 항목이다. 기존 보호 손절을 끄거나 지연시키지 않는다. 입증된 P0-A 전달 오류 수정은 이 정책 검토와 별도 PR로 진행한다.
