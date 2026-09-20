# 필수 근거 전달: 예산 비교와 공통 UTF-8 계약 검증

## 핵심 결과

**표781·1018의 필수 G1~G3를 자르지 않고 전달하는 경로를 오프라인 실험에서 확인했습니다.** 최초 후보/순서 고정 비교에서는24,000부터 통과했습니다. 이어 정식 재무제표의 소유 섹션 오분류를 고친 별도 비교에서는 **12,000부터 packet과 명시적 실험 agent instruction에서 모두 통과**했습니다. source/topic24개 제한과 원문 gold는 유지했습니다.

이는 운영 factory·실제 모델·최종 산문·PDF·BUY/SELL 품질 통과가 아닙니다. 운영 기본값6,000과 실제 KR/US factory·orchestration·cache 호출부는 그대로입니다. 미국 확장·머지·배포는 하지 않았습니다.

## 무엇이 막고 있었는가

1. **이 두 표는 현재24개 제한 밖에 있지 않습니다.** 이전 선정 개선 후 표781은 해당 source/topic의 index0, 표1018은 index4입니다. 전체 source 합계24개라는 뜻이 아니라 source별/topic별 제한입니다.
2. **정식 재무제표가 뉴스 공간을 차지했습니다.** 최초12,000 비교에서는 재무제표 source의 표2가 같은 위험 주제 queue에서 먼저 들어가 표781 trial이12,956바이트가 되어 거절됐습니다. 원문 제목은 “연결 재무상태표”인데 계정명 “충당부채” 때문에 위험으로 분류됐습니다. 정확한 재무제표 제목에 한해 재무 분석으로 보내도록 교정했습니다.
3. **뒤쪽 전달 guard도 따로 존재했습니다.** packet과 manifest는 UTF-8 바이트를 세는데 agent injector는6,000문자를 세었습니다. 패킷만 크게 만들면 언어별로 다르게 누락되거나 manifest에는 없는 본문이 들어갈 수 있었습니다. 이 단위 불일치를 고쳤습니다.
4. **각주가 선정 분류에서 빠졌습니다.** POSCO PF 표447의 중요한 자금보충 조건은 완전한 원문 각주에 있는데 기존 HTML 후보 분류는 표 셀만 봤습니다. 끝의 괄호 부연 때문에 완결 문장을 인식하지 못하는 경우도 확인했습니다.
5. **같은 셀 텍스트가 다른 문맥을 지울 수 있었습니다.** 같은 source ID와 excerpt라는 이유만으로 기간·단위·각주가 다른 표를 중복 처리했습니다. 출처 문맥까지 같은 경우만 중복으로 보도록 수정했습니다.

## 단계별 비교

기준은 기존 삼성생명 primary source와 원래 annual fragment를 사용한 component입니다. 원문 hash와 고정 gold를 대조했으며, 일반 후보 경쟁을 제거하지 않았습니다. 아래 첫 비교는 오분류를 고치기 전의 실패 기록도 보존한 것입니다.

- **6,000:** news5,803바이트. G1/G2 실패, G3 통과.
- **12,000:** news11,171바이트. G1/G2 실패, G3 통과.
- **24,000:** news22,996바이트. G1/G2/G3 모두 통과.
- **32,000:** news31,236바이트. G1/G2/G3 모두 통과.

**정식 재무제표 라우팅 교정 후 별도 재측정:**

- 6,000: news5,803바이트. G1/G2 실패, G3 통과.
- 12,000: news11,744바이트. G1/G2/G3 모두 통과.
- 24,000: news22,892바이트. G1/G2/G3 모두 통과.
- 32,000: news31,986바이트. G1/G2/G3 모두 통과.

이 두 번째 단계는 소유 섹션을 변경한 처리이므로 순수 예산 효과와 분리했습니다. 재무제표 source를 삭제한 것이 아니라 올바른 재무 분석 섹션으로 옮겼습니다.

통과 arm에서는 표781의83셀과 표1018의14셀을 원문 그대로 보존합니다. 승소·의무 소멸, 최선 추정치의 불확실성, 별개 자회사 소송 주체·청구금액·결과 불확실성을 기간·단위·출처와 함께 확인했습니다. gold 문서는 변경하지 않았고 실제 원문은 첨부/커밋하지 않습니다.

연간 fragment를 전체 연간 주석355후보로 바꾼 별도 component도 같은 판정입니다. 이는 새 네트워크 replay나 최신성 재검증이 아닙니다. 연간 후보 전달0개 자체를 실패 기준으로 쓰지 않았고, 오래된 정보를 억지로 채우지 않았습니다.

최종12,000은 **비교한 네 값 중 이 사례가 통과한 첫 값**이지, 모든 기업에 필요한 최소값이나 운영 권고가 아닙니다. 예산이 늘면 앞선 후보의 포함 집합도 달라지므로 단순한 단독 표 크기로 전체 선정 성공을 예측하지 않습니다.

## 구현 경계와 안전성

