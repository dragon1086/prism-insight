# BUY 보고서 심층 근거(DART·경쟁사 비교) 연결 검토

`docs/TRADING_CHANGE_REVIEW_HARNESS.md`의 최소 검토 기록 여섯 항목이다.
플래그 `PRISM_BUY_REPORT_DEPTH_EVIDENCE`는 기본 OFF이며, 이 문서는 활성화 승인이 아니다.

## 1. 변경 유형

- 경제적 의사결정 입력 변경이다. 계층은 BUY 프롬프트 하나로 한정한다.
  스크리닝, 결정론적 게이트, 사이징, SELL 코드는 바꾸지 않는다.
- 기준선: `451de7d4` (`fix/report-remove-fact-gate-20260925`). 플래그 OFF이면 KR/US ko/en
  BUY instruction이 기준선과 바이트 단위로 같다. 기준선 트리와 비교한 sha256이 같았고,
  `tests/test_buy_report_depth_evidence.py`도 이를 검증한다.
- 플래그 ON일 때 바뀌는 내용:
  - KR: C·A 행과 F1·F2 출처에 DART 5-1(F2는 5-3 포함)을 추가한다.
  - KR·US 공통: L 행과 F4 출처에 `경쟁사 비교 분석`을 추가한다.
  - KR·US 공통: 4단계 PER 30% 저평가 기준과 단독 미진입 사유 `PER ≥ 2.5배`를 같은 방식으로 바꾼다.
    동일 기준으로 PER이 양수인 피어가 3개 이상일 때만 피어 중앙값을 비교 기준으로 쓸 수 있고,
    그렇지 않으면 2-1 기준을 쓴다.
  - EVIDENCE_RECONCILIATION에 근거 사용 범위를 정한 항목 하나를 추가한다.
- 임계값(30%, 2.5배, 부채비율 200%, ROE 5%, 매출 10%), 스키마, 점수표, 매트릭스는 그대로다.
- 코드: `prism_core/buy_report_depth_evidence.py`. 두 시장의 `create_*trading_scenario_agent`가
  에이전트 생성 시점에 이 모듈을 적용한다. 기준 문구가 정확히 한 번씩 없으면 부분 적용하지 않고
  예외를 낸다.

## 2. 가설과 반증 조건

- 가설: BUY는 PDF 전문을 받지만 현재 프롬프트가 DART 장과 경쟁사 표를 가리키지 않는다.
  그래서 이익의 질(일회성 영업외·세금 효과), 차입 만기·담보·보증·우발채무·CB 희석,
  피어 대비 위치가 F1·F2·F4 판정과 PER 상대 비교에 체계적으로 반영되지 않는다.
  이 자료를 출처로 지정하면 같은 기준 안에서 판정 근거가 정확해진다.
- 반증 조건:
  - ON에서 F1·F2·F4 또는 4단계 확인 수가 바뀐 사례의 근거를 보고서 원문과 대조해 보니
    오독이 많다.
  - 변화가 한 방향(진입만 늘거나 줄어듦)으로 치우치고, 원문 근거로 설명되지 않는다.
  - 인용 증가 외에 판정 변화가 없다. 이 경우 효과는 없고 비용만 늘어난 것이다.

## 3. 기존 경로와의 궁합

- 스크리닝(`trigger_batch.py`, `prism-us/us_trigger_batch.py`)은 보고서를 읽지 않으므로
  영향이 없다. 같은 후보가 같은 보고서로 BUY에 들어간다.
- 보고서→BUY: `pdf_to_markdown_text` 전문이 잘리지 않고 user prompt에 들어간다.
  PDF 텍스트에서는 `#`와 표의 `|`가 사라지므로 프롬프트는 절 제목으로 가리킨다.
  - `tests/test_kr_dart_pdf_buy_depth_handoff.py`는 실제 한글 PDF로 이 전달을 확인한다.
    `5-1. 실적·현금흐름·차입과 회계 판단`과 `경쟁사 비교 분석`이 codex·legacy 두 전송 경로의
    user prompt에 모두 들어간다. system prompt는 플래그에 따라서만 달라진다.
- BUY→결정론적 게이트: `cores/buy_gate.py:184-194`가 LLM의 `fundamental_check.all_passed`와
  `additional_confirmation_count`를 소비한다. F1·F2나 4단계 판정이 바뀌면 sideways·bear
  게이트 결과도 간접적으로 바뀐다. 그래서 플래그와 동일 후보 비교가 필요하다.
