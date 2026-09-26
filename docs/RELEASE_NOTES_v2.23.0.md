# PRISM-INSIGHT v2.23.0 — DART 심층분석 · 업종별 보고서 · 읽기 쉬운 리포트

> **발행일**: 2026-09-27
> **범위**: `v2.22.0` (`60a0c8cd`) → `62dd9414` (PR #797까지) · 제품 변경 커밋 **226개** / 병합 PR **61개**
> **규모**: 파일 **357개**, **+47,369 / −1,850줄** · 2026-09-15–2026-09-27
> **집계 기준**: 릴리즈 문서·감사 자료 작성 커밋은 위 제품 변경 통계에서 제외합니다. 새 태그에는 이 릴리즈 문서도 포함됩니다.

## 한눈에 보기

이전 정식 릴리즈는 **v2.22.0, 2026-09-15(KST)**입니다. 이번 버전은 지난 12일의 변경 226개를
오래된 순서부터 한 번씩 검토해 작업 단위로 묶었습니다. 가장 큰 변화는 **한국 종목 보고서**입니다.
공시(DART) 원문을 근거로 한 새 장이 생겼고, 은행·지주·건설·바이오·리츠처럼 업종마다 다른
잣대로 읽도록 바뀌었으며, 처음 읽는 분도 이해할 수 있게 요약과 용어 풀이를 다시 썼습니다.

- **보고서**: KR 보고서에 **5장 DART 주요 재무·사업 위험 분석**을 새로 넣고, 핵심 요약을
  "이야기 먼저" 구조로 바꾸었으며, 경쟁사 비교 표·재무구조 차트·계산 근거 부록을 추가했습니다.
  보고서 생성을 중단시키던 사실 편집 단계는 없앴습니다.
- **업종별 관점**: 금융·지주·건설·수주산업·적자 바이오·리츠를 공시 업종명과 재무제표 모양으로
  판별해 각 업종에 맞는 지표로 분석합니다. 판단이 애매하면 일반 기업으로 둡니다.
- **매수 판단**: 목표가 근거가 없으면 임의 상승률(+15~30%)로 채우지 않고 미진입하며, 12개월 컨센서스를
  단기 목표로 쓰지 않습니다(KR·US 기본 적용). 매수 판단은 5장이 포함된 보고서 전문을 읽습니다.
  금융사는 부채비율 대신 **규제 자본비율**로 재무 건전성을 봅니다(운영 서버 적용, 관측 중).
  5장·경쟁사 표를 체크리스트 근거로 인용하게 하는 별도 규칙은 코드만 들어갔고 **꺼져 있습니다**.
- **US 보고서**: 공식 실적·공시·물가·금리 발표로 근거를 보강하고, 단위·계산을 코드로 검증하며,
  경쟁사 비교 표를 추가했습니다.
- **US 선별 대상**: 기존에는 S&P 500 + 나스닥 100 구성종목(약 518개)만 보고 시가총액은 거르지 않았습니다.
  이제는 **상장 보통주 전체**를 보고, 당일 거래대금 5천만 달러 이상 종목 중 **시가총액 10억 달러 이상**만 남깁니다.
- **데이터·운영**: 권리락 종목 하나로 KR 선별 전체가 멈추던 문제, 조용히 끝나던 US 배치, 배치에 묶여 꺼지던
  리포트 프록시를 고쳤고, US 완료 일봉 캐시와 배치 실패 알림을 추가했습니다.
- **BTC·관측**: BTC 데모 공지와 강제청산 위험 검사, API 키 만료 경고를 보강했고, 오닐식 후보와
  초분할을 **관측 전용(SHADOW)**으로 연결했습니다. 실거래 전환은 없습니다.

## 이전 → 지금: 달라진 동작 (날짜순)

최근 보고서 개편만이 아니라 9월 15일부터의 변경을 병합 날짜 순서대로 모았습니다. 보고서의 장별 변화는
다음 절에서 더 자세히 설명합니다.

| 병합일(UTC) | 영역 | 이전 (v2.22.0) | 지금 (v2.23.0) |
|---|---|---|---|
| 09-15 | BTC 데모 체결 복구 | 저장 수량(0.089043)과 실제 체결 수량(0.089)이 달라 정확한 복구가 막혔고, 운영 경보가 공개 경로로 나갈 수 있었음 | 실제 체결 수량 기준으로 복구하고, 운영 경보는 비공개로만 보내며, 복구된 진입은 한 번만 지연 공지 (#731, #732, #734) |
| 09-15 | BTC 공지·알림 | 공개 공지에 마진 모드·레버리지·계좌 대비 비중이 빠지고 청산 레버리지가 "확인 불가", 헬스 알림은 지난 오류와 현재 오류를 구분하지 않음 | 공지에 마진 모드·레버리지·운용자금 대비 증거금 비중을 표시하고 간결하게 정리, 알림에 시각과 경과 시간 표시 (#735, #737, #738, #740) |
| 09-15 | BTC 진입 안전 | 공유 데모 진입 전 강제청산 위험 검사 없음 | 교차마진 자료와 담보 여유를 보수적으로 확인해 위험하면 진입 전에 거부 (#739) |
| 09-15 | 검토 절차 | 전략 변경 시 적합성·호환성 검토가 명문화되지 않음 | 전략 도입 적합성 게이트와 스크리닝→매수·매도 전 경로 추적을 의무화(문서) (#733, #736) |
| 09-16 | BTC API 키 | 키 만료(33004)를 일반 오류로 보고 재시도 | 만료를 따로 분류해 재시도하지 않고 재발급 안내, 전달 결과가 불명확하면 기록 (#743) |
| 09-16 | KR 선별 | 무상증자 권리락 종목 하나 때문에 KR 오전 선별 전체가 중단 | 공식 수정 행과 엄격히 대조해 계속 진행, 지원하지 않는 행사는 계속 차단 (#744) |
| 09-16 | 관측(SHADOW) | 오닐식 후보·초분할 관측 없음 | US·KR 후보 감시와 초분할 초기 진입 투영을 관측 이벤트로만 기록, 실제 매매는 그대로 (#741, #745) |
| 09-17 | 시장 근거 | 보고서·요약·매수 판단이 시장 상황을 각자 서술 | 16개 ETF 수익률·참여도·확정 매매 국면을 공통 근거로 공유(설명용, 점수 변화 없음) (#747) |
| 09-17 | 매수 목표가 (KR·US) | 목표가 근거가 없을 때 현재가 +15~30% 같은 대체값을 쓸 여지가 있었음 | 출처·날짜·기간이 맞는 목표만 쓰고, 12개월 컨센서스를 단기 목표로 쓰지 않으며, 근거가 없으면 미진입 (#747) |
| 09-17 | US 파이프라인 | 나스닥 구성 페이지 이전으로 지수 종목이 518개에서 503개로 조용히 줄었고, 한 배치에서 시장 보고서를 중복 생성했으며, 끊긴 매크로 인용이 최종 보고서에 남음 | 518개 복구, 배치당 시장 보고서 1회, 끊긴 인용 제거 (#748, #749) |
| 09-17 | 비교 자료 | 경쟁사 비교 표가 잘려 전달되기도 함 | 표 전체와 모르는 값을 그대로 보존 (#750) |
| 09-18 | 텔레그램 요약 | 요약 모델이 생성 중에 도구로 가격을 조회하고, 품질 평가가 실패하면 요약을 버렸으며, 빈 값을 NaN으로 표기 | 가격·시가총액을 미리 수집해 근거로 쓰고, 평가가 실패해도 발송하며, 빈 값은 UNKNOWN (직접 커밋 4건) |
| 09-22 | KR 선별 | 유상증자 권리락 종목 때문에 KR 오전·오후 배치가 선별 전에 중단 | 같은 방식으로 공식 수정 행 대조 (#754) |
| 09-23 | 리포트 프록시 | DB 서버 OAuth 프록시가 배치에 묶여 배치가 끝나면 종료 | 리포트 전용 상시 서비스(systemd)로 분리 (#757, #758) |
| 09-23 | US 보고서 | 장 마감 전 관측을 확정 종가로 표기하기도 했고, 계산 기록이 본문에 섞였으며, 공식 발표 근거가 부족 | 공식 실적·공시·물가·금리 근거, 코드 계산, 부록 분리, 애널리스트 수집 현황 표시 (#755, #756, #759–#762) |
| 09-23 | KR 보고서 근거 | 공시 원문을 쓰는 장이 없고, 장 시작 전 조회 시 수급이 비거나 불완전 | DART 공식 근거와 5장 도입, 장 시작 전에는 직전 완료일 수급 사용, 최신 가격이 없으면 날짜를 밝힘 (#763–#765) |
| 09-24 | US 배치 | 오후 배치가 당일 518개·전일 1개 종목 데이터로 조용히 종료, 후보 없음·리포트 실패도 알림 없음 | 데이터 커버리지 검증과 배치 상태 알림, 변경 없는 같은 종목 요약은 재발송 안 함 (#767) |
| 09-24 | US 일봉 | 조회 실패 시 그 일봉을 잃음, 라이브러리 버전이 서버와 로컬에서 다를 수 있음 | 완료된 세션만 캐시하고 누락 종목만 제한 재시도(기본 켜짐), yfinance·curl_cffi 고정 (#770, #771) |
| 09-24 | 매수 게이트 | 부호가 붙은 변동성 값(+1.2%)을 읽지 못함 | 올바르게 읽음. 이 검사는 검토 기한이 지나 현재 꺼져 있음 (#768) |
| 09-24 | KR 보고서 | 연결재무제표가 "해당사항 없음"인 회사 보고서가 실패 | 공식 근거가 있을 때 별도재무제표로 작성 (#772) |
| 09-25 | **US 선별 대상** | **S&P 500 + 나스닥 100 구성종목(약 518개)**, 시가총액은 필요할 때만 조회하고 거르지 않음 | **상장 보통주 전체**(나스닥 공개 목록의 보통주·ADR·영업형 리츠) → 당일 거래대금 5천만 달러 이상 → **시가총액 10억 달러 이상**. `US_SCREENING_UNIVERSE=major_indices`로 이전 방식 복귀 가능 (#769) |
| 09-25 | 보고서 생성 | 사실 편집 단계가 같은 뜻의 다른 표기를 새 숫자로 보고 KR `/report`를 중단(앱 서버 4건 모두 실패) | 해당 단계를 제거해 전략·요약을 한 번씩 만들고 발행 (#773, #775–#777) |
| 09-25 | 보고서 구성 | 끝에 기계식 "경쟁력 비교 근거" 목록, 요약은 섹션 결과 나열 | 2장 경쟁사 비교 표, 이야기형 핵심 요약, "겉과 속" 투자 전략, US 경쟁사 표 (#778–#781) |
| 09-25 | 4장 시장 지표 | 측정되지 않은 선택 ETF 행을 빈 값으로 나열 | "미측정 선택 지표 N개" 한 줄로 요약 (#774) |
| 09-26 | 차트·4장 | KIS 전환 뒤 일부 차트 누락, 출처가 끊기면 4장 전체를 안내문으로 교체 | 차트 복원·신설, 끊긴 문단만 제외, 5장 재무구조 차트 (#782, #784, #785) |
| 09-26 | 5장 커버리지 | 대형 금융사·지주사·같은 날 정정 공시·리츠에서 5장이 자주 빠짐 | 분할 집필과 정정 순서 확정으로 대부분 작성 (#783, #786, #787, #790) |
| 09-26 | 업종별 관점 | 모든 회사를 제조업 잣대로 서술 | 금융·지주·건설·수주산업·적자 바이오·리츠별 지표로 서술 (#788–#790) |
| 09-26 | 금융사 매수 기준 | 금융사도 "부채비율 < 200%"로 판단해 구조적으로 미달 | 규제 자본비율로 판정(운영 서버 적용), 보고서에 없으면 12개월 이내 공시 수치를 검색 (#791, #792, #797) |
| 09-26 | 5장 문체 | 공시 용어를 그대로 사용 | 전문 용어마다 괄호 풀이, 핵심 수치마다 부담·여유 한 문장 (#793, #795) |
| 09-27 | BTC 경보 | 레버리지가 이미 같을 때(110043)를 오류로 보고 오류 폭주 경보, 키 만료 사전 경고 없음 | 성공으로 처리, 만료 14일·3일 전 경고, "만료 없음" 값 처리 (#794, #796) |

## 보고서를 읽는 분께: 무엇이 어떻게 바뀌었나

한국 종목 보고서의 목차는 이제 다음과 같습니다. 굵은 글씨가 이번에 새로 생기거나 크게 바뀐 부분입니다.

> **핵심 요약** → 1. 기술적 분석 → 2. 펀더멘털 분석(**경쟁사 비교 표**) → 3. 뉴스 분석 → 4. 시장 분석
> → **5. DART 주요 재무·사업 위험 분석** → **6. 투자 전략** → **자료 기준과 주요 계산 지표(부록)**

| 보고서 위치 | 이전(v2.22.0) | 지금(v2.23.0) |
|---|---|---|
| 핵심 요약 | 섹션 결과를 모아 정리한 요약 | "한 줄 결론 → 지금 무슨 일이 → 숫자 뒤에 숨은 이야기 → 경쟁사와 비교 → 공시와 주가의 연결 → 앞으로 확인할 것" 순서의 이야기형 요약 |
| 1장 차트 | 주말·장중 요청에서 투자자별 거래량 차트 실패 | 실패 수정, 투자자 내역이 없으면 총거래량으로 대체, 장 시작 전에는 직전 완료일 수급 사용 |
| 2장 | KIS 전환 뒤 시가총액·펀더멘털 차트 누락 | 새 연간 실적 추이·펀더멘털 지표 차트(예상치는 (E) 표시), **경쟁사 비교 표**(최대 4개사와 중앙값) 추가 |
| 보고서 끝 | 기계식 "경쟁력 비교 근거" 목록 | 삭제. 필요한 비교는 2장의 표와 해설로 이동 |
| 4장 | 출처 연결이 끊기면 장 전체를 안내문으로 교체 | 끊긴 문단만 빼고 나머지는 유지, 공통 시장 근거 블록 추가 |
| **5장 (신규)** | 없음 | 공시 원문을 읽는 3개 소단원, 핵심 포인트, 쉬운 용어 풀이, 재무구조 차트 |
| 투자 전략 | 5장 | 5장이 있으면 **6장**, 첫 소제목 "핵심 투자 논리: 겉과 속" |
| 부록 | 없음 | 코드로 계산한 지표·수급·공시 확인 범위를 본문 밖에 모음 |

### 핵심 요약: 결론과 이유를 먼저

- 첫 줄에 한 문장 결론을 쓰고, **겉으로 보이는 숫자와 공시에서 드러나는 사실**을 나란히 보여 줍니다.
  예를 들어 "이익이 늘었지만 일회성 세금 효과 때문"인지, "빚이 줄었지만 전환사채가 주식으로 바뀌어
  주식 수가 늘었는지" 같은 내용입니다. (#781)
- 곧 갚을 빚처럼 부담으로 보이는 숫자는 **갚을 돈과 가진 현금을 함께 비교해** 결론을 냅니다.
  총액만 나열해 위험을 암시하지 않습니다. (#782)
- 투자 전략 장에 없는 새 가격대는 요약에서 만들지 않습니다. KR·US 공통입니다. (#781)

### 5장 DART 주요 재무·사업 위험 분석 (신규)

금융감독원 전자공시(DART)의 최근 정기보고서 재무제표와 주석을 직접 읽어 쓰는 장입니다.

- **5-1 실적·현금흐름·차입과 회계 판단 / 5-2 사업구조·지배구조와 자본변동 / 5-3 주요 약정·기업 사건과
  우발위험**으로 나누고, 소단원마다 처음에 **핵심 포인트** 2~3개를 둡니다. 금액은 억원 단위로 쓰고
  공시 링크를 붙입니다. 같은 사실을 여러 곳에서 반복하지 않도록 담당을 나눴습니다. (#765, #778)
- **쉬운 풀이**: 회계·금융·법률 용어가 처음 나오면 괄호로 한 줄 풀이를 붙입니다.
  예: "환매조건부채권(RP) 매도(일정 기간 뒤 되사기로 약속하고 채권을 파는 단기 자금 조달)".
  중요한 수치 뒤에는 그것이 회사와 주주에게 **부담인지 여유인지** 한 문장으로 설명합니다. (#793, #795)
- **재무구조 차트**: 5-1 끝에 자산·부채·자본과 부채비율 추이 차트를 넣습니다. 금융사는 부채비율 대신
  자기자본/자산 비율을 그립니다. "자산 = 부채 + 자본" 같은 검증을 통과하지 못하면 차트를 빼고 추정하지 않습니다. (#785, #788)
- **업종에 맞는 잣대**(아래 "업종별 관점" 참고)로 공시를 읽습니다. (#788–#790)
- **5장이 빠질 수도 있습니다.** 최근 550일 안에 정기공시가 없거나(예: 맥쿼리인프라), 정정 공시가 여러 번 나와
  최신본을 확정하지 못하거나, 공시가 지나치게 크거나 집필 중 오류가 나면 "공시 심층 장은 이번 보고서에
  반영하지 못했습니다"라는 안내만 남기고 나머지 보고서는 그대로 발행합니다. 이 누락 자체를 매수·매도 신호로
  쓰지 않습니다. 개발 중 자주 빠지던 대형 금융사·지주사·리츠·단독 재무제표 회사 사례는 대부분 해결했습니다.
  (#772, #783, #786, #787, #790)

### 업종별 관점: 은행을 제조업 잣대로 읽지 않기

DART의 공식 업종명과 재무상태표 구조를 함께 보고 판별합니다(증권사 업종 분류는 쓰지 않습니다).
애매하면 일반 기업으로 두며, 일반 기업 보고서의 작성 지시는 이전과 같습니다. (#788–#790)

| 업종 | 무엇을 중심으로 보나 |
|---|---|
| 금융(은행·보험·증권·카드·금융지주) | 부채비율 대신 순이자마진·충당금과 자본 적정성 비율(은행 CET1·BIS, 보험 K-ICS, 증권 NCR) |
| 지주회사 | 보유 지분 가치(NAV)와 지주 할인, 지주회사 자체 수익원과 이중레버리지(빌린 돈으로 자회사에 출자한 정도) |
| 건설 | 원가율·예정원가 변경, 미청구공사, 부동산 PF 보증 |
| 수주산업(조선·방산·엔지니어링) | 계약부채(선수금)는 빚이 아니고 계약자산은 현금이 아님을 구분 |
| 적자 바이오 | 매출 배수 대신 현금 소진 기간과 파이프라인 |
| 리츠 | 실질 현금 이익(FFO)·배당 재원·임대율·차입 만기·담보 대비 대출 비율(LTV)·감정평가, 6개월 사업연도 표기 |

### 투자 전략·부록·차트

- 투자 전략은 **"핵심 투자 논리: 겉과 속"**으로 시작해 강세·약세 논리와 이미 주가에 반영된 것을 구분합니다. (#781)
- 코드로 계산한 지표, 수급 수치, 공시 확인 범위는 본문 대신 **"자료 기준과 주요 계산 지표" 부록**에 모았고,
  내부 상태 코드 대신 "확인되지 않음" 같은 문장으로 보여 줍니다. (#763, #764)
- 2장 경쟁사 비교 표는 증권정보 제공사 WiseReport가 고른 동종 기업 가운데 시가총액이 대상의 10% 미만인 회사를
  빼고(최소 2개사 유지) 최대 4개사와 중앙값을 보여 주며, 비교 문장은 코드가 계산합니다. (#779, #781)
- 4장 끝에는 시장 국면(상승·횡보·하락)과 주요 ETF 수익률 같은 **공통 시장 근거**가 붙고, 측정되지 않은 지표는 한 줄로 묶습니다. (#747, #774, #784)

### US 보고서

- 발행사 최근 실적·가이던스, 정기공시 발췌, CPI·PCE와 금리 같은 **공식 발표**로 근거를 채우고,
  확인되지 않은 항목은 그대로 밝힙니다. (#760, #781)
- SEC 금액의 단위·기간을 검증하고, 목표가 상승여력·레버리지를 코드로 계산합니다.
  장 마감 전 관측을 확정 종가로 부르지 않습니다. (#755, #756, #761, #762)
- **경쟁사 비교 표**(시가총액 3%~10배 범위의 보통주)와 "출처와 계산 근거 기록" 부록을 추가했습니다. (#759, #781)
- 애널리스트 자료의 수집 현황(부분·누락·오류)을 2장에 표시합니다. (#755)

### 텔레그램 요약·`/report`

- 텔레그램 요약은 미리 받은 가격·시가총액만 근거로 쓰고, 품질 평가가 실패해도 쓸 만한 요약은 보냅니다.
  모르는 값은 "확인되지 않음"으로 둡니다. (직접 커밋 4건, 2장 참고)
- 같은 뜻의 다른 표기(예: "84.7만주 순매도"와 "-84.7만주 순매수")를 오류로 보고 **보고서 생성을 중단시키던
  사실 편집 단계를 없앴습니다.** #772·#773에서 넣은 사실 재검토·복구 로직과 #775·#776의 표기 보정도 이때 함께
  제거했습니다. (#777, #781)
- 5장만 실패하면 보고서는 발송하고 운영자에게만 알립니다(알림 설정 시). 이 경우 결과를 캐시하지 않아 다음 요청 때 다시 시도합니다. (#783)

## 1. KR 보고서 DART 심층분석: 수집·집필·차트

- 운영 KR 보고서 경로에서 DART 공식 근거와 코드 계산 지표를 사용하도록 켰습니다. 최신 가격이 없으면 이전 가격으로
  채우지 않고 실제 날짜를 밝힙니다. (#763)
- 공시 표 값을 머리글과 정확히 묶어 옮기고, 원문 표를 그대로 렌더링한 뒤 집필하도록 해 열을 잘못
  읽는 오류를 줄였습니다. 같은 기준의 동종 기업 비교, 장 시작 전 완료 수급 사용을 함께 넣었습니다. (#765)
- 5장을 공시 해석의 단일 담당으로 정하고, 다른 장에는 공시 원문 발췌를 넘기지 않습니다.
  5장 전체 분량을 줄이고(252990 예: 20.7k → 7.2k자, 소단원당 약 2,000~3,500자) 핵심 포인트를 앞에 둡니다. (#778)
- 대형 공시는 제목 블록 단위로 나눠 쓰고, 같은 날 정정된 공시의 순서를 확정합니다. (#783, #786, #787)
- 집필 모델 기본값은 gpt-6-luna/high이며 보고서 경로에서는 astra를 쓰지 않습니다. (#777, #781)
- 부담은 상쇄 자원과 같은 시점·기준으로 맞대어 계산식과 함께 판단합니다. (#782)

## 2. 보고서 구성·정확성

- 이야기형 핵심 요약과 "겉과 속" 투자 전략을 KR·US에 적용했습니다. (#781)
- KIS 전환 뒤 빠졌던 가격·거래량, 연간 실적·펀더멘털 차트를 복원했습니다. (#782)
- 비교 표 전체를 보존하고 질문 중심의 제한 리서치를 도입했습니다. 광범위 활성화는 하지 않았습니다. (#750)
- 근거 라벨을 한국어 문장으로 바꾸고 부록 간격을 정리했습니다. (#764)
- 단독 재무제표 회사 보고서를 복구하고 종합 입력을 보완했습니다. KR/US 거시 블록은 한 번만 만들어 전략·요약이
  함께 쓰고, 텔레그램 봇은 종료 신호를 받으면 순서대로 종료합니다. 이 과정에서 넣은 사실 재검토·복구 로직과
  #773의 검토 프로토콜 재설계는 보고서를 중단시키는 원인이 되어 #777에서 런타임에서 제거했습니다. (#772, #773, #775, #776, #777)
- WiseFn 경쟁사 표를 만들고 기존 경쟁 근거 덤프를 수집 단계에서 없앴습니다. (#779)
- 텔레그램 요약은 가격·시가총액 근거를 미리 수집하고 모델 도구 호출을 없앴으며, 평가가 실패해도 발송합니다.
  보고서 기준일 가격과 최신 시가총액 관측일을 나눠 표기하고, 모르는 값은 NaN 대신 UNKNOWN으로 씁니다.
  (직접 커밋 `8e0db4fc`·`6cc17785`·`bd8df830`·`47b58e15`)

## 3. US 보고서: 공식 근거와 수치 검증

- 실제 로컬 US 파이프라인으로 검증하며 매크로 MCP 재현, 배치당 시장 보고서 1회, Nasdaq-100 구성 복구를 정리했습니다. (#748, #749)
- 애널리스트 추정치를 보존하고 수집 범위를 매수·매도 판단 입력까지 전달하며, 판단 근거 문구를 명확히 했습니다.
  비활성 TradingView 모듈은 삭제했습니다(독자가 보는 변화는 없습니다). (#755)
- 단위와 기술 지표를 코드로 계산하고, 계산 기록을 부록으로 옮기고, 공식 발표 근거를 쓰며, 완성되지 않은 봉을
  차트에서 안전하게 처리합니다. (#756, #759–#762)
- 코드로 만드는 경쟁사 표를 추가하고, 오래된 발행사 발표·영문 영수증·소수점 표기 오류를 고쳤습니다. (#781)

## 4. KR/US 공통 시장 근거

- 16개 ETF 수익률, 참여도, 확정 매매 국면을 보고서·요약·매수 판단에 **설명용**으로 전달합니다. 점수·임계값은
  바꾸지 않았습니다. (#747)
- **매수 목표가 규칙(KR·US, 기본 적용)**: 보고서 목표가는 출처·날짜·기간이 이번 매매에 맞을 때만 쓰고, 12개월
  컨센서스를 단기 목표로 쓰지 않으며, 근거가 없으면 현재가 +15~30% 같은 임의 값으로 채우지 않고 미진입합니다.
  업종 선도 가점은 산업이 정확히 일치할 때만 줍니다. 근거 없는 목표로는 진입이 계약 검증에서 거절됩니다. (#747)
- 매수 판단은 PDF에서 추출한 보고서 전문(5장·경쟁사 표 포함)을 Codex·대체 경로 모두에서 그대로 받습니다. (#765)
- 측정되지 않은 선택 지표 행을 요약 표시하고, 출처가 끊긴 문단만 제외합니다. (#774, #784)

## 5. 업종별 보고서 관점과 금융업 매수 기준

- 보고서 업종 관점을 세 단계로 넣었습니다: 금융·지주, 건설·수주산업·적자 바이오, 리츠(리츠·단독 은행·대형 지주사의
  5장 입력 복구 포함). (#788–#790)
- **금융사 F2(재무 건전성)**: 매수 체크리스트의 "부채비율 < 200%"는 예금·보험 부채가 큰 금융사가 구조적으로
  통과할 수 없어, 금융사만 공시 규제 자본비율로 판정합니다. 기준은 은행·금융지주 CET1 11% 또는 BIS 14% 이상,
  보험 K-ICS 150%, 증권 NCR 150%, 카드·캐피탈 조정자기자본비율 10% 이상입니다. (#791, #792)
- 자본비율 표가 DART 정기보고서에 있는 곳은 순수 은행뿐이라, 금융사 매수 판단에서만 기존 보완 검색(최대 1회)으로
  **기준일이 명시된 12개월 이내 실제 수치**를 찾게 했습니다. 없으면 없다고 쓰고 F2는 미달입니다. (#797)
- 활성 상태: 코드 기본값은 꺼짐, **운영 db-server는 `PRISM_KR_SECTOR_F2_MODE=live`**(2026-09-26 사용자 승인).
  비금융 기업과 업종 정보가 없는 경우의 매수 판단은 이전과 바이트 단위로 같습니다.
- 매수 체크리스트가 5장·경쟁사 표를 항목별 근거로 인용하도록 하는 규칙(`PRISM_BUY_REPORT_DEPTH_EVIDENCE`)은
  **꺼져 있습니다**. 보고서 전문은 이미 읽지만, 이 규칙은 별도 비교 검증과 승인 뒤에 켭니다. (#780)

## 6. 스크리닝·시장 데이터·배치

- 무상·유상증자 권리락 종목 때문에 KR 선별이 멈추던 문제를 공식 수정 행 대조로 고쳤습니다. (#744, #754)
- US 스냅샷 커버리지를 검증하고, KR/US 배치에서 후보 없음·리포트/PDF 실패 시 상태 알림을 보냅니다. 변경 없는
  같은 종목 요약은 다시 보내지 않습니다. (#767)
- 부호가 붙은 변동성 값을 매수 게이트가 읽도록 고쳤습니다. 이 검사의 모드와 임계값은 바꾸지 않았으며, 코드상
  검토 기한(9/18)이 지나 현재 운영에서는 꺼져 있습니다. (#768)
- US 선별 대상을 기존 **S&P 500 + 나스닥 100 구성종목(약 518개, 시가총액은 거르지 않음)**에서 **상장 보통주 전체**로
  넓히고, 당일 거래대금 5천만 달러 이상 종목만 조회해 **시가총액 10억 달러 이상**만 남깁니다(기본 켜짐,
  `US_SCREENING_UNIVERSE=major_indices`로 이전 방식 복귀). 선별 품질 기록은 꺼져 있습니다. (#769)
- yfinance·curl_cffi 버전을 고정하고, 완료된 US 일봉을 캐시하며(기본 켜짐) 누락 종목만 제한적으로 재시도합니다. (#770, #771)

## 7. BTC 데모: 공지·안전장치·경보

모두 **Bybit 데모** 범위이며 실자금·전략·위험 예산은 바꾸지 않았습니다.

- 9/15 스윙 진입의 체결 수량 차이를 복구하고, 운영 경보는 비공개로 보내며, 복구된 진입은 한 번만 지연 공지합니다. (#731, #732, #734)
- 헬스 알림에 시각과 과거·현재 오류 구분을 넣었습니다. (#735)
- 매매 공지에 마진 모드·레버리지·운용자금 대비 증거금 비중을 복원하고 공지를 간결하게 정리했습니다. (#737, #738, #740)
- 공유 데모 진입 전 강제청산 위험을 보수적으로 검사합니다. (#739)
- API 키 만료를 따로 분류하고, 레버리지 무변경 응답의 오경보를 없앴으며, 만료 14일·3일 전에 경고하고, 만료 없음
  값을 올바르게 처리합니다. (#743, #794, #796)

## 8. SHADOW 관측: 오닐식 후보 × 초분할

- US·KR 오닐식 후보 감시와 초분할 초기 진입 투영을 **관측 이벤트로만** 기록합니다. 기존 선정·매수·매도·
  사이징·주문은 바뀌지 않으며, 자동 LIVE 승격은 금지돼 있습니다. 표본은 아직 부족합니다. (#741, #745)

## 9. 운영·거버넌스

- 리포트 전용 OAuth 프록시를 배치 수명과 분리해 db-server의 systemd 서비스로 운영합니다. (#757, #758)
- 전략 도입 적합성 게이트와 스크리닝↔매매 에이전트 호환성 추적을 검토 절차에 의무화했습니다(문서). (#733, #736)
- 격리 작업 폴더를 Git 추적에서 제외했습니다(직접 커밋 `f2f8312f`).

## 개발자용 상세 — 동일 가중치 커밋 집계

`v2.22.0..62dd9414`의 **226개 커밋을 모두 오래된 순서부터 확인**했습니다.
각 커밋은 1표이며 날짜·최근성·변경 줄 수·작성자·PR 크기에 추가 가중치를 주지 않았습니다.
비병합 커밋은 주된 목적 하나에만 배정하고, 병합 커밋은 별도로 집계했습니다.
통합 PR(#781)로 함께 들어온 하위 PR(#777–#780)의 커밋은 하위 PR의 주제로 분류했습니다.
PR이 없는 직접 커밋 **5개**도 포함했습니다. 한 PR의 커밋이 성격에 따라 여러 주제로 나뉜 경우(#765·#772·#781·#782)가
있습니다. 커밋 수가 중요도·완성도·수익성 점수라는 뜻은 아닙니다.

<details>
<summary>226개 커밋의 주제별 집계 펼치기</summary>

| 작업 묶음 | 커밋 | 비율 |
|---|---:|---:|
| KR 보고서 DART 심층분석(5장) 수집·집필·차트 (`report_kr_dart`) | 29 | 12.8% |
| 보고서 구성·정확성(요약·전략·경쟁사 표·차트·사실 검증·텔레그램 요약) (`report_quality`) | 45 | 19.9% |
| US 보고서 공식 근거·수치·피어 표 (`report_us`) | 19 | 8.4% |
| KR/US 공통 시장 근거 (`market_evidence`) | 9 | 4.0% |
| 업종별 보고서 관점·금융업 매수 기준 (`sector_aware`) | 7 | 3.1% |
| 스크리닝·시장 데이터·배치 상태 (`screening_data`) | 16 | 7.1% |
| BTC 데모 공지·안전장치·경보 (`btc`) | 18 | 8.0% |
| SHADOW 관측(오닐 후보·초분할) (`shadow_observability`) | 6 | 2.7% |
| 운영(리포트 프록시)·거버넌스 문서 (`ops_governance`) | 7 | 3.1% |
| 병합 커밋 (`merge`) — PR 병합 61개 + 동기화 병합 9개 | 70 | 31.0% |
| **합계** | **226** | 100% |

</details>

<details>
<summary>오래된 작업 누락 점검: 기간별 집계</summary>

| 작성일 구간 (KST) | 비병합 | 병합 | 합계 |
|---|---:|---:|---:|
| 2026-09-15 – 2026-09-16 | 24 | 15 | 39 |
| 2026-09-17 – 2026-09-22 | 24 | 6 | 30 |
| 2026-09-23 – 2026-09-24 | 60 | 16 | 76 |
| 2026-09-25 – 2026-09-27 | 48 | 33 | 81 |
| **합계** | **156** | **70** | **226** |

</details>

전체 SHA·작성일·제목·단일 분류·PR 연결과 PR별 규모는
[릴리즈 감사 자료](https://github.com/dragon1086/prism-insight/blob/v2.23.0/docs/release_audits/v2.23.0.json)에 있습니다.
PR 연결은 제목 추측이 아니라 GitHub가 기록한 병합 커밋과 병합 부모 간 Git 도달 가능성을 기준으로 확인했습니다.

## 개발자용 상세 — PR별 변경 규모

<details>
<summary>병합 PR 61개 펼치기</summary>

| PR | 제목 | 규모 |
|---|---|---|
| [#731](https://github.com/dragon1086/prism-insight/pull/731) | fix: BTC swing fill recovery and private monitoring alerts | 13 files, +543/−51 |
| [#732](https://github.com/dragon1086/prism-insight/pull/732) | docs: BTC swing incident deployment and exact recovery evidence | 2 files, +21/−0 |
| [#733](https://github.com/dragon1086/prism-insight/pull/733) | docs: strategy adoption fit gate from user-provided reading | 2 files, +72/−1 |
| [#734](https://github.com/dragon1086/prism-insight/pull/734) | fix: confirmed recovered BTC entries reach public channel | 8 files, +433/−11 |
| [#735](https://github.com/dragon1086/prism-insight/pull/735) | fix: timestamp BTC health alerts and distinguish historical errors | 3 files, +85/−10 |
| [#736](https://github.com/dragon1086/prism-insight/pull/736) | docs: mandatory screening↔trading-agent compatibility review | 2 files, +45/−2 |
| [#737](https://github.com/dragon1086/prism-insight/pull/737) | fix(btc): 매매 공지의 마진·레버리지·계좌 비중 상세 복원 | 15 files, +705/−121 |
| [#738](https://github.com/dragon1086/prism-insight/pull/738) | fix(btc): 운용자금 대비 증거금 사용 비중을 우선 표시 | 6 files, +62/−7 |
| [#739](https://github.com/dragon1086/prism-insight/pull/739) | fix(btc): 공유 데모 진입의 강제청산 위험 검사 보강 | 9 files, +431/−9 |
| [#740](https://github.com/dragon1086/prism-insight/pull/740) | fix(btc): 공개 매매 공지 간소화 및 청산 레버리지 누락 교정 | 13 files, +387/−347 |
| [#741](https://github.com/dragon1086/prism-insight/pull/741) | feat(shadow): 오닐식 후보 감시와 초분할 초기 투영 연계 | 19 files, +1,260/−9 |
| [#743](https://github.com/dragon1086/prism-insight/pull/743) | fix(ops): BTC API 만료 진단과 Telegram 전달 미확정 기록 보강 | 12 files, +402/−12 |
| [#744](https://github.com/dragon1086/prism-insight/pull/744) | fix(kr): 무상증자 권리락으로 중단된 오전 KIS 선별 복구 | 4 files, +323/−3 |
| [#745](https://github.com/dragon1086/prism-insight/pull/745) | feat: KR/US watchlist × micro-split SHADOW evidence | 27 files, +2,118/−75 |
| [#747](https://github.com/dragon1086/prism-insight/pull/747) | Align KR/US market evidence and target provenance; validate optional source prefetch | 48 files, +3,699/−101 |
| [#748](https://github.com/dragon1086/prism-insight/pull/748) | Validate real local US pipeline and fix MCP, payload, dependency parity | 24 files, +1,407/−50 |
| [#749](https://github.com/dragon1086/prism-insight/pull/749) | Correct US validation tool-failure accounting and deployment evidence | 3 files, +32/−1 |
| [#750](https://github.com/dragon1086/prism-insight/pull/750) | fix: comparative report evidence and scoped research activation | 18 files, +782/−32 |
| [#754](https://github.com/dragon1086/prism-insight/pull/754) | Fix KIS paid-rights snapshot validation | 4 files, +107/−17 |
| [#755](https://github.com/dragon1086/prism-insight/pull/755) | Preserve US analyst evidence and retire TradingView | 20 files, +866/−551 |
| [#756](https://github.com/dragon1086/prism-insight/pull/756) | Fix verified US report numeric and evidence consistency errors | 18 files, +960/−187 |
| [#757](https://github.com/dragon1086/prism-insight/pull/757) | Keep report OAuth proxy independent of batch lifetime | 5 files, +140/−3 |
| [#758](https://github.com/dragon1086/prism-insight/pull/758) | Match report proxy launcher to DB systemd execution policy | 1 files, +2/−1 |
| [#759](https://github.com/dragon1086/prism-insight/pull/759) | Keep report calculation records outside investor prose | 3 files, +17/−2 |
| [#760](https://github.com/dragon1086/prism-insight/pull/760) | Fill US report evidence gaps using bounded official public sources | 20 files, +2,415/−38 |
| [#761](https://github.com/dragon1086/prism-insight/pull/761) | Fix public report evidence follow-through and numerical consistency | 15 files, +635/−20 |
| [#762](https://github.com/dragon1086/prism-insight/pull/762) | Finish financial appendix rendering and consensus basis wording | 4 files, +19/−1 |
| [#763](https://github.com/dragon1086/prism-insight/pull/763) | Deploy Korean report data quality on the shared production pipeline | 60 files, +10,294/−15 |
| [#764](https://github.com/dragon1086/prism-insight/pull/764) | Finish reader-facing evidence labels and appendix spacing | 5 files, +203/−5 |
| [#765](https://github.com/dragon1086/prism-insight/pull/765) | Preserve substantive DART analysis and peer facts through reports and BUY | 45 files, +5,455/−21 |
| [#767](https://github.com/dragon1086/prism-insight/pull/767) | fix: restore observable KR/US batches and strict US snapshot identity | 15 files, +1,317/−193 |
| [#768](https://github.com/dragon1086/prism-insight/pull/768) | fix: preserve signed KR/US volatility facts in buy gate | 3 files, +223/−4 |
| [#769](https://github.com/dragon1086/prism-insight/pull/769) | feat: US common-stock universe and observation-only screening quality | 19 files, +1,589/−19 |
| [#770](https://github.com/dragon1086/prism-insight/pull/770) | build: pin yfinance 1.4.1 and validated curl_cffi 0.15.0 | 2 files, +14/−2 |
| [#771](https://github.com/dragon1086/prism-insight/pull/771) | fix: cache completed US daily bars and bound missing-only retries | 10 files, +913/−29 |
| [#772](https://github.com/dragon1086/prism-insight/pull/772) | fix: restore standalone-company reports and complete synthesis inputs | 27 files, +1,963/−186 |
| [#773](https://github.com/dragon1086/prism-insight/pull/773) | fix: eliminate report review protocol mismatches and capture bounded failures | 32 files, +3,152/−513 |
| [#774](https://github.com/dragon1086/prism-insight/pull/774) | fix: compact unmeasured optional market indicators in reports | 3 files, +165/−6 |
| [#775](https://github.com/dragon1086/prism-insight/pull/775) | fix: accept equivalent approximate Korean net-share notation | 2 files, +39/−4 |
| [#776](https://github.com/dragon1086/prism-insight/pull/776) | fix: recognize prefixed approximate net-share recovery fields | 2 files, +24/−16 |
| [#777](https://github.com/dragon1086/prism-insight/pull/777) | fix: remove runtime report fact-editor gate; cap DART writers at gpt-6-sol | 22 files, +167/−3,216 |
| [#778](https://github.com/dragon1086/prism-insight/pull/778) | feat: slim KR DART chapter and make it the single owner of filing interpretation | 5 files, +152/−20 |
| [#779](https://github.com/dragon1086/prism-insight/pull/779) | feat: deterministic KR competitor table from WiseReport peers; drop Competitive Evidence dump | 20 files, +2,717/−798 |
| [#780](https://github.com/dragon1086/prism-insight/pull/780) | feat: let BUY agents cite DART chapter and competitor table as evidence (flag, default off) | 11 files, +512/−3 |
| [#781](https://github.com/dragon1086/prism-insight/pull/781) | release: KR/US report overhaul — remove fact-editor gate, DART slim, deterministic peer tables, lay-reader summary | 73 files, +5,113/−4,316 |
| [#782](https://github.com/dragon1086/prism-insight/pull/782) | fix: restore KR report charts and net DART burdens against offsetting resources | 10 files, +1,050/−13 |
| [#783](https://github.com/dragon1086/prism-insight/pull/783) | fix: keep KR DART depth chapter available for large filers | 11 files, +434/−73 |
| [#784](https://github.com/dragon1086/prism-insight/pull/784) | fix: drop only unlinked market paragraphs instead of the whole section | 2 files, +84/−19 |
| [#785](https://github.com/dragon1086/prism-insight/pull/785) | feat: DART balance-sheet structure chart in filing subsection 5-1 | 4 files, +314/−1 |
| [#786](https://github.com/dragon1086/prism-insight/pull/786) | fix: split one oversized DART filing at heading blocks (bank reports) | 3 files, +224/−65 |
| [#787](https://github.com/dragon1086/prism-insight/pull/787) | fix: resolve same-day DART corrections when list and receipt order agree | 4 files, +43/−4 |
| [#788](https://github.com/dragon1086/prism-insight/pull/788) | feat: sector-aware KR reports for financial institutions and holding companies | 14 files, +419/−36 |
| [#789](https://github.com/dragon1086/prism-insight/pull/789) | feat: sector-aware KR reports for construction, contract-based and loss-making biotech | 4 files, +338/−26 |
| [#790](https://github.com/dragon1086/prism-insight/pull/790) | fix: recover KR DART chapter inputs for REITs, standalone banks and large holding companies (sector roadmap stage 3) | 11 files, +222/−22 |
| [#791](https://github.com/dragon1086/prism-insight/pull/791) | feat: 업종별 로드맵 4단계 — 금융업 F2를 규제 자본비율로 판정 (기본 off) | 7 files, +355/−14 |
| [#792](https://github.com/dragon1086/prism-insight/pull/792) | feat: 금융업 F2 live 전환 준비 — 카드·캐피탈 기준 10% | 3 files, +11/−8 |
| [#793](https://github.com/dragon1086/prism-insight/pull/793) | feat: DART 심층분석 장에서 어려운 공시 용어를 쉽게 풀이 | 2 files, +28/−0 |
| [#794](https://github.com/dragon1086/prism-insight/pull/794) | fix: stop false BTC error-burst alerts on leverage no-op and warn before API keys expire | 8 files, +266/−10 |
| [#795](https://github.com/dragon1086/prism-insight/pull/795) | feat: DART 5장 용어 풀이 범위 확대 (예시 밖 전문 용어까지) | 2 files, +20/−10 |
| [#796](https://github.com/dragon1086/prism-insight/pull/796) | fix: treat Bybit's epoch expiredAt as no expiry in the key expiry check | 2 files, +5/−1 |
| [#797](https://github.com/dragon1086/prism-insight/pull/797) | feat: 금융사 BUY 판단에서 자본비율을 Perplexity로 보완 조회 | 3 files, +45/−12 |

</details>

## 검증

- 감사 자료의 커밋 집합을 `git rev-list v2.22.0..62dd9414`와 대조해 226개 전부가 한 번씩, 하나의 분류로만
  들어갔는지 확인했습니다. 병합 PR 61개의 병합 커밋 SHA를 GitHub 기록과 대조했습니다.
- 금융사 매수 기준 변경(#791·#792·#797)은 주문 없는 격리 테스트로 일반 기업의 매수 지시문·요청문이 main과 같음을
  해시로 확인했고, 각 PR은 정확한 head의 CI 통과 후 병합해 db·app 서버에 `62dd9414`까지 배포했습니다.
- `/report 003530`(한화투자증권, 2026-09-27)으로 금융사 판별, 금융업 관점 서술, 5장 생성과 용어 풀이(#793 기준)를
  확인했습니다. #795의 확대된 풀이와 새 형식의 **KR 정기 배치 보고서는 아직 관측하지 않았습니다**
  (추석 연휴 뒤 첫 배치는 2026-09-28 09:30).
- 테스트·스모크는 실제 주문이나 정기 배치의 성공을 뜻하지 않으며, 관측(SHADOW) 결과는 수익성의 증명이 아닙니다.

## 업데이트 방법

```bash
git status --short --branch
git fetch origin --tags
git show --no-patch --oneline v2.23.0
```

- 실제 배포는 `docs/SERVER_GIT_OPERATIONS_ko.md`에 따라 clean 대상에 확인한 commit을 fast-forward하고
  변경 범위별 검증을 수행합니다. 이 명령 예시는 자동 업그레이드 스크립트가 아닙니다.
- 금융사 F2 새 기준은 `.env`에 `PRISM_KR_SECTOR_F2_MODE=live`가 있어야 적용됩니다(코드 기본값은 꺼짐).
  되돌리려면 `off`로 바꿉니다. `PRISM_BUY_REPORT_DEPTH_EVIDENCE`는 별도 승인 전까지 켜지 않습니다.
- US 선별 대상을 이전 지수 기준으로 유지하려면 `US_SCREENING_UNIVERSE=major_indices`를 설정합니다.
- 5장 수집은 DART 공개 화면을 조회하므로 같은 IP에서 반복 대량 조회를 피하십시오.

## 참고 사항과 알려진 한계

- **태그에 코드가 포함됨, 기능이 기본 활성임, 운영 검증 완료, 수익성 입증은 서로 다릅니다.**
- 5장은 공시 근거가 불확실하면 빠질 수 있고, 모델이 쓴 글이므로 원문 링크로 확인하는 것이 좋습니다.
- 금융사 자본비율은 DART에서는 순수 은행만 확인되며, 그 밖의 금융사는 검색 결과에 의존합니다.
  기준일이 없거나 12개월보다 오래된 값은 쓰지 않으므로, 찾지 못하면 이전과 같이 F2 미달입니다.
- 금융사 새 기준의 효과는 표본이 적어(월 1~2건 수준) 수개월 관측이 필요합니다. 새 기준으로만 통과한 진입의
  손절 비율이 나쁘면 `off`로 되돌리는 조건을 미리 정해 두었습니다(`docs/SECTOR_AWARE_ROADMAP_ko.md` 4단계).
- US 시가총액 10억 달러 선별은 유동성 기준이며 종목의 질을 보장하지 않습니다.
- BTC 변경은 데모 범위이고, SHADOW 관측은 실거래나 포트폴리오 성과가 아닙니다.

## 텔레그램 공지

### 한국어

```text
🚀 PRISM-INSIGHT v2.23.0 — DART 심층분석 · 업종별 보고서 · 읽기 쉬운 리포트

9월 15일 이후 226개 커밋과 61개 PR을 날짜순으로 빠짐없이 묶었습니다. 가장 큰 변화는 한국 종목 보고서이고, 미국 선별 대상·BTC 데모·관측 기능도 함께 바뀌었습니다.

📑 보고서가 이렇게 달라졌습니다
· 새 5장 "DART 주요 재무·사업 위험 분석": 공시 원문을 직접 읽고 실적·현금흐름·차입, 지배구조·자본변동, 약정·소송·보증을 정리합니다
· 어려운 용어는 괄호로 풀어 쓰고, 중요한 숫자마다 회사에 부담인지 여유인지 한 문장으로 설명합니다
· 핵심 요약은 "한 줄 결론 → 숫자 뒤에 숨은 이야기 → 앞으로 확인할 것" 순서로 읽기 쉽게 바꿨습니다
· 2장에 경쟁사 비교 표, 5장에 재무구조 차트, 끝에 계산 근거 부록을 추가했습니다
· 투자 전략은 "겉과 속"으로 시작해 이미 주가에 반영된 것과 아닌 것을 나눕니다

🏦 업종에 맞는 잣대
· 은행·보험·증권은 부채비율 대신 자본비율, 지주사는 보유 지분 가치, 건설은 부동산 PF 보증, 조선·방산은 수주 계약, 적자 바이오는 남은 현금으로 버틸 기간, 리츠는 배당 재원을 봅니다
· 금융사의 매수 판단도 부채비율 대신 규제 자본비율로 봅니다(운영 적용, 관측 중)
· 매수 목표가는 근거가 있을 때만 쓰고, 근거가 없으면 임의로 채우지 않고 진입하지 않습니다

🇺🇸 미국 보고서
· 공식 실적·공시·물가·금리 발표로 근거를 보강하고, 계산은 코드로 검증하며, 경쟁사 비교 표를 추가했습니다

⚙️ 데이터·운영
· 증자 권리락(증자로 주가가 조정되는 날) 종목 때문에 선별이 멈추던 문제를 고쳤습니다
· 미국 선별 대상을 S&P 500 + 나스닥 100 구성종목(약 518개)에서 상장 보통주 전체로 넓히고, 시가총액 10억 달러 이상만 남깁니다
· 배치가 조용히 끝나거나 실패하면 알림을 보내고, 미국 일봉은 완료된 날만 캐시합니다
· 보고서 생성을 멈추게 하던 단계를 없애 발행이 더 안정적입니다

₿ BTC·관측
· 데모 매매 공지를 정리하고, 진입 전 강제청산 위험을 검사하며, API 키 만료를 미리 경고합니다
· 오닐식 후보 감시와 분할 진입 실험은 기록만 하고 실제 매매에는 쓰지 않습니다
※ 실자금 전환이나 자동 실거래 승격은 없습니다

릴리즈노트:
https://github.com/dragon1086/prism-insight/releases/tag/v2.23.0

가상 운용·연구 및 소프트웨어 변경 안내이며 투자 권유가 아닙니다.
```

### English

```text
🚀 PRISM-INSIGHT v2.23.0 — DART deep analysis · Sector-aware reports · Easier reading

This release groups all 226 commits and 61 PRs since September 15. The biggest change is the Korean stock report, alongside a wider US screening universe, BTC demo safety and observation-only features.

📑 What changed in the report
· New Chapter 5, "DART financial and business risk analysis": reads the latest official filings directly and covers earnings, cash flow and debt; governance and capital changes; and commitments, lawsuits and guarantees
· Technical terms get a short plain-language explanation, and key figures come with one sentence on whether they are a burden or a cushion
· The executive summary now reads as a story: one-line conclusion → what the numbers hide → what to watch next
· New peer comparison table (Chapter 2), balance-sheet structure chart (Chapter 5) and a calculation appendix
· The strategy chapter opens with "surface vs. substance", separating what the price already reflects

🏦 The right yardstick for each sector
· Capital ratios instead of debt-to-equity for banks, insurers and brokers; NAV for holding companies; PF guarantees for builders; contract balances for shipbuilding and defense; cash runway for loss-making biotech; dividend capacity for REITs
· Buy decisions for financial companies also use regulatory capital ratios instead of debt-to-equity (enabled in production; under observation)
· Buy targets are used only with supporting evidence; with none, the system does not fill in a number and skips the entry

🇺🇸 US reports
· Evidence from official earnings, filings, inflation and rate releases, code-verified calculations and a new peer comparison table

⚙️ Data and operations
· Screening no longer stalls on bonus- and rights-issue ex-dates
· US screening now covers all listed common stocks instead of only S&P 500 + NASDAQ-100 constituents (~518), keeping those with a $1B+ market cap
· Silent or failed batches now send alerts, and completed US daily bars are cached
· Removed the step that could abort report generation, so reports ship more reliably

₿ BTC and observation
· Demo trade notices, liquidation-risk checks and early API-key expiry warnings
· O'Neil-style watchlists and split-entry experiments are recorded for observation only, not traded
No real-funds rollout or automatic live promotion.

Release notes:
https://github.com/dragon1086/prism-insight/releases/tag/v2.23.0

Software, virtual-operation and research updates; not investment advice.
```
