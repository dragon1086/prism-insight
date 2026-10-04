# PRISM-INSIGHT v2.24.0 — 초분할 실전 매수 · 주도주 보유 규칙 · 재진입 실전

<!-- RANGE_END: 3988a985 (origin/main, PR #914). README 개편 PR 병합 후 아래 범위·커밋/PR 수·규모와
     개발자용 집계, docs/release_audits/v2.24.0.json을 같은 기준으로 갱신합니다. -->
> **발행일**: 2026-10-05
> **범위**: `v2.23.0` (`69a7eb3f`) → `3988a985` (PR #914까지) · 제품 변경 커밋 **276개** / 병합 PR **91개**
> **규모**: 파일 **516개**, **+41,878 / −12,044줄** · 2026-09-28–2026-10-05 (병합 기준)
> **집계 기준**: 릴리즈 문서·감사 자료 작성 커밋은 위 제품 변경 통계에서 제외합니다. 새 태그에는 이 릴리즈 문서도 포함됩니다.

## 한눈에 보기

이전 정식 릴리즈는 **v2.23.0, 2026-09-28(KST)**입니다. 이번 버전은 지난 8일의 변경 276개를
오래된 순서부터 한 번씩 검토해 작업 단위로 묶었습니다. 지난 버전이 "보고서"였다면 이번 버전은 **매매 방식**입니다.
PRISM이 지향하는 오닐식 추세추종, 즉 "손실은 작게 자르고 크게 가는 소수 종목에 올라타 계단식으로 자산을 키운다"는
방향에 맞춰 **사는 방식(나눠 사기), 들고 가는 방식(주도주 보유), 다시 타는 방식(재진입)**을 실제 계좌에 적용했습니다.

- **초분할 매수(실전)**: 신규 진입은 1슬롯(계좌의 약 10%) 전액을 한 번에 사지 않고, 종목의 평소 변동 폭에 따라
  **1슬롯의 약 35~80%만 먼저** 삽니다. 이후 AI가 매수 때와 매일 점검 때 세운 **증액 시나리오**가 실제로 확인될 때만
  비중을 늘립니다. 초분할 진입은 시장 국면과 관계없이 **매수 점수 5점 이상**이면 들어갑니다(보유 7종목 이상이면 6점 이상).
  빠르게 가는 종목은 한 세션에 두 번까지 늘리는 **가속 증액**, 강한 트리거의 8점 이상 종목은 처음부터 한 단계 크게 사는
  **상위 셋업 가중**도 들어갔습니다(KR·US, 10/2부터 실전).
- **주도주 보유 규칙(실전)**: 매수 후 4~15거래일 사이에 종가가 최초 매수가보다 20% 이상 오르고 50일선에서 지나치게 멀지
  않으면 "주도주"로 보고, 매수 후 40거래일까지 **50일선 아래 종가나 매수가 이탈 때만** 팝니다. 손절가는 최초 매수가로
  맞춰 본전을 지킵니다. 목표가 도달·과열·촘촘한 추적 손절로 주도주를 일찍 팔던 문제를 겨냥합니다(KR·US, 10/4 배포).
- **재진입 v3(실전)**: 손절되거나 자리 때문에 보류된 종목을, 기준 가격이 살아 있는 동안 최대 60거래일·3번까지
  다시 삽니다. 한국 14:00, 미국 13:50(현지)에 판단하고, AI 재점검과 기존 매수 점검을 모두 통과해야 실제로 삽니다
  (관측 전용에서 실계좌 매수로 전환, 10/4 배포).
- **매수 판단 정확도**: 게이트에 걸린 종목은 4점 이하로 채점, 초분할에 맞는 "정찰병 진입" 관점과 프롬프트 모순 6건 정리,
  사업이 식별되면 경쟁우위 미확인만으로 탈락시키지 않는 F4 보정과 지주회사 판정, 자기자본이 음수인 미국 기업의 F2를 숫자로
  판정, 분산일로 강등된 강세장의 과도한 8점 하한 해소, 장중 거래량 2배를 충족으로 인정, 당일 장중 고가를 저항으로 쓰지 않기.
- **선별**: 트리거마다 PRISM 자체 기록으로 품질 가중치를 계산해 약한 트리거의 "1자리 보장"을 빼고, 전일 종가 아래로
  마감한 종목은 거래량 급증 트리거에서 제외합니다. 미국은 거래대금 하한을 5천만 달러로 통일하고, KIS 조건검색으로 후보를 먼저
  추린 뒤 가격을 조회하며, 시장 상태를 S&P 500과 나스닥 종합지수 둘 다로 판정합니다.
- **메시지·보고서·대시보드**: 매수·추가 매수·매도·포트폴리오 메시지와 대시보드에 **비중·평균 매수가·슬롯 기준 손익**을
  보여 주고, 누적 수익률을 실제 사용한 비중으로 계산합니다. 데이터 제공사 이름·내부 코드를 공개 출력에서 숨기고, 보류 사유에
  AI의 실제 이유를 보여 주며, 10/1 비어 있던 해외 방송 채널 번역을 복구했습니다.
- **모델·실행**: 리포트·번역 등 보조 모델은 gpt-6-luna, 텔레그램 봇 분석은 gpt-6.1-sol로 옮겼고, 운영 매수·매도 판단은
  gpt-6.1-sol로 바꿨습니다(운영 설정). 리포트와 매매 판단이 로그인 하나를 함께 쓰고, 미국 매수 판단이 시작 단계에서
  실패하던 문제를 고쳤습니다.
- **운영·보안**: 로그에 남던 텔레그램 봇 토큰을 가리고 봇 토큰 3개를 교체했습니다. 실계좌 구독자는 30분 넘은 신호를 주문하지
  않습니다. 투자 방향(North star)과 연구 교훈 장부를 검토 절차에 넣고, 주간 주도주 리포트와 2주 점검 도구를 추가했습니다.
- **BTC 데모**: 데모 메인 계좌의 신규 진입을 5분 주기 LLM 시나리오 방식으로 바꾸고, 매매 공지를 읽기 쉽게 다시 썼으며,
  정산·공지 결함을 고쳤습니다. 실자금 변경은 없습니다.
- **2주 점검 약속**: 10/2~10/4에 여러 매매 변경이 한꺼번에 실전이 되었으므로, **10/18 점검 전까지는 매매 로직을 더 바꾸지
  않고** 장애·오류 수정만 합니다. 효과는 그때 변경별로 판단합니다.

## 이전 → 지금: 달라진 동작 (날짜순)

9월 28일 v2.23.0 이후의 변경을 병합 날짜 순서대로 모았습니다. 매매 흐름 전체의 변화는 다음 절에서 다시 정리합니다.

| 병합일(UTC) | 영역 | 이전 (v2.23.0) | 지금 (v2.24.0) |
|---|---|---|---|
| 09-27 | 테스트·발행 안전 | 시그널 발행 차단 스위치를 발행 순간에는 다시 보지 않았고, 실행 스크립트가 파이썬 경로를 정하기 전에 써서 장 운영일 확인이 늘 통과 | 발행 시점에도 차단 확인, 장 운영일 확인 복구, 테스트 수집·의존성 정리(외부 기여자 @tkgo11의 커밋 7개를 저작자 그대로 반영) (#819, #821) |
| 09-27 | US 선별 수집 순서 | 확장된 미국 선별 대상이 나스닥 종목부터 나열돼 600초 수집 예산 안에 NYSE 대형주(JPM·XOM·LLY 등)가 빠짐(5,065개 중 2,095개 수집) | S&P 500·나스닥 100 구성종목을 먼저 수집 (#820) |
| 09-28 | 관측(SHADOW) 재진입 v2 | AI 재점검이 거절하면 그 종목 감시가 끝남, 재점검 입력에 차트 이미지가 그대로 들어감 | 거절 뒤에도 눌림 반등·재돌파를 계속 감시, 이미지를 빼 입력 토큰 약 1/12 (#823, #824). 10/4 v3로 대체 |
| 09-28 | 공개 출력 | KR 보류 메시지·보고서에 데이터 제공사 이름이 노출 | 공개 보고서·번역·텔레그램에서 제공사 이름과 링크를 숨김(수치·판단 불변) (#825) |
| 09-28 | US 거래대금 하한 | 대부분 트리거 1억 달러, 오후 상승 트리거에만 5천만~1억 달러 별도 1종목 | 모든 트리거와 사전 필터를 **5천만 달러**로 통일 (#826) |
| 09-28 | KR 매도 AI | AI 매도 판단이 시스템 시장 국면을 받지 못하고 스스로 강세장으로 판단해 느슨한 추적 손절 폭을 쓰기도 함 | 시스템 국면을 전달해 기존 결정론 손절 루프와 같은 폭을 사용(더 촘촘하게 만들지 않음) (#830) |
| 09-28 | KR 메시지 문체 | "MCP", "BAR_FINALITY_UNKNOWN" 같은 내부 용어와 "기준는" 같은 조사 오류 | 쉬운 한국어로 바꾸고 조사를 앞 글자에 맞춰 교정 (#831) |
| 09-28 | US 선별 수집 | 약 5,066개 종목 전부를 yfinance로 조회(약 19분) | KIS 조건검색으로 시총 10억 달러 이상·거래대금 기준 근처 종목만 먼저 추려 조회 (#827) |
| 09-28 | US 시장 상태 | S&P 500 하나로 판정해 9/15부터 "조정" 상태가 이어져 미국 오전 배치가 쉼 | S&P 500과 나스닥 종합지수가 **둘 다** 조정일 때만 조정, 하나만이면 "압박" (#833) |
| 09-28 | 관측(SHADOW) 미국 분할 | 최초 50% 후 장중 거래량 1.5배 확인 때만 증액(실제 증액이 드묾) | 변동성 기준 최초 비중(B3 규칙), 거래량 조건 제거로 바꿔 계속 관측 (#832) |
| 09-29 | 매수 거래량 신호 | 장중 배치라 당일 미완성 봉 거래량을 늘 빼서 "거래량 2배" 조건이 사실상 작동하지 않음(MDB 장중 7.4배인데 "최대 0.67배") | 장중에 이미 20일 평균의 2배를 넘으면 충족, 못 넘으면 "미확정"(부진으로 보지 않음) (#835, #836) |
| 09-29 | US 선별 메타데이터 | 메타데이터 확인이 300초 예산을 넘겨 145개 종목이 빠짐 | 거래대금 순서·7일 캐시·KIS 시가총액 사용으로 예산 안에 완료 (#834) |
| 09-29 | 문서 정리 | 오래된 릴리즈 노트(v2.10~v2.20)·임시 문서·쓰지 않는 이미지가 저장소에 남음 | 정리(태그·GitHub 릴리즈에는 그대로 남음) (#837) |
| 09-29 | KR 장중 시세 | KIS 장중 스냅샷 조회가 일시 전송 오류로 실패 | 해당 묶음만 재시도 (#838) |
| 09-29 | 목표가 저항 | 판단 시점의 당일 장중 고가를 "단기 저항"으로 써서 손익비가 0.11처럼 왜곡(HPSP) | 진행 중인 당일 봉 고가·저가를 저항·지지로 쓰지 않고, 52주 확정 최고가와 신고가 돌파 목표 조건 충족 여부를 사실로 제공 (#840) |
| 09-29 | 로그인 | 리포트와 매수·매도 판단이 OAuth 로그인을 따로 써서 계정 전환 때마다 재로그인 | 로그인 하나를 프록시로 함께 쓰고, 만료 2시간 전에 미리 갱신 (#841, #842) |
| 09-29 | US 번역 순서 | 번역 PDF 12건이 매매 판단보다 먼저 시작해 같은 사용 한도를 소진, AVGO·QURE·BE 매수 판단이 한도 초과로 실패 | 번역 PDF는 매매 단계가 끝난 뒤 시작 (#844) |
| 09-30 | US 기관 수급 | 미국은 일별 기관 순매매 자료가 없는데 판단문 72건 중 13건이 "기관 순매수 미확인"을 적음 | 미국은 해당 조건을 평가·언급하지 않는다고 명시(점수 불변) (#846) |
| 09-30 | /insight | Sonnet 5 기반 | Sonnet 5.5로 교체, 강제 도구 호출 없이 JSON 복구 (#843) |
| 09-30 | 분산일 강세장 | 분산일 때문에 한 단계 강등된 강세장에 진짜 횡보장용 8점 하한까지 붙어 사실상 매수 금지 | 강등된 국면 규칙(횡보 5점)만 적용, 미국 분산일 수는 두 지수 중 작은 값. 진짜 횡보·약세장 하한은 그대로 (#848) |
| 09-30 | 매매 판단 강도 | 추론 강도·빠른 처리 여부를 수동 설정 | 활성 OAuth 계정에 맞춰 자동 선택 (#847, #849) |
| 09-30 | 매수 모델 비교 | 두 번째 모델 비교 수단 없음 | 상한이 있는 관측용 비교 기능 추가(코드 기본 꺼짐, 운영도 꺼짐) (#850) |
| 09-30 | KR 보고서 분기 EPS | 회사 현황 작성자가 매번 다른 페이지를 골라 분기 EPS가 있다가 없다가 함 | 확정 분기 EPS 표와 전년 동기 비교를 코드로 미리 만들어 제공 (#851) |
| 09-30 | KR 수급 비교 | 수급 비교에 늘 "기업행위 미조정" 단서를 붙임 | 분할·병합 등 기업행위 여부를 실제로 확인해 없으면 "그대로 비교 가능" (#852) |
| 09-30 | KR 관측 누락 | 운영 KR 경로가 초분할·판단 입력 관측을 남기지 않음 | 운영 경로에서도 기록 (#853) |
| 09-30 | 모델 | 차트 해석 등에 쓰던 gpt-5.4-mini가 OAuth에서 거부돼 KR은 9/28, US는 9/09부터 차트 해석이 실패, 봇은 gpt-5.6-terra | 보조 모델 gpt-6-luna, 봇 분석 gpt-6.1-sol (#854). 운영 매수·매도 판단은 같은 날 서버 설정으로 gpt-6-astra → gpt-6.1-sol |
| 09-30 | 인증 경보 | 로그 파일 수정 시각만 보고 전날 오류 160건을 오늘 오류로 경보 | 각 줄의 시각으로 최근 90분만 집계 (#855) |
| 09-30 | US 매수 판단 시작 | 9/22부터 병렬 매수 판단이 시작 단계에서 30초 만에 실패해 예비 경로로 판단 | 시작 단계만 차례로 실행, 판단은 병렬 유지 (#856) |
| 10-01 | 누적 수익률 | 반 슬롯 시험 매수도 1슬롯으로 계산 | 실제 사용한 비중으로 가중한 누적 수익률(텔레그램·대시보드) (#858) |
| 10-01 | 장중 손절 공백 | 피라미딩이나 두 계좌 보유처럼 보유 행이 2개 이상인 종목은 장중 손절·추세 이탈 루프가 건너뜀 | 계좌·행 단위로 판정해 장중에도 보호, 손절 문구를 실제 실행 방식(장중 현재가 ≤ 손절가×0.995)에 맞춤 (#859) |
| 10-01 | 매매일지·방송 번역 | 모델 교체 뒤 요청 형식 오류로 매매일지가 0자로 저장되고 해외 방송 채널 번역이 빈 메시지 | 새 요청 방식으로 복구, 빈 응답은 저장·발송하지 않음. 경쟁사 PER은 지표별 유효값 3개 이상일 때만, 보류 사유에 AI 실제 이유 표시 (#860) |
| 10-01 | 오전 거래량 참고 | 없음 | 오전 판단에 "오전 누적 거래량 ≥ 전일 거래량: 예/아니오" 참고 사실 추가(거래량 신호 판정은 불변) (#861) |
| 10-01 | 메시지 코드 | 보류 사유에 `effective_score`, `moderate_bull`, 영어 점수 보정 사유가 그대로 | 한국어로 표시 (#862) |
| 10-02 | 관측(SHADOW) 초분할 | 미국 분할 관측은 셋업 자동검토를 통과한 진입만 대상(9/28 이후 0건) | 한국·미국의 **모든 실제 진입**에 같은 규칙으로 가상 기록 (#863–#866) |
| 10-02 | **초분할 실전** | 신규 진입은 1슬롯 전액 | 변동성 기준 약 35~80%를 지정가로 먼저 매수, 보유·메시지·대시보드·구독자 시그널에 비중 표시. 운영 10/2 14:50 KST 켬 (#867, #868) |
| 10-02 | 사업 경쟁력(F4)·지주사 | 경쟁우위 근거를 못 찾으면 "미확인 = 미달", 업종명이 다른 지주회사는 지주사로 판별 안 됨(솔브레인홀딩스) | 사업 모델·매출원이 식별되고 출처 있는 훼손이 없으면 통과(KR·US), 법인명으로 지주사 판별 보정, 지주사는 핵심 관계회사 기준 (#869) |
| 10-02 | **초분할 증액** | 장중 고정 사다리: 5분봉 2개 확인 후 진입가 +2%면 80%, +4%면 100% | 고정 사다리 정지 후 AI가 세운 **증액 시나리오**(2~4개, 실제 가격, 5%p 단위)로만 증액, 계획은 세션별로 보관 (#872, #875–#877) |
| 10-02 | 초분할 진입 점수 | 국면별 하한(횡보·약세 8점, 강한 약세 9점) | 초분할 계획을 세울 수 있는 신규 진입은 국면과 관계없이 5점 (#873) |
| 10-02 | 비중 기록 | 매매일지·일지 압축·주간 리포트가 모든 거래를 1슬롯으로 기록 | 비중·평균 매수가·슬롯 기준 손익 반영 (#874) |
| 10-02 | 거래량 급증 트리거 | 장중 양봉만 보고 갭하락 종목도 "상승"으로 분류(STX −11.5%) | 전일 종가 아래로 마감한 종목 제외(KR·US), 미국 종가 강도 트리거도 한국과 같은 규칙 (#878) |
| 10-02 | 실계좌 구독자 | 재시작하면 쌓여 있던 지난 신호를 옛 가격으로 순서 없이 재생할 수 있음 | 30분 넘은 신호는 주문하지 않고 경보 (#879) |
| 10-03 | 매수 채점표 | 추세 게이트로 미진입인 종목이 6~7점처럼 보이기도 함(AVGO) | 게이트에 걸린 미진입은 최대 4점·거시 가점 없음, 손익비 미달은 점수가 아니라 사유에만 (#880) |
| 10-03 | BTC 진입 방식 | 기존 메인·스윙 규칙 진입 | 데모 메인 계좌에서 5분 주기 LLM 시나리오 진입, 수량·위험·주문 검증은 코드가 담당 (#881, #883–#886, #888) |
| 10-03 | US 음수 자본 F2 | 자기자본이 음수면 부채비율을 계산할 수 없어 판정이 오락가락(DELL 8점 → 2~3점) | 3년 이익·주주환원·순부채/EBITDA·이자보상·현금흐름 네 조건을 숫자로 계산해 판정 블록 제공 (#882) |
| 10-03 | 초분할 매수 프롬프트 | "분할매매 불가·올인/올아웃" 전제와 국면 관점만 있음 | 초분할 실전 종목에만 "정찰병 진입" 관점을 붙이고, 사용자와 함께 모순 6건을 정리 (#889) |
| 10-03 | BTC 공지 | 계획·체결·보호·정산이 섞이고 같은 상태를 다시 알림 | 위험 비율·전후 비교가 있는 간결한 공지, 재공지 억제, 공개 시세 한도 대응, 경보 구분 (#890–#895) |
| 10-03 | 검토 절차·토큰 | 연구 결론이 흩어져 있고, 텔레그램 봇 토큰이 일부 로그에 평문으로 남음 | 연구 교훈 장부(#896), 로그에서 토큰 자동 가림(#897) |
| 10-04 | BTC 정산·감사 | 정상 펀딩 체결을 미확인으로 분류해 정산이 보류되고, 전량 청산 뒤 종료 공지가 빠지기도 함 | 펀딩 체결을 정확히 대조, 전량 청산을 한 건으로 안내, 읽기 전용 감사·사전등록 재생 비교 도구 (#898, #899, #901, #905) |
| 10-04 | **재진입 실전** | 손절·보류 종목 재진입은 관측 전용(가상 기록) | 재진입 v3: 한국 14:00·미국 13:50 판단, 최대 3번, AI 재점검 + 정상 매수 점검 통과 시 실계좌 매수 (#900) |
| 10-04 | 증액 계획 휴장일 | 증액 계획 날짜를 평일로만 계산해 10/5 대체공휴일로 잡힐 수 있음 | 거래소 달력(KR XKRX·US XNYS)으로 계산 (#902) |
| 10-04 | BTC 매매 공지 | 체결과 누적 보유, 계좌 손익이 한 문단에 섞임 | 체결·보유·계좌 순자산·TP/SL별 금액을 블록으로 구분 (#903) |
| 10-04 | 투자 방향 | 검토 절차에 투자 방향과 점검 지표가 명시되지 않음 | North star(오닐식 추세추종·계단식 성장)와 4대 점검 지표를 검토 절차 맨 앞에 명시 (#904) |
| 10-04 | 주간 주도주 리포트 | 없음 | 큰 수익 종목 포착·손절 비용·놓친 대박·트리거별 성적을 운영자 전용 채팅으로 매주 보고 (#906) |
| 10-04 | 최종 선발 | 모든 트리거에 최소 1자리를 보장 | 품질 가중치 0.95 이하 트리거는 1자리 보장에서 제외, 남은 자리는 점수 × 가중치 순 (#907) |
| 10-04 | 초분할 가속·가중 | 한 세션에 증액 1번, 최초 비중은 변동성만으로 결정 | 최초 진입가 +8%·거래량 1.5배면 그 세션 2번까지, 8점 이상 + 강한 트리거는 최초 +20%p(최대 80%), 위험 한도는 현재 유효 손절선 기준 (#908) |
| 10-04 | **주도주 보유** | 주도주도 목표가·과열·추적 손절·시간 점검으로 일찍 매도 | 주도주 판정 후 50일선 아래 종가·매수가 이탈 때만 매도, 손절가는 최초 매수가 (#909, #914) |
| 10-04 | BTC 공지 보완 | 간결 공지에서 현재 TP가 빠지고, 청산 뒤 계좌 순자산이 없음 | 현재 TP와 청산 뒤 확인된 순자산 표시 (#910, #911) |
| 10-04 | 2주 점검 기록 | 매도를 막은 순간의 가격, 자리를 잃은 후보, 막힌 증액이 기록되지 않음 | 변경별 근거 이벤트 보강과 점검 도구 `tools/two_week_review.py` (#912) |
| 10-04 | 토큰 재노출 | 교체한 봇 토큰이 BTC 리포터·주간 수집 로그에 다시 남음 | 해당 진입점에도 가림 적용 (#913) |

## 투자자께: 매수부터 매도·재진입까지 무엇이 달라졌나

한 종목이 선별되어 팔리고 다시 사지기까지의 흐름입니다. "실전"은 실제 계좌 주문에 쓰인다는 뜻이고,
"관측"은 주문 없이 기록만 한다는 뜻입니다.

| 단계 | 이전(v2.23.0) | 지금(v2.24.0) | 상태 |
|---|---|---|---|
| 선별 | 모든 트리거에 최소 1자리 보장, 거래량 급증은 장중 양봉만 확인 | 약한 트리거는 1자리 보장 제외, 전일 종가 아래 마감 종목 제외, 미국 5천만 달러 하한·두 지수 시장 판정 | 실전 |
| 매수 판단 | 국면별 최소 점수, 게이트 종목 점수가 들쭉날쭉 | 게이트 종목 최대 4점, 정찰병 관점, F4·지주사·미국 음수 자본 보정, 거래량 하한 인정 | 실전 |
| 첫 매수 | 1슬롯 전액 | 1슬롯의 약 35~80%(변동성 기준), 상위 셋업이면 +20%p | 실전 |
| 증액 | 없음(분할 증액은 관측 전용) | AI 증액 시나리오, 수익 중일 때만, 피라미드형, 가속 구간 세션당 2번 | 실전 |
| 보유 | 목표가·과열·추적 손절로 일찍 매도 | 주도주는 50일선·매수가 기준으로 최대 40거래일 보유, 그 뒤 20일선 | 실전 |
| 매도 | 손절·추세 이탈·AI 매도 | 같음. 단, 주도주에는 결정론 보유 가드, 다중 행 종목도 장중 손절 | 실전 |
| 재진입 | 관측 전용 v2 | v3: 최대 3번, 한국 14:00·미국 13:50, 실계좌 매수 | 실전 |

### 초분할: 처음엔 일부만, 확인되면 늘리기

- **첫 매수**: 진입가 대비 최근 14거래일 평균 변동 폭(ATR)을 보고, 손절 대용 폭(1.5×ATR, 4~10% 범위)이 넓을수록 적게,
  좁을수록 많이 삽니다. 계산상 1슬롯의 약 35~80%입니다. 지정가로 주문하며 원·센트 단위로 내림합니다. (#867)
- **최소 점수 5점**: 처음에 일부만 사므로 초분할 진입은 시장 국면과 관계없이 매수 점수 5점 이상이면 들어갑니다.
  KR·US 후보 1,636건 탐색에서 4점 이하는 대체로 손실, 5~6점은 상승기 이익·약세기 작은 손실이었습니다. 보유 7종목 이상이면
  6점 이상만 진입한다는 기존 규칙과 업종 3종목·보유 10종목 한도, 손익비·손절폭 게이트는 그대로입니다. (#873, #908)
- **증액 시나리오**: AI가 매수할 때와 매일 보유 점검 때 "어떤 가격에서, 어떤 조건이면, 비중을 몇 %까지" 늘릴지
  2~4개 시나리오를 오닐·미너비니·드러켄밀러·버핏 등 여러 투자자 관점으로 세웁니다. 코드가 장중에 그 조건만 확인해
  주문합니다. 늘리는 폭은 5%p 단위·한 번에 25%p 이하·직전 매수분 이하(피라미드형)이고, **평균 매수가보다 위일 때만**,
  트리거 가격보다 2% 넘게 달아나면 따라가지 않으며, 계획은 지정된 세션 하루만 유효합니다. 매도·손절 신호가 나온 날은 늘리지
  않습니다. (#875–#877)
- **가속 증액**: 오늘 이미 한 번 늘렸고 최초 진입가보다 8% 이상 높으며 거래량이 평소 같은 시각의 1.5배 이상이면 그 세션에
  한 번 더 늘릴 수 있습니다. 포트폴리오 재현에서 8세션에 87% 오른 종목(삼성전기)이 초분할로는 이익이 절반이 된 것을 보고
  넣었습니다. (#908)
- **상위 셋업 가중**: 매수 점수 8점 이상이고 트리거가 "일중 상승률 상위"나 "갭 상승 모멘텀 상위"이면 첫 비중을 20%p 크게
  시작합니다(최대 80%). AI가 점수를 부풀릴 유인이 생기지 않도록 이 규칙은 **AI 프롬프트에 알리지 않습니다.** (#908)
- **위험 한도**: 늘린 뒤 현재 유효 손절선(최초 손절가와 현재 손절가 중 높은 값)까지 밀려도 총손실이 처음 1슬롯을 샀을 때의
  손실 폭을 넘지 않도록 증액분을 자릅니다. 손절선이 진입가 근처로 올라간 강한 종목은 1슬롯까지 갈 수 있고, 손절선이 처음
  그대로면 대략 75~85%에서 멈춥니다. 손절·추적 손절 기준은 여전히 **최초 진입가**입니다. (#875, #908)
- **표시**: 매수 메시지에 "초분할 비중: N% (1슬롯 기준)"과 증액 시나리오 요약, 추가 매수 메시지에 비중 a%→b%·추가 매수가·
  평균 매수가·주문 상태, 매도 메시지에 비중·평균 매수가·슬롯 기준 손익, 포트폴리오 요약에 "사용 비중 x.xx/10 슬롯"이
  표시됩니다. 대시보드 보유 표에는 비중 배지와 평균가, 슬롯 기준 수익률이 붙습니다. (#867, #868, #874)

### 주도주 보유 규칙: 크게 가는 종목을 끝까지 타기

- **왜**: 실제 기록에서 진입 후 60거래일 안에 +30% 이상 오른 한국 종목의 **실현 수익 중앙값은 +2%, 상승 폭 중앙값은 +60%**
  였습니다. 목표가 도달, 과열, 20일선 이탈, 횡보·약세장 목표가 매도, 시간 점검, AI가 올린 장중 손절선이 주도주를 일찍
  팔았습니다. (#909)
- **판정**: 매수 후 처음으로 확정 종가가 최초 매수가보다 +20% 이상인 날이 **4~15거래일** 사이이고, 그 종가가 50일선의
  1.4배 이하일 때만 주도주입니다. 1~3거래일 만에 +20%를 찍은 급등형이나 50일선에서 너무 멀어진 종목은 제외하고 기존 규칙을
  따릅니다(단순 8주 규칙은 이런 급등형에서 이익을 거의 다 반납해 탈락했습니다).
- **보유 중**: 손절가를 **최초 매수가**로 맞추고(AI가 더 높게 올려 둔 값도 매수가로 바꿈), 매수 후 40거래일까지는
  50일선 아래 종가, 최초 매수가 아래 종가, 장중 매수가 이탈 때만 팝니다. 40거래일이 지나면 20일선 아래 종가가 매도 조건에
  더해집니다. 공개매수·상장폐지 같은 공식 법인 이벤트는 언제든 매도 사유입니다.
- **수익 보호선 단계**: 고점 +5% 전(0단계), +5% 이후 주도주가 아닌 종목(1단계, 기존 AI 추적 손절 그대로), 주도주(2단계),
  보유 기한 이후(3단계)로 나눕니다. 1단계의 AI 추적 손절을 종가로만 집행하는 안은 두 시장 모두 결과가 나빠(KR −41~−49%p,
  US −29~−38%p) 바꾸지 않았습니다.
- **표시**: 포트폴리오 요약에 "🏃 주도주 보유 규칙 적용: 매수 후 N거래일 +X%, 50일선 이탈 전까지 보유 (~MM/DD)",
  매도 메시지에 "🏃 주도주 보유 규칙 적용 종목"이 붙습니다.
- **주의**: 근거는 KR 7건·US 4건, 같은 자료로 규칙을 고른 사후 분석입니다. 실제 성과는 더 나쁠 수 있어 주간 리포트로
  계속 추적합니다. 배포 직후 매수 후 40거래일 안의 기존 보유 종목도 소급 판정합니다. (#909, #914)

### 재진입 v3: 손절·보류 뒤 다시 올라타기

- **대상**: 엄격한 매수 판단을 거친 뒤 손절된 종목, 자리 때문에 보류되거나 게이트에 막힌 종목입니다. 기준 가격(대개 첫
  매수 때 돌파했던 가격대)이 무너지기 전까지 **최대 60거래일, 최대 3번** 다시 삽니다. 작은 손절 여러 번을 감수하고 큰 추세
  한 번을 노리는 방식입니다. (#900)
- **신호 세 가지**: 기준 가격 아래로 내려갔다가 다시 올라서는 **재돌파**, 기준 가격까지 눌렸다가 버티는 **눌림 지지**,
  기준 가격 아래로 크게 흔든 뒤 5거래일 안에 거래량을 동반해 회복하는 **흔들기 후 회복**입니다.
- **판단 시각**: 한국 14:00, 미국 13:50(현지, 한국 시간 새벽 2:50 무렵). 오후 정기 배치와 한국 종가 동시호가보다 앞섭니다.
  점심 무렵 판단은 종가 판단과 신호 일치율이 한국 65%, 미국 50%뿐이어서 마감에 가깝게 옮겼습니다.
- **안전장치**: AI 재점검이 진입을 승인하고, 정상 신규 진입과 같은 점검(보유 슬롯, 업종 한도, 최소 점수, 최종 매수 게이트,
  재매수 쿨다운, 초분할 계획)을 통과해야 삽니다. 시장당 하루 2건, 같은 종목 하루 1번, 주문 마감(한국 14:40, 미국 14:25)이
  있고, 초분할 계획을 못 세우면 1슬롯 전체를 사지 않고 멈춥니다. 손절은 시장 상황별 최대 손절폭(−7/−6/−5%) 이내입니다.
  한국은 기본 계좌, 미국은 설정된 계좌마다 실행합니다.
- **메시지**: 매수 메시지에 "🔁 재진입 매수 (기준 가격 눌림 지지 매수, 1/3번째 시도)"와 이전에 손절·보류했던 종목이라는
  설명이 붙습니다. 재진입이 아닌 매수 메시지는 그대로입니다.
- **근거와 한계**: 사전에 정한 통과 기준으로는 **FAIL**이었고(한국 눌림 지지 +3.7%, 미국 재돌파 +1.6%가 최선), 흔들기 후
  회복만 두 시장 모두 평균이 나았습니다. 포트폴리오 재현과 사용자 판단으로 관측 단계를 건너뛰고 실전으로 갔으며, 운영하면서
  보완합니다. 이전 v2 관측은 은퇴했습니다.

## 1. 초분할 실전 매수 (KR·US)

- 미국 분할 관측을 변동성 기준 최초 비중·넓은 증액 구간·거래량 조건 제거(B3 규칙)로 바꾸고, 한국 운영 경로가 초분할
  관측을 남기지 않던 문제를 고쳤습니다. (#832, #853)
- 같은 규칙을 한국·미국 **모든 실제 진입**에 가상으로 적용하는 관측(B3 전 진입)을 열었습니다. 한국은 KIS 1분봉을 5분봉으로
  묶어 씁니다. 운영 점검에서 결정 시각 순서 오류와 서비스 실행 경로 문제를 찾아 고쳤습니다. (#863–#866)
- 초분할 실전 코드를 기본 꺼짐으로 넣고, 주문 없는 점검 뒤 사용자 승인으로 **10/2 14:50 KST에 KR·US를 함께 켰습니다.**
  미국 한국어 요약의 달러 표기와 80% 진입 문구도 바로잡았습니다. (#867, #868)
- 같은 날 저녁 "장중 고정 사다리는 오닐식 긴 호흡에 비해 성급하다"는 판단으로 고정 사다리 증액을 멈추고, AI 증액 시나리오로
  대체했습니다. 별도 관측 단계 없이 초분할 실전의 일부로 켜졌습니다. 관점 이름 오타로 시나리오가 버려지던 문제와, 다음 세션
  계획이 오늘 계획을 덮어 남은 시간의 증액을 막던 문제를 고쳤습니다. (#872, #875–#877)
- 초분할 진입 최소 점수 5점, 매매일지·일지 압축·주간 리포트·한국 대기 주문형 청산 경로의 비중 반영, 거래소 휴장일 달력
  적용이 들어갔습니다. (#873, #874, #902)
- 초분할 매수에만 붙는 프롬프트 부록에 "정찰병 진입" 관점을 넣었습니다. 틀린 진입은 작게 잘리고 놓친 진입은 되돌릴 수
  없으니 불확실성은 미진입이 아니라 비중으로 처리하라는 뜻입니다. 사용자와 함께 결정한 모순 정리: "분할매매 불가·올인/올아웃"
  전제는 초분할 매수에 적용하지 않음, 결정 규칙을 보정 점수 기준으로 명시, "지지선이 10% 넘게 멀면 손절 불가" 사유는 초분할
  진입에 적용하지 않음(손절 규칙상 최대 손절폭이 항상 정해지므로), 강세 국면에서 첫 확정 저항이 +3% 이내면 그 돌파를 증액
  조건으로 보고 목표는 다음 저항까지 80%. 게이트·하한·손절 규칙은 바뀌지 않습니다. 다른 종목의 프롬프트는 그대로입니다. (#889)
- 가속 증액, 상위 셋업 가중, 현재 유효 손절선 기준 위험 한도, 보유 7종목 6점 규칙의 부록 명시를 추가했습니다. (#908)
- 바뀌지 않은 것: 슬롯 수는 여전히 종목 수로 세므로 30% 종목도 한 자리를 차지합니다. 외부 구독자는 최초 비중만 따르고
  증액은 따르지 않습니다. 매도는 여전히 전량입니다.

## 2. 주도주 보유 규칙과 수익 보호선 단계 (KR·US)

- 주도주 판정·단계·매도 조건을 순수 규칙으로 구현하고, 배치 매도 점검·AI 매도 판단·AI 실패 시 대체 규칙·10분 주기 추세 이탈
  루프 모두에 같은 가드를 걸었습니다. AI가 다른 이유로 매도를 제안해도 판단 직후 가드가 보유로 바꾸고, AI의 손절·목표가
  조정 제안은 무시합니다. 장중 하드스탑 루프는 그대로 손절가(=최초 매수가)를 집행합니다. (#909)
- 매도 프롬프트를 펼쳐 찾은 모순 11건(목표가 도달 즉시 매도, 추적 손절 규칙, 3일 연속 하락 매도, 시간 관리 등)을 주도주에게만
  붙는 부록과 결정론 가드로 처리했습니다. 공유 매도 지시문은 그대로입니다. "추적 손절은 종가 기준" 문구와 실제 장중 집행의
  불일치는 기존 모순으로 남겨 별도 결정 과제로 두었습니다. (#909)
- 최종 리뷰 반영: 추세 이탈 루프의 판정 범위를 배치와 같은 40거래일로 맞추고, 한 종목 오류가 다른 종목 처리를 막지 않게
  했으며, 현재가를 모르면 손절가를 바꾸지 않습니다. 주도주 재설정이 "손절가는 내리지 않는다"는 원칙의 유일한 예외임을
  명시했습니다. (#914)

## 3. 재진입 (관측 v2 보강 → v3 실전)

- v2 관측에서 AI 재점검이 거절해도 감시를 이어가 눌림 반등·재돌파를 기다리게 했고, 재점검 입력에서 차트 이미지를 빼 비용을
  줄였습니다. 설계 문서에 비용·빈도 절을 추가했습니다. (#823, #824, 직접 커밋 `767f4c9e`·`201a1a22`)
- v3는 관측으로 시작해 사용자 결정(판단 시각 14:00/13:50, 매수 규칙과 같은 목표 계산, 시장 상황별 손절 상한, 흔들기 후 회복
  포함)을 반영한 뒤 실계좌 실전으로 전환했습니다. 리뷰에서 실제 매도의 원장 연결, 주 기준 신호만 매수, 주문 마감, 진입 락,
  1슬롯 대체 매수 금지를 보완했습니다. 배치 진입도 같은 시장 진입 락 안에서 실행되며, 락을 120초 못 잡으면 예전처럼
  진행합니다. (#900)

## 4. 매수·매도 판단 규칙

- **채점표**: 5점 이상은 추세 게이트·상습 손절 게이트를 통과한 종목에만 주고, 게이트로 미진입하는 종목은 최대 4점·거시 가점
  없음으로 맞췄습니다. 같은 후보 비교에서 게이트 종목만 점수가 내려가고 결정은 그대로였습니다. (#880)
- **사업 경쟁력(F4)과 지주회사**: 사업 모델과 매출원이 식별되고 출처 있는 구조적 훼손이 없으면 통과합니다(KR·US). 업종명이
  "회사 본부 및 경영 컨설팅 서비스업"이어도 법인명에 홀딩스·지주·홀딩이 있으면 지주회사로 보고, 핵심 관계회사 기준으로
  판단합니다. (#869)
- **미국 음수 자본 F2**: 자기자본이 음수인 미국 종목만 3년 연속 흑자와 주주환원, 순부채/EBITDA 2.5배 이하, 이자보상배율 4배
  이상, 최근 연도 잉여현금흐름 흑자를 계산해 판정 블록으로 넣습니다. 확인할 수 없는 값은 미달로 봅니다. 4월 이후 자사주
  매입형 음수 자본 종목은 탈락 뒤 30일 +9.1%, 적자 누적형은 −2.7%였습니다. 끄려면 `PRISM_US_NEG_EQUITY_F2=off`. (#882)
- **분산일 강세장**: 분산일로 강등된 강세장은 강등된 국면 규칙만 적용합니다. 미국 분산일 수는 S&P 500과 나스닥 중 작은 값입니다.
  손절·추적 손절·손익비 하한·최대 손실·추세 게이트는 그대로입니다. (#848)
- **거래량**: 장중 거래량이 이미 20일 평균의 2배를 넘으면 신호 충족으로 인정하고, 못 넘으면 "미확정"으로 둡니다. 오전 판단에는
  "오전 누적 거래량 ≥ 전일 거래량" 참고 사실을 붙이되 신호 판정에는 쓰지 않습니다. (#835, #836, #861)
- **목표가 저항**: 진행 중인 당일 봉의 고가·저가를 주요 저항·지지로 쓰지 않고, 52주 확정 최고가와 신고가 돌파 목표(진입가×1.20)
  조건 충족 여부를 사실로 넣습니다. 2a 규칙·손익비 하한·손절은 그대로입니다. (#840)
- **KR 수급·분기 EPS·미국 기관 자료**: 수급 비교 기간의 기업행위를 실제로 확인하고, 회사 현황에 확정 분기 EPS 표를 코드로
  제공하며, 미국은 일별 기관 자료가 없어 해당 조건을 평가하지 않는다고 명시했습니다. 매수 메시지의 "분기 EPS 미확인",
  "기업행위 미조정" 같은 문구를 줄이기 위한 변경입니다. (#846, #851, #852)
- **KR 매도 AI 국면**: AI 매도 판단에 시스템의 실시간 국면을 넣어, 결정론 손절 루프와 같은 추적 손절 폭을 쓰게 했습니다. (#830)
- **미국 시장 상태**: S&P 500과 나스닥 종합지수를 함께 봅니다. 2019~2026 재현에서 이 방식으로 풀린 날의 후보는 평균 +3.03%,
  계속 조정인 날은 −0.53%였습니다. 되돌리려면 `US_MARKET_PULSE_INDEX_MODE=spx`. (#833)
- **장중 손절 공백**: 보유 행이 2개 이상인 종목도 장중 손절·추세 이탈 루프가 계좌·행 단위로 보호합니다. 손절 문구를 실제
  집행 방식(장중 현재가 ≤ 손절가×0.995)에 맞췄습니다. (#859)

## 5. 스크리닝·시장 데이터

- **트리거 품질 우선순위**: 최근 180일 PRISM 기록(분석 후보의 +20% 도달 비율, 슬롯 기준 실현 손익)으로 트리거별 가중치(0.70~1.30)를
  하루 한 번 계산합니다. 가중치 0.95 이하 트리거는 "트리거별 1자리 보장"에서 빠지고, 남은 자리는 점수 × 가중치 순으로
  채웁니다. 트리거를 지우지 않고 총 선발 수와 텔레그램 점수도 그대로입니다. 운영 로그 재생에서 약한 트리거가 차지한 자리가
  KR 22→7, US 124→105로 줄었습니다(대체 종목의 사후 수익은 측정하지 않았습니다). 끄려면 `TRIGGER_QUALITY_PRIORITY=false`. (#907)
- **거래량 급증 트리거**: 전일 종가 대비 하락한 종목을 점수 계산 전에 제외하고, 미국 종가 강도 트리거에 한국과 같은 전일 종가
  회복 규칙을 넣었습니다. (#878)
- **미국 선별 대상 수집**: 지수 구성종목 먼저 수집, 거래대금 하한 5천만 달러 통일, KIS 조건검색으로 후보를 먼저 추린 뒤 가격
  조회, 메타데이터 확인을 예산 안에 끝내기. 5천만 달러는 성과 중립(더 낮춘 3천만·2천만 달러는 반례)이라는 재현에 근거했습니다.
  (#820, #826, #827, #834)
- KIS 장중 스냅샷의 일시 전송 오류를 재시도합니다. (#838)

## 6. 텔레그램 메시지·보고서·대시보드

- 공개 보고서·번역·텔레그램에서 데이터 제공사 이름과 링크를 숨깁니다. 수치와 판단은 바뀌지 않습니다. (#825)
- KR 매매 메시지에서 내부 용어를 쉬운 말로 바꾸고, 바뀐 단어 뒤 조사를 받침에 맞춰 고칩니다. 보류 사유에는 AI가 실제로 적은
  미진입 이유를 보여 주며, 국면 코드·보정 점수·점수 보정 사유도 한국어로 표시합니다(KR·US 한국어 메시지). (#831, #860, #862)
- 누적 수익률은 반 슬롯 시험 매수와 초분할 비중을 반영해 계산합니다(텔레그램 요약·대시보드). 거래별 승률·평균 수익률은 거래
  단위 그대로입니다. (#858)
- 매매일지가 0자로 저장되고 해외 방송 채널(en/ja/zh/es) 번역이 빈 메시지로 나가던 문제를 고쳤습니다. 빈 응답은 저장하거나
  보내지 않습니다. (#860)
- 대시보드: 보유 표 매수가 아래 비중 배지·평균가, 수익률 아래 슬롯 기준 수익률, 거래 이력 카드의 비중, 슬롯 사용률 카드의 실제
  비중 합계가 추가됐습니다. (#867)
- 주간 인사이트 리포트의 매수·매도 줄에 비중과 슬롯 기준 손익이 표시되고, 일지 압축은 부분 비중 거래에 비중을 표기해 교훈이
  1슬롯 결과로 오해되지 않게 합니다. (#874)

## 7. 모델·로그인·Codex 실행

- **모델**: OAuth에서 거부되는 gpt-5.4-mini와 gpt-5.6-luna를 gpt-6-luna로 옮겼습니다(리포트·번역·카카오·매매일지·차트 해석·
  모더레이션·아카이브). 텔레그램 봇 분석은 gpt-6.1-sol입니다. 프록시도 옛 모델 이름을 gpt-6-luna로 연결합니다. 리포트 경로는
  여전히 최고 등급 모델(astra)을 쓰지 않습니다. (#854)
- **매수·매도 판단 모델**: 운영 서버 설정으로 gpt-6-astra에서 gpt-6.1-sol로 바꿨습니다(9/30). 일회성 비교에서 판단·점수·
  목표/손절이 같았고 비용은 크게 낮았으나 응답은 20~50% 느렸습니다. 이 선택을 위해 Codex 설정이 gpt-6.1-sol을 받도록 했고,
  두 번째 모델을 상한 있게 비교하는 관측 기능도 넣었지만 운영에서는 꺼 두었습니다. 코드 기본값은 바뀌지 않았습니다. (#850)
- **로그인 하나**: 매수·매도 판단의 Codex CLI가 리포트 프록시를 거쳐 같은 로그인을 씁니다. 대신 리포트와 매매 판단이 같은 사용
  한도를 나눠 씁니다. 공유 토큰은 만료 2시간 전에 미리 갱신하고, 다른 프로세스가 이미 갱신한 토큰을 다시 읽습니다. (#841, #842)
- **계정별 강도**: 활성 OAuth 계정에 따라 매매 판단의 추론 강도와 빠른 처리 여부를 자동으로 고릅니다. 매 호출마다 읽으므로
  재시작이 필요 없습니다. (#847, #849)
- **안정성**: 병렬 매수 판단이 Codex 도구 서버를 동시에 띄우다 시작 단계에서 실패하던 문제를 시작 단계만 차례로 실행해
  고쳤습니다(9/22~10/1 미국 판단 일부는 예비 경로로 판단됐을 수 있습니다). 미국 번역 PDF는 매매 단계 뒤에 시작하고,
  인증 오류 경보는 줄 단위 시각으로 집계합니다. (#844, #855, #856)
- `/insight`와 BTC 사후 분석의 Sonnet을 claude-sonnet-5-5로 바꿨습니다. (#843)

## 8. 투자 방향·연구 교훈·관측 도구

- **North star**: 매매 변경 검토 절차 맨 앞에 PRISM의 투자 방향을 적었습니다. 오닐식 추세추종, 평소 손익비 관리, 크게 가는 소수
  종목에 의존한 계단식 성장, 초분할·재진입은 손실을 관리하며 가는 종목에 올라타기 위한 것, 핵심은 스크리닝과 매수 판단의
  정확도입니다. 점검 지표는 큰 수익 종목 포착과 비중 확대, 손실·최대 낙폭·회복 기간, 거래 빈도와 손절 비용, 선별·매수 정확도
  네 가지입니다. 프롬프트를 바꿀 때 기존 프롬프트와의 논리 모순을 찾아 사용자와 함께 결정하는 절차도 추가했습니다. (#889, #904)
- **연구 교훈 장부**(`docs/RESEARCH_LESSONS_ko.md`): 이미 결론이 난 질문을 다시 검증하지 않도록 연구 결론과 교훈을 한곳에
  모았습니다. 코드와 표만 남기는 연구 PR은 만들지 않습니다. (#896)
- **이번 기간에 검증했지만 채택하지 않은 것**: 미국 상대 거래대금 선호(검증 기간에서 실패), 확정 저항 목표 재계산(효과
  미입증), 미국 기관 수급 대용치(채택 기준 미달), 한국 오전 거래량 시각 보정(오탐 25%)은 채택하지 않았습니다. 한국 초분할 재현은
  손실·낙폭 감소를 보여 실전 도입 근거가 됐습니다. 손절 점검 주기·ATR 손절·증액 규칙 변형 연구는 결론만 교훈 장부에 남기고 PR은
  닫았습니다. (#829, #839, #845, #857)
- **주간 주도주 리포트**: 매주 일요일(운영 18:30 KST) 운영자 전용 채팅으로 이번 주 요약, 주도주 현황, 놓친 대박, 손절 비용,
  트리거별 성적, 방향 점검을 보냅니다. 계좌 기여는 고정 10슬롯 기준이며 공개 채널로는 보내지 않습니다. (#906)
- **2주 점검**: 10/18에 변경별 효과를 판단할 수 있도록 주도주 가드가 매도를 막은 순간의 가격, 트리거 품질로 자리를 잃은 후보,
  안전장치에 막힌 증액, AI 매도 실패 시 대체 규칙 사용, KIS 초당 호출 한도 초과를 기록합니다. 점검 도구는 읽기 전용입니다. (#912)

## 9. BTC 데모: LLM 시나리오 전환과 공지

모두 **Bybit 데모** 범위이며 실자금·다른 계좌·KR/US 전략은 바꾸지 않았습니다.

- 데모 메인 계좌의 신규 진입을 **5분 주기 LLM 시나리오** 방식으로 바꿨습니다(gpt-6-luna, 레버리지 10배 고정). LLM은 시나리오만
  제안하고, 수량·위험·주문 검증은 코드가 합니다. 시나리오마다 계좌 순자산의 2%가 비용 포함 계획 손실 예산이며, 하루 4% 손실이나
  전량 정산된 시나리오 3개 연속 순손실이면 신규 위험을 멈추고 운영자 검토 전에는 재개하지 않습니다. 10/3 11:20 KST 운영 전환
  뒤 첫 판단과 보호 점검 실행을 확인했습니다. 이것은 수익성 입증이 아닙니다. (#881, #883)
- 출력 계약 위반을 엄격히 거르고 모델 오류를 따로 집계하며, 판단 감사 기록이 보호 작업과 다투지 않게 했습니다. 취소 뒤 실제로
  제출되지 않은 교체 주문을 안전하게 정리하고, 프롬프트의 결정 논리를 실제 주문 의미와 맞췄습니다. (#885, #886, #888, #890)
- 매매 공지를 다시 썼습니다. 계좌 증거금 비중, 레버리지 전후 목표 수익, 손절 시 계좌 영향, 확인된 전후 비교를 구분하고,
  체결·누적 보유·계좌 순자산·TP/SL별 금액을 짧은 블록으로 나눕니다. 같은 상태를 다시 알리지 않고, 현재 TP와 청산 뒤 확인된
  순자산을 표시합니다. 공개 시세 한도 초과 시 잠시 물러서고, 오류 경보에서 판단 문제와 보호 문제를 구분합니다.
  (#891–#895, #903, #910, #911)
- 정상 펀딩 체결을 미확인으로 분류해 정산이 보류되던 결함과, 전량 청산 뒤 빈 손절 값 때문에 종료 공지가 빠지던 결함을
  고쳤습니다. 누락된 과거 공지 복구는 읽기 전용 점검만 하고 승인 전에는 보내지 않습니다. (#898, #899)
- 오프라인 인과 재생, 엄격한 판단 기록, 읽기 전용 운영 감사, 사전등록 재생 비교 도구를 추가했습니다. 같은 하루 재생의 순자산
  변화는 +2.13%/+2.24%/+1.33%(비용 2배)였지만 종료 거래가 3~4건뿐이고 최대 이익 거래 하나를 빼면 음수여서 **수익성은 입증되지
  않았습니다**. 최신 운영 보완 전후를 같은 기록으로 대조한 결과 매매 결과는 같았습니다(정산·공지 수리일 뿐 정책 변경 아님).
  (#884, #901, #905)

## 10. 보안·운영·저장소

- **봇 토큰**: 텔레그램은 요청 주소에 봇 토큰을 넣는데, HTTP 라이브러리가 그 주소를 로그에 남겨 일부 서버 로그에 토큰이
  평문으로 남았습니다. 로그를 만들 때 토큰 모양 문자열을 가리도록 하고, 기존 로그를 정리했으며, 봇 토큰 3개를 교체했습니다.
  교체 뒤 BTC 리포터와 주간 수집 로그에 다시 남던 경로도 막았습니다. (#897, #913)
- **실계좌 구독자**: 메시지 발행 시각 기준 30분이 넘은 신호는 주문하지 않고 경보를 보냅니다(`SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES`,
  `off`로 해제). 재부팅 뒤 자동 기동과 경보 설정 누락 점검을 운영 문서에 추가했습니다. 구독자가 9/16부터 10/3까지 멈춰 있던
  사례에서 나온 변경입니다. (#879)
- **기여자**: 외부 기여자 @tkgo11의 테스트 정리·발행 차단 보강 커밋을 저작자 그대로 반영했고, 과거에 CLA 동의를 남긴 기여자 6명을
  CLA 확인 허용 목록에 넣었습니다. (#819, #821)
- **저장소**: 오래된 릴리즈 노트(v2.10~v2.20), 임시 문서, 쓰지 않는 이미지를 정리했고, `CLAUDE.md`의 오래된 사실(미국 시가총액
  기준, 손절 설명, 버전)을 바로잡았습니다. (#837, 직접 커밋 `11fbca80`)
- **README 개편**(시즌2 실적·매매 방식·기여자)도 이번 버전에 포함됩니다.

## 개발자용 상세 — 동일 가중치 커밋 집계

`v2.23.0..3988a985`의 **276개 커밋을 모두 오래된 순서부터 확인**했습니다. 이 범위에는 릴리즈 문서 작성 커밋이 없어 제외한
커밋은 없습니다. 각 커밋은 1표이며 날짜·최근성·변경 줄 수·작성자·PR 크기에 추가 가중치를 주지 않았습니다.
비병합 커밋은 주된 목적 하나에만 배정하고, 병합 커밋은 별도로 집계했습니다. PR이 없는 직접 커밋 **3개**도 포함했습니다.
#880에 함께 들어온 초분할 테스트 시각 고정 커밋(`2e6ba489`)은 초분할로 분류했습니다. 커밋 수가 중요도·완성도·수익성
점수라는 뜻은 아닙니다.

<details>
<summary>276개 커밋의 주제별 집계 펼치기</summary>

| 작업 묶음 | 커밋 | 비율 |
|---|---:|---:|
| 초분할 실전·증액 시나리오·B3 관측 (`micro_split`) | 26 | 9.4% |
| 재진입 v2 보강·v3 실전 (`reentry`) | 9 | 3.3% |
| 주도주 보유 규칙 (`runner_hold`) | 3 | 1.1% |
| 매수·매도 판단 규칙(채점표·F2/F4·분산일·거래량·저항·장중 손절) (`trading_rules`) | 15 | 5.4% |
| 스크리닝·시장 데이터(트리거 품질·미국 선별 수집) (`screening_data`) | 13 | 4.7% |
| 보고서·메시지·수익률 표시 (`reports_messages`) | 7 | 2.5% |
| 모델·로그인·Codex 실행 (`models_oauth`) | 15 | 5.4% |
| 사전등록 연구·교훈 장부·투자 방향 (`research_governance`) | 17 | 6.2% |
| 주간 주도주 리포트·2주 점검 (`observability`) | 3 | 1.1% |
| 보안·구독자·기여자·저장소 정리 (`ops_security`) | 13 | 4.7% |
| BTC 데모 LLM 시나리오·공지·정산·재생 도구 (`btc`) | 38 | 13.8% |
| 병합 커밋 (`merge`) — PR 병합 91개 + 동기화 병합 26개 | 117 | 42.4% |
| **합계** | **276** | 100% |

</details>

<details>
<summary>오래된 작업 누락 점검: 기간별 집계</summary>

| 작성일 구간 (KST) | 비병합 | 병합 | 합계 |
|---|---:|---:|---:|
| 2026-09-16 – 2026-09-28 | 27 | 13 | 40 |
| 2026-09-29 – 2026-09-30 | 35 | 23 | 58 |
| 2026-10-01 – 2026-10-02 | 32 | 31 | 63 |
| 2026-10-03 | 26 | 17 | 43 |
| 2026-10-04 – 2026-10-05 | 39 | 33 | 72 |
| **합계** | **159** | **117** | **276** |

첫 구간의 9월 16일 작성 커밋 7개는 외부 기여자의 커밋으로, 9월 28일(KST) #819로 병합됐습니다.

</details>

전체 SHA·작성일·제목·단일 분류·PR 연결과 PR별 규모는
[릴리즈 감사 자료](https://github.com/dragon1086/prism-insight/blob/v2.24.0/docs/release_audits/v2.24.0.json)에 있습니다.
PR 연결은 제목 추측이 아니라 GitHub가 기록한 병합 커밋과 병합 부모 간 Git 도달 가능성을 기준으로 확인했습니다.

## 개발자용 상세 — PR별 변경 규모

<details>
<summary>병합 PR 91개 펼치기</summary>

| PR | 제목 | 규모 |
|---|---|---|
| [#819](https://github.com/dragon1086/prism-insight/pull/819) | chore: test hygiene, stale-test reconciliation, publish-guard fix (tkgo11 #742 extract) | 66 files, +1,226/−1,370 |
| [#820](https://github.com/dragon1086/prism-insight/pull/820) | fix: screen US index members first in the expanded universe | 5 files, +153/−7 |
| [#821](https://github.com/dragon1086/prism-insight/pull/821) | ci: allowlist contributors who gave retroactive CLA consent in #603 | 1 files, +3/−1 |
| [#823](https://github.com/dragon1086/prism-insight/pull/823) | feat: re-entry v2 second chance after a declined recheck (pullback bounce / re-breakout) | 7 files, +215/−32 |
| [#824](https://github.com/dragon1086/prism-insight/pull/824) | fix: re-entry recheck strips base64 chart images from report text (~12x fewer tokens) | 4 files, +25/−4 |
| [#825](https://github.com/dragon1086/prism-insight/pull/825) | fix: keep data-vendor names out of published reports and trading messages | 12 files, +94/−12 |
| [#826](https://github.com/dragon1086/prism-insight/pull/826) | feat: unify the US trading-value floor at $50M across all triggers (#822 step 1) | 7 files, +117/−99 |
| [#827](https://github.com/dragon1086/prism-insight/pull/827) | feat: shortlist the US universe with the KIS condition search before pricing (#822 step 2) | 4 files, +388/−1 |
| [#829](https://github.com/dragon1086/prism-insight/pull/829) | docs: #822 step 3 (relative turnover) not adopted after pre-registered validation | 1 files, +49/−6 |
| [#830](https://github.com/dragon1086/prism-insight/pull/830) | fix: give the KR AI sell decision the system's live market regime | 3 files, +145/−0 |
| [#831](https://github.com/dragon1086/prism-insight/pull/831) | fix: stop internal terms leaking into KR trading messages and fix particles | 4 files, +103/−7 |
| [#832](https://github.com/dragon1086/prism-insight/pull/832) | feat: oneil-adaptive-v2 SHADOW policy — volatility-sized first entry, wider add ladder, no volume gate | 29 files, +1,273/−130 |
| [#833](https://github.com/dragon1086/prism-insight/pull/833) | feat: US Market Pulse reads the S&P 500 and the NASDAQ Composite together | 7 files, +284/−25 |
| [#834](https://github.com/dragon1086/prism-insight/pull/834) | fix: finish US eligibility metadata within budget (liquidity order, cache, KIS cap) | 4 files, +232/−9 |
| [#835](https://github.com/dragon1086/prism-insight/pull/835) | fix: let BUY count today's volume surge as a lower bound (KR/US) | 8 files, +495/−1 |
| [#836](https://github.com/dragon1086/prism-insight/pull/836) | docs: record volume lower-bound deploy and first KR batch | 1 files, +15/−0 |
| [#837](https://github.com/dragon1086/prism-insight/pull/837) | docs: prune temporary notes, old release notes and unused images | 98 files, +0/−9,198 |
| [#838](https://github.com/dragon1086/prism-insight/pull/838) | fix: retry KIS intraday snapshot chunks on transport errors | 2 files, +57/−1 |
| [#839](https://github.com/dragon1086/prism-insight/pull/839) | research: confirmed-resistance target replay v1 (RETIRE) | 3 files, +440/−0 |
| [#840](https://github.com/dragon1086/prism-insight/pull/840) | fix: stop treating today's open-bar high as resistance; add 52-week-high facts for 2a | 13 files, +251/−5 |
| [#841](https://github.com/dragon1086/prism-insight/pull/841) | feat: route the BUY/SELL Codex CLI through the OAuth proxy (one login) | 4 files, +239/−1 |
| [#842](https://github.com/dragon1086/prism-insight/pull/842) | fix: refresh the shared OAuth token ahead of the healthcheck cron gap | 4 files, +108/−2 |
| [#843](https://github.com/dragon1086/prism-insight/pull/843) | feat: switch Sonnet usages to claude-sonnet-5-5 | 3 files, +60/−14 |
| [#844](https://github.com/dragon1086/prism-insight/pull/844) | fix: start US report translations after the BUY/SELL tracking step | 2 files, +71/−4 |
| [#845](https://github.com/dragon1086/prism-insight/pull/845) | research: US UDVR institutional-proxy replay v1 (H1 RETIRE, H2 INSUFFICIENT) | 3 files, +346/−0 |
| [#846](https://github.com/dragon1086/prism-insight/pull/846) | fix: state that US daily institutional flow is not supplied | 4 files, +47/−9 |
| [#847](https://github.com/dragon1086/prism-insight/pull/847) | feat: pick the BUY/SELL Codex effort from the active OAuth account | 3 files, +91/−0 |
| [#848](https://github.com/dragon1086/prism-insight/pull/848) | fix: stop distribution days turning a bull market into "no new buys" | 5 files, +114/−12 |
| [#849](https://github.com/dragon1086/prism-insight/pull/849) | feat: make the Codex trading service tier configurable per account | 7 files, +119/−9 |
| [#850](https://github.com/dragon1086/prism-insight/pull/850) | feat: capped SHADOW comparison of a second BUY-scenario model (gpt-6.1-sol) | 5 files, +293/−1 |
| [#851](https://github.com/dragon1086/prism-insight/pull/851) | fix: give the KR company-status writer a fixed quarterly EPS table | 6 files, +736/−14 |
| [#852](https://github.com/dragon1086/prism-insight/pull/852) | fix: check KR flow windows for corporate actions instead of always caveating | 8 files, +283/−10 |
| [#853](https://github.com/dragon1086/prism-insight/pull/853) | fix: emit KR micro-split and decision-input SHADOW from the enhanced agent | 2 files, +32/−2 |
| [#854](https://github.com/dragon1086/prism-insight/pull/854) | feat: move off gpt-5.4-mini/gpt-5.6 models (luna -> gpt-6-luna, bot -> gpt-6.1-sol) | 43 files, +113/−106 |
| [#855](https://github.com/dragon1086/prism-insight/pull/855) | fix: count OAuth health log errors by line time, not file mtime | 2 files, +70/−2 |
| [#856](https://github.com/dragon1086/prism-insight/pull/856) | fix: serialize Codex MCP boot so parallel BUY analyses stop timing out | 4 files, +104/−2 |
| [#857](https://github.com/dragon1086/prism-insight/pull/857) | research: KR intraday volume pace v1 and KR micro-split B3 v1 replays | 6 files, +964/−0 |
| [#858](https://github.com/dragon1086/prism-insight/pull/858) | fix: weight cumulative returns by occupied slot fraction (KR/US) | 8 files, +228/−11 |
| [#859](https://github.com/dragon1086/prism-insight/pull/859) | fix: protect pyramided holdings intraday and align stop wording (KR/US) | 17 files, +424/−162 |
| [#860](https://github.com/dragon1086/prism-insight/pull/860) | fix: journal/translation LLM 400s, per-metric peer usability, hold reasons, jargon (KR/US) | 16 files, +269/−34 |
| [#861](https://github.com/dragon1086/prism-insight/pull/861) | feat: morning reference fact — morning cumulative volume ≥ previous day (KR/US) | 5 files, +100/−7 |
| [#862](https://github.com/dragon1086/prism-insight/pull/862) | fix: render regime/score codes and score-adjustment reasons in Korean messages | 2 files, +39/−1 |
| [#863](https://github.com/dragon1086/prism-insight/pull/863) | feat: B3 all-entries SHADOW core (oneil-adaptive-v3-ae, KR/US) | 7 files, +523/−43 |
| [#864](https://github.com/dragon1086/prism-insight/pull/864) | feat: B3 all-entries SHADOW runtime — KR inputs, hooks, worker (KR/US) | 11 files, +780/−2 |
| [#865](https://github.com/dragon1086/prism-insight/pull/865) | fix: stamp the B3 worker decision after every input is collected | 2 files, +29/−4 |
| [#866](https://github.com/dragon1086/prism-insight/pull/866) | fix: B3 SHADOW unit uses the versioned interpreter | 1 files, +2/−1 |
| [#867](https://github.com/dragon1086/prism-insight/pull/867) | feat: micro-split LIVE (KR/US), default off | 29 files, +1,016/−52 |
| [#868](https://github.com/dragon1086/prism-insight/pull/868) | fix: micro-split display (USD in US summary, 80% entry ladder text) | 3 files, +25/−7 |
| [#869](https://github.com/dragon1086/prism-insight/pull/869) | fix: F4 business clarity + holding-company look-through (KR/US) | 13 files, +251/−78 |
| [#872](https://github.com/dragon1086/prism-insight/pull/872) | fix: pause fixed-ladder micro-split adds (default off) | 4 files, +35/−1 |
| [#873](https://github.com/dragon1086/prism-insight/pull/873) | feat: micro-split entry score floor 5 (KR/US) | 7 files, +209/−0 |
| [#874](https://github.com/dragon1086/prism-insight/pull/874) | fix: micro-split size in journal, compression, weekly report, pending exit | 10 files, +154/−30 |
| [#875](https://github.com/dragon1086/prism-insight/pull/875) | feat: 시나리오 기반 초분할 증액 계획(add_plan)으로 고정 사다리 대체 (KR/US) | 17 files, +2,284/−82 |
| [#876](https://github.com/dragon1086/prism-insight/pull/876) | fix: add_plan lens labels never drop a scenario | 2 files, +18/−3 |
| [#877](https://github.com/dragon1086/prism-insight/pull/877) | fix: per-session add plans (next-session review must not replace today's plan) | 6 files, +58/−6 |
| [#878](https://github.com/dragon1086/prism-insight/pull/878) | fix: volume-surge screening excludes stocks closing below the previous close | 3 files, +105/−0 |
| [#879](https://github.com/dragon1086/prism-insight/pull/879) | fix: subscriber skips Pub/Sub signals older than the age cutoff | 3 files, +94/−0 |
| [#880](https://github.com/dragon1086/prism-insight/pull/880) | fix: buy_score rubric matches the trend and repeat stop-out gates | 5 files, +62/−24 |
| [#881](https://github.com/dragon1086/prism-insight/pull/881) | BTC LLM scenario foundation — execution disabled pending broker proof | 34 files, +5,351/−1 |
| [#882](https://github.com/dragon1086/prism-insight/pull/882) | feat: US negative-equity issuers get a deterministic F2 block | 3 files, +235/−0 |
| [#883](https://github.com/dragon1086/prism-insight/pull/883) | docs: record BTC LLM demo rollout evidence | 2 files, +32/−3 |
| [#884](https://github.com/dragon1086/prism-insight/pull/884) | BTC LLM causal replay and semantic decision tapes | 13 files, +1,844/−0 |
| [#885](https://github.com/dragon1086/prism-insight/pull/885) | BTC: strict LLM contracts, bounded decision memory and separate model incidents | 23 files, +531/−31 |
| [#886](https://github.com/dragon1086/prism-insight/pull/886) | BTC: preserve decision audits across independent protection contention | 3 files, +48/−6 |
| [#888](https://github.com/dragon1086/prism-insight/pull/888) | BTC: reconcile proven unsubmitted cancelled replacements safely | 6 files, +196/−8 |
| [#889](https://github.com/dragon1086/prism-insight/pull/889) | feat: micro-split entry frame and BUY prompt contradiction fixes | 8 files, +150/−21 |
| [#890](https://github.com/dragon1086/prism-insight/pull/890) | BTC: audit prompt logic, align order contracts and exact target identity | 10 files, +339/−17 |
| [#891](https://github.com/dragon1086/prism-insight/pull/891) | BTC: readable position/risk notices and verified before-after comparisons | 9 files, +898/−45 |
| [#892](https://github.com/dragon1086/prism-insight/pull/892) | fix(btc): concise lifecycle notices and bounded post-model lock acquisition | 9 files, +715/−113 |
| [#893](https://github.com/dragon1086/prism-insight/pull/893) | fix(btc): bounded public rate-limit recovery and observed protection contention | 7 files, +203/−10 |
| [#894](https://github.com/dragon1086/prism-insight/pull/894) | fix(btc): distinguish judgment and protection status in error-burst alerts | 4 files, +177/−5 |
| [#895](https://github.com/dragon1086/prism-insight/pull/895) | fix(btc): no status reannouncements and safer notification completion | 9 files, +343/−29 |
| [#896](https://github.com/dragon1086/prism-insight/pull/896) | docs: research lessons ledger linked from the trading change harness | 2 files, +47/−0 |
| [#897](https://github.com/dragon1086/prism-insight/pull/897) | fix: redact Telegram bot tokens from all logs | 13 files, +91/−0 |
| [#898](https://github.com/dragon1086/prism-insight/pull/898) | fix(btc): exact Funding execution proof for pending settlement | 4 files, +261/−1 |
| [#899](https://github.com/dragon1086/prism-insight/pull/899) | fix(btc): flat closure notices and explicit guarded recovery | 8 files, +495/−4 |
| [#900](https://github.com/dragon1086/prism-insight/pull/900) | feat: re-entry v3 LIVE (re-break / retest / shakeout recovery, intraday 14:00 KR · 13:50 ET) | 19 files, +5,543/−639 |
| [#901](https://github.com/dragon1086/prism-insight/pull/901) | feat(btc): add read-only forward audit and registered replay comparison | 7 files, +1,346/−1 |
| [#902](https://github.com/dragon1086/prism-insight/pull/902) | fix: micro-split add plans follow exchange calendars, never a holiday | 2 files, +62/−7 |
| [#903](https://github.com/dragon1086/prism-insight/pull/903) | feat(btc): readable trade notices with fill, position and account PnL | 11 files, +404/−39 |
| [#904](https://github.com/dragon1086/prism-insight/pull/904) | docs: north-star investment direction in the trading harness | 2 files, +24/−0 |
| [#905](https://github.com/dragon1086/prism-insight/pull/905) | docs(btc): precise latest-version replay comparison and accounting audit | 3 files, +145/−0 |
| [#906](https://github.com/dragon1086/prism-insight/pull/906) | feat: weekly leader (runner) report for KR/US | 3 files, +1,037/−0 |
| [#907](https://github.com/dragon1086/prism-insight/pull/907) | feat(screening): data-driven trigger quality priority in final selection (KR/US) | 11 files, +1,228/−16 |
| [#908](https://github.com/dragon1086/prism-insight/pull/908) | feat(micro-split): acceleration adds and conviction tilt (KR/US) | 10 files, +708/−83 |
| [#909](https://github.com/dragon1086/prism-insight/pull/909) | feat(trading): runner hold rule (O'Neil 8-week, R5) with staged profit protection (KR/US) | 19 files, +1,838/−15 |
| [#910](https://github.com/dragon1086/prism-insight/pull/910) | fix(btc): keep current TP and runner context in concise notices | 5 files, +189/−58 |
| [#911](https://github.com/dragon1086/prism-insight/pull/911) | fix(btc): include verified account equity after scenario closure | 8 files, +230/−1 |
| [#912](https://github.com/dragon1086/prism-insight/pull/912) | feat(observability): 2주 점검(10/18) 근거 기록 보강 + tools/two_week_review.py | 20 files, +1,813/−31 |
| [#913](https://github.com/dragon1086/prism-insight/pull/913) | fix(logging): keep Telegram bot tokens out of BTC reporter and weekly firecrawl logs | 4 files, +15/−0 |
| [#914](https://github.com/dragon1086/prism-insight/pull/914) | fix(runner-hold): harden first-session edges from the final review | 7 files, +45/−21 |

</details>

## 검증

- 커밋 집합을 `git rev-list v2.23.0..3988a985`와 대조해 276개 전부가 한 번씩, 하나의 분류로만 들어갔는지 확인했습니다.
  병합 PR 91개의 병합 커밋 SHA를 GitHub 기록과 대조했고, 이 문서 본문이 91개 PR과 직접 커밋 3개를 모두 언급하는지 스크립트로
  확인했습니다.
- `v2.23.0` 태그는 원격 기준 `69a7eb3f`(v2.23.0 노트에 #800–#818을 반영한 커밋)를 가리킵니다. 이번 범위는 그 다음부터입니다.
- 실전 전환(초분할 #867·#875·#908, 주도주 #909, 재진입 #900, 트리거 품질 #907 등)은 각 PR의 CI 통과 뒤 병합해 운영 서버에
  ff-only로 배포했습니다. 초분할은 주문 없는 점검 뒤 사용자 승인으로 켰고, 재진입 v3는 사용자가 관측 단계 없이 실전을 직접
  승인했습니다.
- **아직 관측하지 않은 것**: 10/5는 한국 휴장(개천절 대체공휴일)이라 초분할 첫 실제 증액, 주도주 판정에 따른 손절가 재설정,
  트리거 품질 선발, 재진입 v3 실제 매수의 첫 한국 관측은 10/6 배치부터입니다. 첫 주간 주도주 리포트는 10/11, 2주 점검은
  10/18입니다.
- 테스트·스모크는 실제 주문이나 정기 배치의 성공을 뜻하지 않으며, 관측(SHADOW)·재생 결과는 수익성의 증명이 아닙니다.

## 업데이트 방법

```bash
git status --short --branch
git fetch origin --tags
git show --no-patch --oneline v2.24.0
```

- 실제 배포는 `docs/SERVER_GIT_OPERATIONS_ko.md`에 따라 clean 대상에 확인한 commit을 fast-forward하고 변경 범위별 검증을
  수행합니다. 이 명령 예시는 자동 업그레이드 스크립트가 아닙니다.
- **초분할**: `MICRO_SPLIT_LIVE_ENABLED=true`일 때만 실전입니다(코드 기본값 꺼짐). `MICRO_SPLIT_LIVE_MARKETS`(기본 `KR,US`),
  증액만 멈추려면 `MICRO_SPLIT_LIVE_ADDS_ENABLED=false`, 최소 점수 5점을 끄려면 `MICRO_SPLIT_MIN_SCORE=off`, 상위 셋업 가중을
  끄려면 `MICRO_SPLIT_CONVICTION_TILT=false`. 증액은 B3 워커(`prism-b3-ae-shadow@kr`, `@us`)가 집행하며 워커는 시작할 때만
  설정을 읽으므로 바꾼 뒤 재시작합니다. B3 관측은 `B3_AE_SHADOW_ENABLED`(기본 꺼짐)입니다.
- **주도주 보유**: 코드 기본값이 **켜짐**입니다(`RUNNER_HOLD_ENABLED=true`, `RUNNER_HOLD_MARKETS=KR,US`). 끄려면 `false`.
  배포 직후 기존 보유 종목도 소급 판정되어 손절가가 최초 매수가로 바뀔 수 있습니다.
- **재진입 v3**: `REENTRY_V3_LIVE_ENABLED=true`와 `REENTRY_V3_LLM_RECHECK=true`가 있어야 실계좌 매수가 일어납니다(코드 기본값
  꺼짐). 판단 실행은 crontab(한국 14:00·16:40 KST, 미국 13:50·17:20 ET)으로 직접 등록하고 v2 줄은 내립니다.
  자세한 절차는 `docs/REENTRY_V3_LIVE_ko.md` 11절입니다.
- **트리거 품질**: 기본 켜짐, 끄려면 `TRIGGER_QUALITY_PRIORITY=false`. 미국 시장 상태를 S&P 500 단독으로 되돌리려면
  `US_MARKET_PULSE_INDEX_MODE=spx`.
- **모델**: 리포트·보조 모델은 `REPORT_MODEL`/`REPORT_AUX_MODEL`과 MCP 기본 모델을 `gpt-6-luna`로 맞춥니다. 매수·매도 판단
  모델은 `PRISM_BUY_CODEX_MODEL`/`PRISM_SELL_CODEX_MODEL`로 정하며, gpt-6.1-sol에서 빠른 처리를 쓰려면 최신 Codex CLI가
  필요합니다. Codex를 리포트 프록시로 연결하는 방법과 롤백은 `docs/CODEX_VIA_OAUTH_PROXY_ko.md`에 있습니다. #860 이후 리포트
  OAuth 프록시를 재시작해야 번역 요청 수정이 적용됩니다.
- **구독자**: 실계좌 구독자는 최신 예제로 갱신하고 경보 채팅 설정을 확인하십시오. 직접 운영하는 봇이 있다면 오래된 로그에 토큰이
  남았는지 확인하고 필요하면 토큰을 교체하십시오.
- 주간 주도주 리포트(`tools/weekly_runner_report.py`) cron은 문서에만 있으므로 배포 때 직접 추가합니다.

## 참고 사항과 알려진 한계

- **태그에 코드가 포함됨, 기능이 기본 활성임, 운영 검증 완료, 수익성 입증은 서로 다릅니다.**
- **10/18 2주 점검 전까지 매매 로직 변경을 보류합니다.** 여러 변경이 같은 주에 실전이 되어 효과를 구분하기 위해서입니다.
  장애·오류 수정만 진행합니다.
- 주도주 보유 규칙의 근거는 11건의 사후 분석입니다. 보유 연장으로 늘어난 이익과 반납한 이익을 주간 리포트로 함께 봅니다.
- 재진입 v3는 사전 통과 기준을 넘지 못한 상태에서 사용자 판단으로 실전에 들어갔습니다. 시장당 하루 2건 상한과 기존 매수 점검이
  위험을 제한합니다.
- 초분할에서 보유 슬롯·업종·7종목 한도는 비중이 아니라 종목 수로 셉니다. 보유 7종목 이상 6점 규칙은 프롬프트 규칙이며 코드로
  강제하지 않습니다. 외부 구독자는 증액을 따르지 않습니다.
- 초기 관측에서 증액 계획이 비어 있는 경우가 많았습니다. 매수·점검 단계가 계획을 쓰지 않는지 2주 점검에서 확인합니다.
- 매도 프롬프트의 "추적 손절은 종가 기준" 문구와 실제 장중 집행의 불일치는 남아 있습니다(종가 집행 재현이 더 나빠 집행 방식은 유지).
- 트리거 품질 가중치는 최근 180일 기록에 의존하며 표본이 적은 트리거는 1.0 근처에 머뭅니다.
- 미국 판단은 9/22~10/1 일부가 예비 경로로 이뤄졌을 수 있으니 그 기간 성과 분석에 유의하십시오.
- 리포트와 매수·매도 판단이 OAuth 사용 한도를 함께 쓰므로 한도가 소진되면 둘 다 멈출 수 있습니다.
- BTC 변경은 데모 범위이고, LLM 시나리오의 수익성은 입증되지 않았습니다.
- 오래된 릴리즈 노트(v2.10~v2.20)는 `docs/`에서 빠졌지만 각 태그와 GitHub 릴리즈에서 볼 수 있습니다.

## 텔레그램 공지

### 한국어

```text
🚀 PRISM-INSIGHT v2.24.0 — 초분할 실전 매수 · 주도주 보유 · 재진입 실전

9월 28일 이후 276개 커밋과 91개 PR을 날짜순으로 빠짐없이 묶었습니다. 이번 버전의 중심은 매매 방식입니다. 손실은 작게 자르고, 크게 가는 소수 종목에 올라타 계단식으로 키운다는 방향에 맞춰 사는 방식·들고 가는 방식·다시 타는 방식을 실제 계좌에 적용했습니다.

🧩 나눠 사기(초분할)
· 새 종목은 1슬롯을 한 번에 사지 않고, 종목의 평소 변동 폭에 따라 약 35~80%만 먼저 삽니다
· AI가 미리 세운 증액 시나리오가 실제로 확인될 때만, 수익 중일 때만 조금씩 늘립니다
· 빠르게 가는 종목은 한 세션에 두 번까지 늘리고, 강한 트리거의 8점 이상 종목은 처음부터 조금 더 크게 삽니다
· 처음에 일부만 사므로 시장 국면과 관계없이 5점 이상이면 진입합니다(보유 7종목 이상이면 6점)

🏃 주도주는 오래 들고 가기
· 매수 후 4~15거래일 안에 +20% 이상 오른 종목은 손절가를 매수가로 올려 두고, 50일선이나 매수가가 깨질 때까지 최대 40거래일 보유합니다
· 목표가 도달이나 촘촘한 손절로 크게 갈 종목을 일찍 팔던 문제를 줄이려는 변경입니다

🔁 다시 올라타기(재진입)
· 손절되거나 보류된 종목이 기준 가격 위에서 다시 힘을 보이면 최대 3번까지 다시 삽니다
· 한국 14:00, 미국 13:50에 판단하고, AI 재점검과 기존 매수 점검을 모두 통과해야 삽니다

🎯 고르는 눈
· PRISM 자체 기록으로 약한 트리거를 가려 좋은 트리거에 자리를 더 줍니다
· 전일보다 내린 종목은 거래량 급증 후보에서 빼고, 게이트에 걸린 종목은 점수를 낮게 매깁니다
· 미국은 S&P 500과 나스닥을 함께 보고 시장 상태를 판단합니다

📱 메시지·대시보드
· 매수·추가 매수·매도 메시지와 대시보드에 비중, 평균 매수가, 슬롯 기준 손익이 나옵니다
· 누적 수익률을 실제 사용한 비중으로 계산하고, 보류 사유에 AI의 실제 이유를 보여 줍니다
· 해외 방송 채널 번역이 비어 있던 문제를 고쳤습니다

⚙️ 운영·보안
· 로그에 남던 텔레그램 봇 토큰을 가리고 토큰을 교체했습니다
· 실계좌 구독자는 30분 넘은 지난 신호로 주문하지 않습니다
· 모델을 gpt-6-luna·gpt-6.1-sol로 정리했습니다

₿ BTC 데모
· 데모 계좌 신규 진입을 5분 주기 AI 시나리오 방식으로 바꾸고, 매매 공지를 읽기 쉽게 다시 썼습니다

※ 여러 변경이 한 주에 실전이 되어, 10월 18일 2주 점검 전까지는 매매 규칙을 더 바꾸지 않고 효과를 지켜봅니다

릴리즈노트:
https://github.com/dragon1086/prism-insight/releases/tag/v2.24.0

가상 운용·연구 및 소프트웨어 변경 안내이며 투자 권유가 아닙니다.
```

### English

```text
🚀 PRISM-INSIGHT v2.24.0 — Split entries live · Leader holding · Re-entry live

This release groups all 276 commits and 91 PRs since September 28. The focus is how PRISM trades: cut losses small, ride the few big winners and grow in steps. That idea now drives how positions are bought, held and re-entered on the live accounts.

🧩 Buying in parts (micro-split)
· A new position no longer buys the full slot at once; it starts with about 35–80% of a slot, sized by the stock's usual volatility
· It adds only when an AI-written add scenario is confirmed, and only while the position is in profit
· Fast movers can add twice in one session, and score-8+ picks from the strongest triggers start a step larger
· Because the first buy is partial, split entries need a buy score of 5 in any market regime (6 when 7+ stocks are held)

🏃 Holding the leaders
· A stock up 20%+ within 4–15 trading days gets its stop moved to the entry price and is held up to 40 trading days until the 50-day line or the entry price breaks
· This targets the habit of selling future big winners early on targets or tight trailing stops

🔁 Getting back on (re-entry)
· A stopped-out or skipped stock that shows strength above its key level can be bought again, up to three times
· Decisions run at 14:00 Korea time and 13:50 New York time, and need an AI recheck plus the normal buy checks

🎯 Picking better
· PRISM's own track record now ranks the triggers, giving weak triggers less room
· Stocks down on the day are dropped from volume-surge candidates, and gate-blocked stocks get low scores
· The US market state now reads the S&P 500 and the NASDAQ Composite together

📱 Messages and dashboard
· Buy, add, sell messages and the dashboard show position size, average entry and slot-based P&L
· Cumulative returns use the size actually deployed, and hold messages show the AI's real reason
· Fixed empty translations on the broadcast channels

⚙️ Operations and security
· Telegram bot tokens are masked in logs, and the tokens were rotated
· The live-account subscriber no longer trades signals older than 30 minutes
· Models were consolidated on gpt-6-luna and gpt-6.1-sol

₿ BTC demo
· Demo-account entries now come from 5-minute AI scenarios, with clearer trade notices

Many changes went live in the same week, so trading rules stay frozen until the two-week review on October 18.

Release notes:
https://github.com/dragon1086/prism-insight/releases/tag/v2.24.0

Software, virtual-operation and research updates; not investment advice.
```