- 신규 `prism_core/report_source_budget.py`가 기본6,000 UTF-8 바이트와 실험 허용값을 검증합니다. Boolean·실수·문자열·허용 목록 밖 값은 거절합니다.
- `packet()`, `apply_section_research()`, `build_insight_manifest()`는 명시적 keyword-only `source_budget_bytes`를 지원합니다. 실제 호출부는 바꾸지 않았으므로 운영은6,000입니다. packet/receipt/source/env가 스스로 한도를 높일 수 없습니다.
- 큰 실험 packet을 **기본 factory**에 주면 여전히 전체 생략합니다. 반면 명시적 실험 injector에 같은 허용 예산을 주면 JSON/source note를 정확히 복원합니다. recording backend의 `AgentSpec.instructions`까지 변형 없이 전달되는 회귀도 확인했습니다. 실제 모델은 호출하지 않았습니다.
- manifest 전체6,000/section1,800/앞8개 record 포인터 제한은 별도이며 유지합니다. manifest 포인터 수와 실제 주입된 본문 수를 같은 지표로 취급하지 않습니다. 기존 guidance owner 불일치와 US news 실행 조건도 별도 미검증 항목입니다.
- 잘못된 raw surrogate는 전체 note를 거절하고, ASCII JSON에서 디코딩된 잘못된 excerpt는 manifest의 해당 record만 건너뛰어 정상 sibling을 보존합니다.
- `material_filing_selection.py`는 원문 전체 각주를 같은 중립적 서술 분류에 반영합니다. 완결 문장 뒤의 닫힌 평면 괄호 부연을 인식할 뿐, 문장·조건·부연을 삭제하지 않습니다. 미완결·중첩/짝 불일치 괄호와 숫자 소수점/조건 표제를 통과시키지 않는 회귀를 고정했습니다.
- 공시 dedup은 canonical filing/provenance도 비교합니다. 기간·단위·각주·위치가 다르면 별개 근거로 남고, 정확한 중복과 dict key 삽입 순서만 다른 경우는 중복 제거합니다. False와0의 metadata 충돌도 숨기지 않습니다.
- `filing_report_evidence.py`는 material HTML의 가장 가까운 제목이 정식 재무상태표·손익/포괄손익계산서·현금흐름표·자본변동표와 정확히 일치할 때만 재무 분석 owner를 우선합니다. 하위 충당부채/우발채무 주석과 유사 제목은 기존 owner를 유지합니다. 원문 및 provenance는 재작성하지 않습니다.
- 변경된 선정 결과가 오래된 cache로 대체되지 않도록 revision은 `bounded-html-v12-statement-routing`으로 갱신했습니다. 새 의존성·원문 상한 확대·매매 규칙 변경은 없습니다.

## 남은 실패를 숨기지 않습니다

POSCO PF 표447은 각주 선정 개선으로 index28→4가 되어24개 후보 안에 들어왔습니다. 그러나 현재 순차 채우기에서는 앞선 근거와 합친 trial이34,548바이트여서32,000에서도 미전달입니다. 각주/주체/금액을 잘라서 통과시키거나32,000을 자동 증액하지 않았습니다. **다음 문제는 후보 수 자체보다 제한 안에서 큰 필수 묶음과 다른 근거를 어떻게 함께 배치하느냐입니다.**

따라서 한국 범용성 및 실제 보고서 품질은 아직 완료가 아닙니다. 이후 작업은 최대32,000의 제한을 유지한 일반적인 whole-record 배치/선정 설계, 새로운 source-first KR 검증군, 실제 모델·산문·PDF·BUY/fresh SELL 검증입니다. 한국의 해당 게이트를 모두 통과해야 미국 공식 SEC 경로로 넘어갑니다.

## 검증과 재현

- 첫 고정 비교, 공통 guard 적용, 각주 선정 변경, 문맥 dedup 변경을 별도 artifact로 보존했습니다. 각 예산 arm 안에서는 candidate/order hash가 같고6,000 반복 실행도 byte-exact였습니다. 공통 guard만 바꾼 단계에서는 모든 packet hash가 이전 단계와 같았습니다.
- 일반/연간 대체2개와 LG·카카오·HMM·POSCO4개 component, 합계6개 시나리오×4개 예산을 비교했습니다. 다른4개 업종은 원문·조건·출처 감사/스트레스 검증이며 새로운 독립 gold나 금융 품질 합격군이 아닙니다.
- 최종 연구 입력 회귀 **2,259개 통과**, 기존 경고4개입니다. 재무제표 routing은 RED8건을 먼저 재현했습니다. 별도 provider 호환 통합 **46개 통과**. 새 미국 수집/실행을 의미하지 않습니다.
- 변경 Python12개 Ruff·AST/구문·diff 검사 통과. 독립 source-boundary 검토103개, 코드 리뷰71개 및 routing 증분55개는 전체 회귀와 중복되므로 합산하지 않습니다. 최종 코드 리뷰 APPROVE입니다. Python 전용 타입 검사 통과는 주장하지 않습니다.
- 공시/모델 호출0회이며 테스트는 네트워크·broker·운영 채널 실행 없이 수행했습니다. packet note 바이트, agent instruction의 envelope/기존 지침 바이트, 모델 토큰은 서로 다릅니다. 토큰이나 실제 생성 지연은 측정하지 않았습니다.
- Git 밖 `workspace/lmg90x0p/`의 `packet_budget_before_guard_20260921.json`, `packet_budget_shared_guard_20260921.json`, `packet_budget_footnote_treatment_20260921.json`, `packet_budget_verified_20260921.json`, `packet_budget_statement_routing_20260921.json`에 단계별 결과와 코드 hash를 보존했습니다. 현행 최종 실험은 `packet_budget_comparison.py --corpus`로 재현할 수 있습니다. `--full-traces`는 raw excerpt 없이 전체 admission/source-path trace를 출력합니다.