- 중복 제재를 막는 장치:
  - 새 통과·실패 규칙, 점수, 감점, 미진입 사유를 만들지 않는다.
  - 공시 위험 하나만으로 `severity = "high"` 리스크 이벤트가 되지 않는다.
  - 장이나 표가 없으면 NOT_IN_INPUT으로 표기할 뿐 게이트가 아니다.
- 피어 중앙값은 선택된 피어의 중앙값이지 업종 평균이 아니다. 그래서 F2의
  '업종 평균 이하' 판정에는 쓰지 않는다. 피어 중앙값을 허용한 곳은 PER 상대 기준의 양방향
  (30% 저평가, 2.5배 고평가)뿐이다.
- DART `5-1`은 투자 전략 장(`6. 투자 전략`/`6-1`)과 다른 절이라고 프롬프트에 명시했다.
- SELL은 보고서를 읽지 않는다. 보유 행과 BUY 시나리오 JSON만 사용한다
  (`stock_tracking_enhanced_agent.py:1319`, `prism-us/us_stock_tracking_agent.py:2287`).
  단, 저장된 rationale 문구가 달라지면 SELL 입력 문맥도 달라질 수 있다. 이 영향은 미확인으로 남긴다.
- US에는 DART 장이 없다. 그래서 US 변경은 경쟁사 표 관련 부분(L·F4·PER 상대 기준)으로 한정한다.
  US 경쟁사 표는 별도 PR이 대기 중이다. 병합 전에는 ON에서도 대부분 NOT_IN_INPUT이다.

## 4. 동일 후보 비교 계획 (사전 등록)

- 방법: 같은 보고서 PDF(sha256 고정)와 같은 캡처 문맥(시세·추세·국면·일지)을 격리 재생한다.
  주문은 없고, 가상 계좌와 합성 SQLite를 쓴다.
  - OFF와 ON을 각각 2회 이상 실행해 모델 잡음을 추정한다.
  - 도구: `tools/isolated_trading_case_supervisor.run_case`. 등록 `controls.report_depth`를
    false와 true로 나누고, arm_id와 case_id는 따로 둔다.
  - 결과 요약의 `system_sha256`으로 두 arm의 프롬프트가 실제로 다른지 확인한다.
- 표본: KR 최근 DART 장이 있는 보고서를 쓴다.
  - 유지돼야 할 예: DART·피어가 2-1 판정을 확인해 주는 정상 진입 후보.
  - 반례: 일회성 이익으로 흑자이거나, 만기·CB 희석 위험이 큰 후보.
  - 경계 사례: 피어가 3개 미만이거나 PER이 음수인 후보.
  - 가능하면 sideways·bear 국면 후보를 포함한다. 게이트 영향이 그 국면에서만 생긴다.
- 기록 항목: decision, F1~F4, all_passed, additional_confirmation_count, 인용 절, 게이트 결과.
  같은 arm 반복 실행 사이의 변동을 잡음 기준선으로 쓴다.
- 중단·롤백 조건: 아래 중 하나라도 해당하면 OFF를 유지한다.
  - 판정이 바뀐 사례 중 원문 대조로 오독인 비율이 20% 이상이다.
  - 진입 편향이 한 방향으로 나타나고 근거로 설명되지 않는다.
  - 새 미진입 사유나 가공 근거가 등장한다.
- 롤백: 환경 변수를 제거하면 된다. 코드 되돌림은 필요 없다.

## 5. 관측

- 각 BUY 판단마다 `[BUY_REPORT_DEPTH] enabled=true|false ticker=...` 한 줄을 남긴다
  (`stock_tracking_agent.py`, `prism-us/us_stock_tracking_agent.py`).
  값은 환경 변수가 아니라 실제 사용된 instruction에서 판정한다.
- ON 이후 성과가 나쁠 때는 원인을 나눠서 본다. 같은 기간 OFF 재생 결과, 국면 변화,
  보고서 생성 결측(DART·경쟁사 장 없음)을 구분한다.

## 6. 현재 결론

- 사전 등록 비교 단계다. 플래그는 기본 OFF이고 SHADOW/LIVE 활성화는 하지 않았다.
- 활성화는 위 동일 후보 비교 결과를 받은 뒤 기존 승인 절차로 결정한다. LIVE는 보류 중이다.
