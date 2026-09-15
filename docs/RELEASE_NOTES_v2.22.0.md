# PRISM-INSIGHT v2.22.0 — 전략 원장 · BTC 안전성 · 분석·운영 신뢰성

> **발행일**: 2026-09-15
> **범위**: `v2.21.0` (`54355eb2`) → `512ed3de` (PR #729까지) · 제품 변경 커밋 **331개** / 병합 PR **109개**
> **규모**: 파일 **574개**, **+88,012 / −6,570줄** · 2026-08-27–2026-09-15
> **집계 기준**: 릴리즈 문서·감사 자료 작성 커밋은 위 제품 변경 통계에서 제외합니다. 새 태그에는 이 릴리즈 문서도 포함됩니다.

## 한눈에 보기

이전 정식 릴리즈는 **v2.21.0, 2026-08-27(KST)**입니다. 이번 버전은 지난 19일의 변경을
오래된 순서부터 검토해, **전략과 실제 계좌를 분리하는 원장**, **BTC 데모 실행 안전성**,
**근거를 보존하는 분석·관측**, **실제 입력 구조를 검증하는 운영 절차**로 묶었습니다.
최근 수급·배치 수정만을 강조하지 않고, 초반 리포트·Codex·브랜딩부터 중반 BTC 연구와
격리 진단 기반까지 함께 정리했습니다.

- **리포트·봇**: 긴 보고서의 모델/추론 설정, 번역 PDF와 평가 지연, 분기 EPS·경쟁 근거,
  안전한 실패 처리와 PDF 캐시를 함께 보강했습니다.
- **주식 매매·스크리닝**: 후보 보충·레짐별 cap·진입 직전 가격·파일럿 예산·fill chaser를
  정비하고, 근거 없이 +15% 목표를 보장하던 손익비와 포화 점수를 제거했습니다.
- **전략 원장·초분할**: 현금과 독립된 슬롯 원장 v2, scout 관찰, 50→100 파일럿 상태,
  동결 알림을 구현했습니다. 실주문 초분할 활성화와는 구분합니다.
- **BTC**: 체결·정산·부분 노출 보호, native SL/TP와 공유 실행을 데모 경로에서 보강했습니다.
  별도의 연구 묶음은 사건 재생·전략 조합·비중·분할·하위 봉 가설의 재현과 기각 결과를 담습니다.
- **관측·격리 진단**: 후보→진입→청산 연결, 매매일지 영향, 결정론적 Evidence Packet과
  주문 없는 진단 도구를 추가했습니다. CAPTURE·SHADOW·비활성 상태를 LIVE와 혼동하지 않습니다.
- **데이터·운영·소개**: 한국 KIS 경로, 원격 리포트 조회, 미국 데이터 열 구조, 알림의 불명 상태,
  배포 검증 하네스, Priso 마스코트와 다국어 README를 정리했습니다.

## 1. 리포트·텔레그램: 생성부터 근거와 전달까지

- **모델·시간 예산을 정렬했습니다.** KR/US 리포트 모델 계약과 파일명 표기를 맞추고,
  긴 Luna 보고서에는 medium 추론 설정을 보존했습니다. 평가 명령은 최근 유효 보고서를
  재사용하고 전체·에이전트·fallback 시간 예산을 구분합니다. (#618–620, #623)
- **번역 PDF 전달을 묶어 개선했습니다.** 제한된 동시성, 요청·배치 시간 제한, 완료한 번역부터
  전달하기, timeout 재시도를 적용했습니다. 번역 실패를 원문 전달 성공으로 위장하지 않습니다.
  초기 전달 개선과 후속 복구를 별도 신기능으로 중복 세지 않았습니다. (#620, #639)
- **CAN SLIM 자료와 실제 매수 입력을 맞췄습니다.** 최근 확정 분기 EPS와 전년 동기 비교를
  우선하고, 연간·누적·추정 EPS나 다른 법인 실적을 대체값으로 쓰지 않도록 했습니다.
  자료가 부족할 때만 제한적으로 보완하며, 출처·회계 범위·발표일이 없으면 UNKNOWN으로 남깁니다.
  (#713–714)
- **경쟁우위 근거를 재사용하되 확인을 생략하지 않습니다.** KR/US 보고서 사이에 근거를
  전달하고 제목·문단 형식 차이로 유실되지 않게 했습니다. 호출 근거가 없으면 실제 조사를
  요구하며, 주가의 상대적 강세를 제품 가격 결정력이나 사업 경쟁우위로 바꿔 해석하지 않습니다.
  (#715–716)
- **조회 성공·기록 없음·조회 실패를 구분합니다.** 동일 종목 청산 이력과 판단 근거의
  결측 상태를 명시하고 한국어 매매 안내를 읽기 쉽게 정리했습니다. 조회 실패를
  “청산 이력이 없다”로 확정하지 않습니다. (#722)
- **정식 리포트 실패·캐시·PDF를 함께 고쳤습니다.** 앱의 리포트는 인증된 DB KIS 조회를
  사용하며, 실패 메시지를 정상 보고서/PDF로 저장하거나 캐시에서 재사용하지 않습니다.
  정확한 원본 이름·신선도에 맞는 PDF만 짝짓고, 렌더링은 임시 파일 후 원자적으로 게시합니다.
  내부 경로·예외 원문을 사용자에게 보내지 않고 기존 이용 횟수 환불을 보존합니다. (#642, #723–724)

## 2. Codex·Astra: 분석 실행과 프로세스 수명주기

- **Codex OAuth Fast 매매 분석 경로**와 KR/US 프로필을 추가하고, 하위 프로세스의
  허용 인자·격리·시간 제한을 강화했습니다. 이 분석 경로가 브로커 주문을 대신 실행하는 것은
  아닙니다. OpenAI priority 요청의 호환 fallback도 유지했습니다. (#626, 직접 커밋 `d2a9875f`)
- **BUY와 SELL의 모델·effort·timeout 설정을 분리**했습니다. Astra 전환을 위한 경계를
  마련하되 설정 지원만으로 모든 환경의 기본 모델을 자동 교체하지 않습니다. SELL 선행,
  분석 이후 주문 적용 순서, 취소 전파와 기존 fallback을 보존했습니다. (#678–679)
- **timeout 뒤 남는 프로세스와 대용량 입력 교착을 줄였습니다.** 소유 프로세스만 종료·회수하고,
  큰 다국어 입력을 끝까지 전달해 EOF를 닫기 전에 출력 대기를 시작하던 문제를 수정했습니다.
  (#678, #693)
- **장애를 민감정보 없이 분류합니다.** 첫 이벤트, MCP 시작/완료/오류, 마지막 관측 단계,
  정리 결과를 기록하되 원문 prompt·도구 결과·계좌 식별자·stderr를 그대로 노출하지 않습니다.
  OAuth quota probe의 지원 모델과 오류 분류도 정리했습니다. (#681–682, #689)
- 실제 배치에서 timeout과 fallback이 관측됐으며, 이번 계측·교착 수정만으로 Astra 운영
  안정화나 모델 우위가 입증된 것은 아닙니다.

## 3. KR·US 주식: 후보 선택·진입·실행 계약

- **US 오전 후보 부족 시 Value-to-Cap 경로를 조건부 보충**합니다. 필요한 경우 시가총액을
  조회하고 이미 선택한 종목을 제외하며, 자료 커버리지가 충분할 때만 적용합니다.
  v2.21.0에 남았던 비활성 보충 경로를 고쳤지만, 항상 3종목을 채운다는 보장은 아닙니다. (#617)
- **트리거의 의도와 최종 레짐 cap을 지킵니다.** 전일 대비 하락을 단순 마감 회복으로
  잘못 선택하는 사례를 제한하고, KR 약세장 후보를 최종 refill에서 다시 3개로 늘리지
  않도록 했습니다. US 포트폴리오·점수·후보 한도도 실행 경계에서 확인합니다. (#624, #633–635, #643–644)
- **진입 직전 가격과 파일럿 예산을 재검증**합니다. 분석 가격을 그대로 주문하지 않고 새 조회를
  사용하며, 목표·손절을 유리하게 옮겨 통과시키지 않습니다. 일반/반등 파일럿 점수 정책을
  공통화하고 50% 파일럿 원금 상한과 정수 수량 내림을 적용합니다. 0주를 1주로 올리지 않습니다.
  (#678, #680, #684)
- **전략 판단과 브로커 사정을 분리**했습니다. 계좌 예산 부족·주문 거절 때문에 유효한 전략
  진입을 지우지 않되, 실제 주문에는 별도의 자금 검증을 유지합니다. 파일럿 소유 포지션에
  일반 피라미딩이 끼어들지 못하도록 했습니다. (#635, #692, #694)
- **9월 2일 수동 lifecycle 승인으로 기존 LIVE 미체결 추격을 복구**했습니다.
  환경변수와 lifecycle 이중 게이트를 함께
  확인하고 SHADOW/LIVE 예산과 상태 표시를 구분합니다. 거절된 정정을 성공으로 집계하지 않으며,
  직접 cron 실행의 import와 집계 쿼리도 고쳤습니다. (#640–641)

## 4. BTC 데모: 체결·정산·보호 주문의 연결

- **초기 C1 guard와 실행 관측을 보존**했습니다. 실패 사례·판단 snapshot·거래소 지연과
  제한된 demo ACK probe를 기록하고, 스윙 진입을 공개 채널에 더 자세히 알립니다.
  ACK는 확정 체결이 아닙니다. (#627, #645, 직접 관측 커밋)
- **스윙 자본·확정봉 실행·청산 정산을 정비**했습니다. 메인 데모 자본과 기존 노출·미체결 진입을
  함께 고려하고, 확정 4시간 신호를 기존 주기에서 신속히 처리합니다. 청산 완료는 로컬 가격 추정이나
  요청 수락이 아니라 확인된 Bybit 체결·정산 근거로 판단합니다. (#651, #654, #656–657)
- **부분 노출 보호와 조회 복구를 강화**했습니다. 정상 continuation 페이지·중복 flat 표현을
  처리하고, 불명·상충 조회를 확정 flat으로 바꾸지 않습니다. 보호 주문을 먼저 취소한 뒤
  새로 넣는 공백과 불필요한 동일 스탑 재발행을 줄이고, health 보고서의 자기 경보도 수정했습니다.
  (#659–661)
- **native SL과 TP의 영속 수명주기**를 연결했습니다. 진입 응답 유실·부분체결·재시작,
  TP intent·수수료·잔량·자체 감축과의 경쟁을 관리하고, 불확실한 감축을 무조건 재주문하지 않습니다.
  (#662–664)
- **메인·스윙 공통 실행 잠금과 위험 예약**을 추가하고, 9월 7일 합산 heat 6.5%·진입
  slippage 0.1%의 제한된 데모 시험을 활성화했습니다. 보호·정산 복구를 선택적 연구보다
  우선하며, 신규 진입 중단 중에도 기존 보호는 유지합니다. 실제 신규 진입·부분체결의
  전진 검증은 남아 있으며, 실자금 운용 승인이나 장기 수익성 입증은 아닙니다. (#670–671, #673–674)
- **청산 전후 상태도 기존 관측 경로에 기록**합니다. 전략·노출·스탑·트레일 snapshot은
  모드별로 분리하고 수집 실패가 매매를 중단하지 않게 했습니다. snapshot 자체를 체결
  증거나 비용·펀딩을 반영한 순손익으로 취급하지 않습니다. (#655–656)

## 5. BTC 연구: 유리한 결과만 남기지 않는 재현 도구

- **판단·편향·확정봉 팩터 관측**을 추가했습니다. 입력·설정 hash와 허용/거절 근거를
  보존하고, 확정봉·시간 순서·모드 격리·embargo를 지키는 연구 경계를 마련했습니다.
  과거 결정을 사후에 만들어 prospective 기록에 넣지 않습니다. (#652–653, 직접 연구 커밋)
- **단타 50% 부분익절·잔량 추적과 사건 기반 재생**을 구현했습니다. 펀딩 시점, 부분체결,
  보호 수정 지연, 현금과 평가자산을 함께 처리하고, 지표 계산 오류를 “거래 0건”으로
  숨기지 않습니다. 이 단타는 운영 활성화가 아닌 오프라인 후보입니다. (#665–666)
- **전략 조합·적응형 청산·MA 전환을 사전등록해 비교**했습니다. 고정 조합과 적응형 후보가
  요구한 성장·일관성·위험 기준을 충족하지 못한 결과도 보존했습니다. 비용 차감 결과가
  부정적이거나 불충분한 가설을 “최적 전략을 찾았다”로 홍보하지 않습니다. (#667–669)
- **고정 레버리지 비중·부모 주문 분할·하위 봉 경제성**을 별도 연구로 비교했습니다.
  수익·노출·낙폭의 상충관계, 5분 하위 봉과 mark/funding, 공동 회계·인과적 국면을
  재현했습니다. 분할 우위·단타 수익성은 입증하지 못했으며 자료/회계 계약이 다른 연구의
  수익 숫자를 직접 이어 붙이지 않습니다. 실주문 child 주문 활성화도 아닙니다. (#675–677)

## 6. 진입품질·매매일지·성과 관측

- **로깅 아키텍처·데이터 카탈로그 도식**을 추가하고, 기존 트리거 라우팅·RS 검증 근거를
  문서로 보존했습니다. 문서화 자체를 새 매매 기능 활성화로 세지 않습니다.
  (직접 커밋 `76fd8952`, #628)
- **후보 → 진입 → 청산/결과**에 같은 decision/position 식별자를 연결하고,
  KR enhanced 경로까지 동일한 문맥을 보존했습니다. 레짐·스윙·정책/설정 버전과
  관측 커버리지를 함께 추적합니다. (#621–622)
- **US 진입품질과 KR/US 매매일지 영향 CAPTURE**를 추가했습니다. 판단 당시 입력,
  실제 언급 여부, 결정론적 점수 조정과 기준선 통과 변화 등을 기록합니다. 이는 매매일지가
  수익성을 높였다는 인과 증명이나 새 진입 게이트가 아닙니다. (8월 29일–9월 1일 직접 커밋 묶음)
- **결정론적 Evidence Packet·분석 skill·하네스**를 마련했습니다. 결측은 실패/통과가
  아닌 MISSING이며, 정확한 ID와 출처·시점이 없는 이력을 추정해 연결하지 않습니다.
  과거 백필과 전진 관측을 구분합니다.
- **전략 성과와 실제 체결 증거를 분리**했습니다. 브로커 거절·부분체결·불명 상태 때문에
  유효한 전략 원장 청산을 분석에서 제거하지 않습니다. 반대로 전략 수익률을 브로커 실현
  손익이라고 부르지도 않습니다. US 청산 표시와 경과 보유 시간도 바로잡았습니다. (#683, #686)
- **추세·ADX·noise-stop 연구와 관측 연결을 보강**했습니다. 원본 행·기준시각·설정
  fingerprint를 남기고, 재구성 연구를 독립 holdout으로 취급하지 않습니다. 완료 배치와
  고유 거래 세션을 구분하고 KR 관측·후속 성과 연결을 복구했습니다. (#687, #696, #720)
- **약세장 제3슬롯은 주문 없는 반사실 관측**입니다. 실제 cap은 유지합니다.
  새 스크리닝 점수 도입 이후에는 관측 버전을 나눠 집계하고, 기존 평가의 후속 성과는
  원래 버전에 귀속합니다. 이전 로그를 버리지 않습니다. (#649, #727)

## 7. 전략 원장·초분할: 계좌와 독립된 연구 기반

- **초분할 순수 코어와 US scout SHADOW**를 추가했습니다. 목표 단계·레짐 cap·정수주식
  투영을 검증하고, 초기 0→10% scout 및 비교 Packet/CLI를 마련했습니다.
  초기 단계표를 실거래 분할 규칙으로 자동 활성화하지 않습니다. (#636–638, #646–647)
- **금액형 초안을 슬롯·정규화 비중 원장 v2로 교체**했습니다. 전략 ID를 계좌 자금·브로커
  실행과 분리하고 가중평단·누적 투입·잔여 배분·평가 노출을 구분합니다. 과거 v1 원장을
  묵시적으로 변환하지 않으며, 투영에는 명시적인 이전 목표를 요구합니다. (#687–688, #690)
- **50→100 SHADOW 파일럿 수명주기**를 구현했습니다. 최초 50%, 이후 세션의 재확인과
  잔여 배분, WAIT·취소·만료·청산을 원자적 상태 전이와 멱등 이벤트로 처리합니다.
  운영 계좌의 자동 추가매수를 켰다는 의미는 아닙니다. (#691–692, #694)
- **발생 시점의 알림과 누적 실행 증거를 보존**합니다. 승인된 테스트 채팅에 한정해
  자동 전송 시도를 최대 한 번으로 제한하며, 응답이 불명하면 UNKNOWN으로 남깁니다.
  “정확히 한 번 도착”을 보장하는 시스템이라고 표현하지 않습니다. (#695, #697)

## 8. 격리 진단: 주문 없이 실제 분석 경로를 검증하는 기반

- **자격증명 로딩을 계좌 접근 시점으로 지연**하고, ambient broker/MCP 설정을 읽지 않는
  명시적 가상 에이전트 초기화를 추가했습니다. 자격증명 없이 import된다고 실제 주문
  안전 검사가 생략되는 것은 아닙니다. (#698–699)
- **고정된 읽기 전용 진단 전송과 등록 케이스**를 구현했습니다. 합성 SQLite,
  제한된 모델 전송, 읽기 전용 소스, 고정 실행 파일·인자, parent lease로 실행 범위를
  제한합니다. 감독자 종료나 연결 단절 때 소유 자식을 정리합니다. (#700–704)
- **케이스 claim·단계별 정리 증거·EOF 종료 유예**를 보존합니다. 정리를 확인하지 못한
  상태를 성공으로 바꾸거나 과거 UNKNOWN을 지우지 않습니다. (#704, #706, #712)
- **no-order 원장 효과·청산 비교·이력 복구 연결부는 기본 비활성**입니다.
  출처·시각·해시에 결합된 입력으로 가상 효과와 정상 기업 상태 처리를 보강했지만,
  `EFFECTS_RUNTIME_ENABLED=False`를 유지합니다. (#705, #707, #710–711)
- **운영자 처분과 증거 보관은 오프라인 절차**입니다. 미해결 케이스를 자동 재시도하지 않고
  보수적인 호스트 정지 상태 관측을 남깁니다. 이 기반의 테스트 성공은 자연 발생
  종단 SHADOW 성과나 실거래 실행 완료를 뜻하지 않습니다. (#708–709)

## 9. 시장 데이터·수급·스크리닝 숫자의 정직성

- **한국 배치에서 대화형 KRX 인증과 레거시 공급자 경로를 정리**하고 KIS 중심의
  명시적 자료 계약을 적용했습니다. 마스터 종목명·내부 식별자·개별 이력 누락 재시도와
  장애 안내를 보강했으며, KIS 실패를 가공 값으로 채우지 않습니다. (#717–719, #721)
- **앱 리포트는 기존 인증 API·터널로 DB KIS를 읽습니다.** 가격·지수·시총·수급 등
  허용된 조회만 노출하고 요청/응답 크기와 실행 시간을 제한합니다. 앱에 브로커 비밀키를
  복사하거나 주문 API를 추가하지 않습니다. (#723–724)
- **US는 보유 스냅샷과 가격·거래량 보조 근거를 분리**합니다. 최근 5/20세션 상승·하락
  거래량 비율(SVR)과 20세션 종가 위치·거래량 지표(CMF)를 계산하되 기관 순매수라고 부르거나
  상관된 신호에 중복 가점을 주지 않습니다. (#725)
- **KR은 외국인·기관의 5/20/30거래일 순매수 수량**을 계산합니다. 단위·시작/종료일·
  관측 수·결측과 정규화 가능 여부를 표시하며, 미확정 당일·가격 누락·실제 순매수 금액을
  혼동하지 않습니다. (#725)
- **가공 손익비를 단계적으로 기록에서 분리한 뒤 계산에서도 제거**했습니다.
  +15% 목표 보정과 고정 손절로 만들던 스크리닝 손익비·만점 agent 점수를 삭제하고,
  기존 모멘텀·RS·과열도 가중치만 재정규화했습니다. 목표·손절·R/R은 실제 BUY 시나리오 전까지
  미확정이며, 가까운 고점을 새 전역 탈락 게이트로 만들지 않았습니다. 탑다운 순위는 달라질 수
  있습니다. (#726–727)
- **US 배치의 실제 MultiIndex 시세 구조를 처리**합니다. 단일 종목이라도 High가 DataFrame으로
  돌아오는 오류를 재현해 공급자 경계에서 정규화했습니다. 요청 종목을 정확히 선택하고
  중복/모호한 열은 결측으로 처리하며, KR 소비자에도 같은 방어를 적용했습니다. (#634, #728)

## 10. 운영·알림·검증 하네스

- **가변 운영 데이터와 코드의 저장 위치를 분리**했습니다. runtime/cache와 SQLite WAL/SHM을
  잘못 커밋하지 않게 하고, backup 실행 권한·clean ff-only 배포 절차를 정리했습니다.
  기존 데이터·자격증명을 reset/clean으로 덮어쓰지 않습니다. (8월 29일–31일 직접 커밋 묶음)
- **로드맵과 만료 상태를 실제 동작에 연결**했습니다. BTC는 구현·검증·배포·전진 관측·수익성
  단계를 나누고, 만료된 비전 품질 SHADOW와 lifecycle 상태를 존중합니다. 승인 리뷰 수와
  PR/검증 요건은 다른 경계로 취급합니다. (#648, #658, #664)
- **모호한 Telegram 전달을 자동 재전송하지 않습니다.** 응답 유실·부분 전달·만료 lease를
  UNKNOWN/보류로 남기고, 명시적 rate-limit 거절과 구분합니다. 이는 중복 위험을 줄이는
  정책이며, 모든 메시지의 도착을 보장하거나 실제 미전달을 자동 판별하는 것은 아닙니다. (#720)
- **배포 검증을 기억이 아니라 작업 절차와 CI에 연결**했습니다. 기능/장애/배포 요청은
  하네스를 적용하고, 실제 KR 소비자 및 US 오전·오후 배치→JSON의 평탄/MultiIndex/결측
  입력을 검사합니다. 서버 읽기 전용 스모크와 첫 정규 배치 완료는 별도로 보고합니다.
  CI 검사는 자동이며 서버 스모크는 담당자가 실행해야 하는 완료 조건입니다. (#728–729)

## 11. Priso·다국어 프로젝트 소개

- **Priso 마스코트 v1.0**의 이미지 자산과 manifest·정체성/사용 가이드를 추가했습니다. (#625)
- 영어·한국어·일본어·중국어·스페인어 README의 프로젝트 로고 옆에 Priso를 배치했습니다. (#630)
- Anthropic 배지를 Sonnet 5 표기로 정리했습니다. 소개 문구 변경을 별도의 런타임 모델
  전환으로 세지 않습니다. (#631)

## 개발자용 상세 — 동일 가중치 커밋 집계

`v2.21.0..512ed3de`의 **331개 커밋을 모두 오래된 순서부터 확인**했습니다.
각 커밋은 1표이며 날짜·최근성·변경 줄 수·작성자·PR 크기에 추가 가중치를 주지 않았습니다.
비병합 커밋은 주된 목적 하나에만 배정하고, 병합 커밋은 별도로 집계했습니다.
백포트·테스트·lint·재시도 수정도 감사에서 빠뜨리지 않되, 본문에서는 같은 작업으로 묶었습니다.
PR이 없는 직접 커밋 **20개**도 포함했습니다. 글의 길이는 기능 설명과 위험 구분에 필요한 만큼이며
커밋 수가 수익성·완성도·중요도 점수라는 뜻은 아닙니다.

<details>
<summary>331개 커밋의 주제별 집계 펼치기</summary>

| 주제 | 동일 가중치 커밋 수 | 핵심 범위 |
|---|---:|---|
| 리포트·봇·근거 전달 | 23 | 모델·번역·평가 응답, CAN SLIM/경쟁 근거, PDF·실패 처리 |
| Codex·Astra 실행 경로 | 11 | 분리 설정, subprocess 수명주기, timeout·stdin·민감정보 없는 계측 |
| 주식 매매·실행 계약 | 15 | 점수·예산·가격·파일럿·fill chaser·전략/계좌 경계 |
| 진입품질·매매일지·관측 | 20 | 연결 ID, CAPTURE, Evidence Packet, journal·성과 근거 |
| 전략 원장·초분할 기반 | 13 | scout 관측, 슬롯 원장 v2, SHADOW 파일럿, 동결 알림 |
| 격리 진단·주문 없는 검증 | 25 | 가상 초기화, 고정 전송, 감독·정리 증거, 비활성 효과 연결부 |
| BTC 실행 안전성·데모 운영 | 25 | 체결·정산·보호 주문, TP/SL, 공유 실행·위험 예약 |
| BTC 오프라인 연구 | 30 | 편향·팩터·사건 재생, 조합·비중·분할·하위 봉 비교 |
| 시장 데이터·배치 입력 | 7 | KIS 전환·마스터·이력 재시도, US 열 구조·스냅샷 |
| 스크리닝·수급 입력 | 22 | 후보 보충·레짐 cap, 수급 구간, 가공 손익비 제거 |
| 운영·검증 거버넌스 | 11 | 런타임 데이터 격리, 로드맵, 만료·배포 검증 하네스 |
| Priso·프로젝트 소개 | 3 | 마스코트·사용 규칙·다국어 README |
| 병합 커밋 | 126 | PR 병합 109개 + 동기화·백포트 계보 병합 17개 |
| **합계** | **331** | **비병합 205개 + 병합 126개, 중복 분류 없음** |

</details>

<details>
<summary>오래된 작업 누락 점검: 기간별 집계</summary>

| 작성일 구간 | 비병합 | 병합 | 합계 |
|---|---:|---:|---:|
| 2026-08-27–2026-09-04 | 61 | 37 | 98 |
| 2026-09-05–2026-09-07 | 48 | 24 | 72 |
| 2026-09-09–2026-09-10 | 51 | 38 | 89 |
| 2026-09-11–2026-09-15 | 45 | 27 | 72 |
| **합계** | **205** | **126** | **331** |

</details>

전체 SHA·작성일·제목·단일 분류·PR 연결과 PR별 규모는
[릴리즈 감사 자료](https://github.com/dragon1086/prism-insight/blob/v2.22.0/docs/release_audits/v2.22.0.json)에 있습니다.
PR 연결은 제목 추측이 아니라 병합 부모 간 Git 도달 가능성을 기준으로 확인했습니다.

## 개발자용 상세 — PR별 변경 규모

아래는 **PR 번호 오름차순의 전체 109개 목록**입니다. 번호가 비어 있는 PR은 이 범위에
병합된 것으로 임의 포함하지 않았습니다. 규모는 GitHub PR 메타데이터 참고값이며 가중치가 아닙니다.
직접 커밋과 PR이 아닌 병합도 위 집계에는 포함됩니다.

<details>
<summary>이번 범위의 PR 109개 전체 목록 펼치기</summary>

| PR | 주제 | 규모 |
|---|---|---|
| [#617](https://github.com/dragon1086/prism-insight/pull/617) | fix(us): fill short morning batches with Value-to-Cap | 3 files, +255/−30 |
| [#618](https://github.com/dragon1086/prism-insight/pull/618) | feat: align report models and expose swing regime | 23 files, +438/−57 |
| [#619](https://github.com/dragon1086/prism-insight/pull/619) | fix: keep medium reasoning for long reports | 3 files, +6/−5 |
| [#620](https://github.com/dragon1086/prism-insight/pull/620) | fix: bound translated report delivery latency | 5 files, +526/−125 |
| [#621](https://github.com/dragon1086/prism-insight/pull/621) | feat: record linked trading context lifecycle | 22 files, +1,353/−25 |
| [#622](https://github.com/dragon1086/prism-insight/pull/622) | fix: link enhanced trading context lifecycle | 2 files, +69/−5 |
| [#623](https://github.com/dragon1086/prism-insight/pull/623) | fix: bound telegram evaluation latency | 8 files, +639/−86 |
| [#624](https://github.com/dragon1086/prism-insight/pull/624) | fix: reject downside closing-strength recoveries | 6 files, +150/−2 |
| [#625](https://github.com/dragon1086/prism-insight/pull/625) | feat: add Priso mascot assets | 8 files, +70/−0 |
| [#626](https://github.com/dragon1086/prism-insight/pull/626) | feat: integrate Codex Fast trading analysis | 15 files, +1,519/−119 |
| [#627](https://github.com/dragon1086/prism-insight/pull/627) | feat(btc): preserve C1 guard and research evidence | 39 files, +4,760/−277 |
| [#628](https://github.com/dragon1086/prism-insight/pull/628) | docs: preserve validated trading evidence | 3 files, +556/−1 |
| [#629](https://github.com/dragon1086/prism-insight/pull/629) | test: align rebound pilot fixtures with final gate | 3 files, +21/−3 |
| [#630](https://github.com/dragon1086/prism-insight/pull/630) | docs: place Priso beside the project logo | 5 files, +35/−21 |
| [#631](https://github.com/dragon1086/prism-insight/pull/631) | docs: update Anthropic badge to Sonnet 5 | 5 files, +10/−10 |
| [#633](https://github.com/dragon1086/prism-insight/pull/633) | fix(us): enforce entry risk contracts | 5 files, +384/−18 |
| [#634](https://github.com/dragon1086/prism-insight/pull/634) | fix(us): isolate malformed trigger snapshots | 3 files, +213/−30 |
| [#635](https://github.com/dragon1086/prism-insight/pull/635) | fix(us): preserve ledger and execution separation | 3 files, +2/−156 |
| [#636](https://github.com/dragon1086/prism-insight/pull/636) | feat(core): add micro-split phase one contract | 7 files, +716/−0 |
| [#637](https://github.com/dragon1086/prism-insight/pull/637) | feat(us): capture micro-split scout shadow | 13 files, +448/−11 |
| [#638](https://github.com/dragon1086/prism-insight/pull/638) | fix(ops): detect micro-split cron shadow gate | 2 files, +15/−2 |
| [#639](https://github.com/dragon1086/prism-insight/pull/639) | fix: recover translated PDF timeouts | 7 files, +325/−55 |
| [#640](https://github.com/dragon1086/prism-insight/pull/640) | fix: restore effective fill-chaser LIVE state | 7 files, +202/−37 |
| [#641](https://github.com/dragon1086/prism-insight/pull/641) | fix: honor fill-chaser lifecycle from direct cron entrypoint | 2 files, +81/−5 |
| [#642](https://github.com/dragon1086/prism-insight/pull/642) | fix: sanitize KR failure alerts and harden portfolio delivery | 5 files, +220/−26 |
| [#643](https://github.com/dragon1086/prism-insight/pull/643) | fix(kr): enforce weak-regime selection hard cap | 3 files, +106/−4 |
| [#644](https://github.com/dragon1086/prism-insight/pull/644) | test(kr): isolate weak-regime flag from server env | 1 files, +9/−5 |
| [#645](https://github.com/dragon1086/prism-insight/pull/645) | fix(btc): deliver detailed swing entries to public channel | 4 files, +342/−16 |
| [#646](https://github.com/dragon1086/prism-insight/pull/646) | feat: add micro-split phase 2a evidence replay packet | 10 files, +860/−15 |
| [#647](https://github.com/dragon1086/prism-insight/pull/647) | fix: bootstrap micro-split evidence CLI | 2 files, +43/−1 |
| [#648](https://github.com/dragon1086/prism-insight/pull/648) | fix(vision): honor expired buy-quality shadow | 6 files, +92/−16 |
| [#649](https://github.com/dragon1086/prism-insight/pull/649) | feat(kr): observe weak-regime third-slot candidate | 12 files, +1,423/−8 |
| [#651](https://github.com/dragon1086/prism-insight/pull/651) | fix(btc): reconcile swing exits with Bybit settlement | 2 files, +424/−59 |
| [#652](https://github.com/dragon1086/prism-insight/pull/652) | feat(btc): capture causal OHLCV factor evidence | 11 files, +802/−4 |
| [#653](https://github.com/dragon1086/prism-insight/pull/653) | fix(btc): isolate factor evidence by mode | 3 files, +31/−5 |
| [#654](https://github.com/dragon1086/prism-insight/pull/654) | fix(btc): isolate swing capital and remove 30m execution delay | 5 files, +250/−10 |
| [#655](https://github.com/dragon1086/prism-insight/pull/655) | feat(btc): ClickStack exit state snapshots without execution changes | 4 files, +169/−0 |
| [#656](https://github.com/dragon1086/prism-insight/pull/656) | fix(btc): execution-confirmed reductions and preserve native protection | 5 files, +298/−11 |
| [#657](https://github.com/dragon1086/prism-insight/pull/657) | fix(btc): safe pending reconciliation and profitability evidence contract | 11 files, +973/−23 |
| [#658](https://github.com/dragon1086/prism-insight/pull/658) | docs(btc): roadmap and development review governance | 3 files, +197/−0 |
| [#659](https://github.com/dragon1086/prism-insight/pull/659) | fix(btc): stop false protection failures on normal Bybit pagination | 6 files, +118/−11 |
| [#660](https://github.com/dragon1086/prism-insight/pull/660) | feat(btc): verified partial-exposure protection and in-place stop updates | 10 files, +1,076/−42 |
| [#661](https://github.com/dragon1086/prism-insight/pull/661) | fix(btc): prevent error-burst alerts from counting themselves | 3 files, +63/−2 |
| [#662](https://github.com/dragon1086/prism-insight/pull/662) | fix(btc): attach native entry stops with exact recovery fences | 13 files, +1,807/−95 |
| [#663](https://github.com/dragon1086/prism-insight/pull/663) | feat(btc): durable TP settlement and execution diagnostics | 7 files, +1,486/−35 |
| [#664](https://github.com/dragon1086/prism-insight/pull/664) | docs(btc): record TPSL deployment and authorized review policy | 5 files, +146/−0 |
| [#665](https://github.com/dragon1086/prism-insight/pull/665) | feat(btc): offline 50% scalp exits and adaptive monitoring policy | 5 files, +686/−1 |
| [#666](https://github.com/dragon1086/prism-insight/pull/666) | feat(btc): strict execution replay and causal historical exit benchmark | 9 files, +1,768/−1 |
| [#667](https://github.com/dragon1086/prism-insight/pull/667) | research(btc): shared-capital strategy mixtures with frozen validation gates | 9 files, +2,761/−1 |
| [#668](https://github.com/dragon1086/prism-insight/pull/668) | research(btc): validate swing/scalp exits with shared-capital replay | 12 files, +2,687/−1 |
| [#669](https://github.com/dragon1086/prism-insight/pull/669) | research(btc): model MA convergence and multi-timeframe transitions | 17 files, +2,817/−17 |
| [#670](https://github.com/dragon1086/prism-insight/pull/670) | fix(btc): complete non-scalp execution recovery and shared admission | 24 files, +3,587/−52 |
| [#671](https://github.com/dragon1086/prism-insight/pull/671) | docs(btc): record verified non-scalp safety deployment | 2 files, +28/−2 |
| [#673](https://github.com/dragon1086/prism-insight/pull/673) | feat(btc): controlled shared-risk demo trial activation | 9 files, +931/−15 |
| [#674](https://github.com/dragon1086/prism-insight/pull/674) | docs(btc): record demo 6.5% / 0.1% policy activation | 2 files, +39/−7 |
| [#675](https://github.com/dragon1086/prism-insight/pull/675) | research(btc): validate confidence-weighted account allocation at fixed leverage | 10 files, +1,649/−1 |
| [#676](https://github.com/dragon1086/prism-insight/pull/676) | research(btc): compare bounded split-entry execution | 9 files, +1,525/−4 |
| [#677](https://github.com/dragon1086/prism-insight/pull/677) | research(btc): 5m economic replay and causal regime revalidation | 17 files, +3,713/−2 |
| [#678](https://github.com/dragon1086/prism-insight/pull/678) | fix: Astra BUY 운영 전 안전 보강 | 25 files, +1,973/−101 |
| [#679](https://github.com/dragon1086/prism-insight/pull/679) | feat: Astra SELL 전환 설정 분리 | 7 files, +222/−21 |
| [#680](https://github.com/dragon1086/prism-insight/pull/680) | fix(trading): restore analysis notice on KR fresh quote rejection | 2 files, +19/−0 |
| [#681](https://github.com/dragon1086/prism-insight/pull/681) | fix(llm): add safe Codex failure diagnostics | 3 files, +112/−5 |
| [#682](https://github.com/dragon1086/prism-insight/pull/682) | fix(ops): restore ChatGPT quota health probe | 2 files, +93/−4 |
| [#683](https://github.com/dragon1086/prism-insight/pull/683) | fix(us): clarify strategy exit notices and preregister entry review | 3 files, +131/−4 |
| [#684](https://github.com/dragon1086/prism-insight/pull/684) | fix(trading): unify entry floor policy and enforce pilot budget caps | 20 files, +888/−202 |
| [#686](https://github.com/dragon1086/prism-insight/pull/686) | fix(analysis): separate strategy ledger returns from broker execution | 8 files, +349/−61 |
| [#687](https://github.com/dragon1086/prism-insight/pull/687) | feat: trend research validation and independent split-entry ledger | 27 files, +2,788/−9 |
| [#688](https://github.com/dragon1086/prism-insight/pull/688) | fix(ledger): block projections with unknown prior target | 3 files, +7/−1 |
| [#689](https://github.com/dragon1086/prism-insight/pull/689) | fix: safe Astra runtime stage telemetry and process validation | 5 files, +404/−35 |
| [#690](https://github.com/dragon1086/prism-insight/pull/690) | fix: independent slot-weight strategy ledger v2 | 10 files, +657/−276 |
| [#691](https://github.com/dragon1086/prism-insight/pull/691) | feat: SHADOW-only 50-to-100 pilot lifecycle | 5 files, +820/−99 |
| [#692](https://github.com/dragon1086/prism-insight/pull/692) | fix: separate pilot ownership from legacy pyramid orders | 8 files, +457/−100 |
| [#693](https://github.com/dragon1086/prism-insight/pull/693) | fix: eliminate large-prompt Codex stdin deadlock | 6 files, +255/−16 |
| [#694](https://github.com/dragon1086/prism-insight/pull/694) | fix: decouple strategy pilot entries from broker cash | 13 files, +368/−186 |
| [#695](https://github.com/dragon1086/prism-insight/pull/695) | feat: durable test-only strategy ledger outbox | 4 files, +706/−4 |
| [#696](https://github.com/dragon1086/prism-insight/pull/696) | research: reproducible actual-data ADX and noise-stop diagnostics | 30 files, +3,404/−11 |
| [#697](https://github.com/dragon1086/prism-insight/pull/697) | feat: guarded test-chat strategy notice transport | 5 files, +496/−1 |
| [#698](https://github.com/dragon1086/prism-insight/pull/698) | refactor: defer broker configuration until account access | 5 files, +196/−6 |
| [#699](https://github.com/dragon1086/prism-insight/pull/699) | feat: explicit isolated agent initialization and fallback contexts | 10 files, +992/−41 |
| [#700](https://github.com/dragon1086/prism-insight/pull/700) | feat: isolated read-tool and fallback transport diagnostics | 15 files, +4,552/−0 |
| [#701](https://github.com/dragon1086/prism-insight/pull/701) | feat: fixed invocation and readonly diagnostic agent transports | 6 files, +1,626/−0 |
| [#702](https://github.com/dragon1086/prism-insight/pull/702) | feat: owned parent lifecycle for isolated agent diagnostics | 9 files, +893/−9 |
| [#703](https://github.com/dragon1086/prism-insight/pull/703) | feat: registered isolated BUY and SELL analysis cases | 6 files, +809/−8 |
| [#704](https://github.com/dragon1086/prism-insight/pull/704) | feat: durable whole-case supervision for isolated diagnostics | 4 files, +892/−1 |
| [#705](https://github.com/dragon1086/prism-insight/pull/705) | feat: disabled no-order ledger effects and fast-stop comparison | 6 files, +892/−0 |
| [#706](https://github.com/dragon1086/prism-insight/pull/706) | feat: durable safe diagnostic stage and cleanup receipts | 4 files, +448/−30 |
| [#707](https://github.com/dragon1086/prism-insight/pull/707) | feat: disabled no-order seams in KR and US agent pipelines | 8 files, +746/−40 |
| [#708](https://github.com/dragon1086/prism-insight/pull/708) | feat: offline operator disposition with immutable unknown cases | 3 files, +529/−5 |
| [#709](https://github.com/dragon1086/prism-insight/pull/709) | feat: immutable operator evidence and conservative host probe | 4 files, +455/−26 |
| [#710](https://github.com/dragon1086/prism-insight/pull/710) | feat: disabled strategy history, HOLD marks and receipt-only repair | 9 files, +610/−29 |
| [#711](https://github.com/dragon1086/prism-insight/pull/711) | fix: accept actual normal corporate status in disabled shadow context | 2 files, +21/−1 |
| [#712](https://github.com/dragon1086/prism-insight/pull/712) | fix: preserve helper EOF finalization before signalling | 3 files, +85/−3 |
| [#713](https://github.com/dragon1086/prism-insight/pull/713) | fix: preserve report evidence, leader context and bar finality | 9 files, +226/−14 |
| [#714](https://github.com/dragon1086/prism-insight/pull/714) | fix: trace KR buy evidence gaps and permit bounded supplementation | 4 files, +136/−14 |
| [#715](https://github.com/dragon1086/prism-insight/pull/715) | fix: reuse sourced competitive evidence in KR/US report inputs | 10 files, +546/−170 |
| [#716](https://github.com/dragon1086/prism-insight/pull/716) | fix: prevent missing competitive evidence from bypassing discovery | 6 files, +108/−22 |
| [#717](https://github.com/dragon1086/prism-insight/pull/717) | fix: prevent KRX authentication stalls from blocking KR batches | 7 files, +163/−12 |
| [#718](https://github.com/dragon1086/prism-insight/pull/718) | fix: complete KIS-only Korean market-data migration | 73 files, +1,951/−4,231 |
| [#719](https://github.com/dragon1086/prism-insight/pull/719) | fix: resolve KIS names through master and internal product IDs | 3 files, +62/−1 |
| [#720](https://github.com/dragon1086/prism-insight/pull/720) | fix: monitoring evidence linkage and safe Telegram delivery | 23 files, +982/−116 |
| [#721](https://github.com/dragon1086/prism-insight/pull/721) | fix: KIS single-ticker history recovery and correct outage alert | 5 files, +162/−12 |
| [#722](https://github.com/dragon1086/prism-insight/pull/722) | fix: Korean evidence notices and explicit strategy history inputs | 9 files, +402/−3 |
| [#723](https://github.com/dragon1086/prism-insight/pull/723) | fix: route app reports through DB KIS and suppress diagnostic reports | 20 files, +995/−41 |
| [#724](https://github.com/dragon1086/prism-insight/pull/724) | fix: prevent stale diagnostic PDF reuse after successful report retry | 3 files, +158/−25 |
| [#725](https://github.com/dragon1086/prism-insight/pull/725) | feat: align US flow proxies and KR investor windows with dated evidence | 17 files, +1,109/−28 |
| [#726](https://github.com/dragon1086/prism-insight/pull/726) | fix: distinguish KR/US screening proxy from scenario R/R | 10 files, +277/−8 |
| [#727](https://github.com/dragon1086/prism-insight/pull/727) | fix: remove fabricated +15% screening R/R in KR and US | 15 files, +422/−422 |
| [#728](https://github.com/dragon1086/prism-insight/pull/728) | fix: US afternoon batch MultiIndex OHLCV crash | 12 files, +279/−2 |
| [#729](https://github.com/dragon1086/prism-insight/pull/729) | chore: make deployment verification an explicit harness gate | 4 files, +49/−2 |

</details>

## 검증

- 감사 자료의 커밋 집합을 실제 `git rev-list`와 대조하고, 331개 전부의 단일 분류와
  기간·주제별 합계, 병합 PR 109개의 SHA·메타데이터를 확인했습니다.
- 이전 릴리즈 형식의 개요·기능 묶음·상세 집계·PR 목록·업데이트·한계·한/영 공지를 유지했습니다.
  최근 몇 건의 회고나 PR 제목 나열만으로 본문을 대신하지 않았습니다.
- 기능별 문서의 당시 검증과 이번 릴리즈 문서 검증을 구분합니다. 과거 테스트 수를 합산해
  현재 전체 저장소가 무결하다고 주장하지 않습니다. 릴리즈 PR도 정확한 head의 CI를 확인한 뒤 발행합니다.
- US 열 구조 장애는 수정 전 운영 시세로 재현했고, 수정 후 같은 10일 값 배열을 보존한 채
  정상 실행을 확인했습니다. 실제 모듈의 오전·오후 통합 회귀를 CI에 포함했습니다.
- 테스트·안전한 스모크는 실패한 운영 회차의 전체 보고서·주문·채널 발송을 다시 실행했다는
  의미가 아니며, 연구/SHADOW 결과가 수익성 또는 실체결의 증명도 아닙니다.

## 업데이트 방법

먼저 운영 설정·원장과 코드의 변경 상태를 확인하고, 검증된 태그를 가져옵니다.

```bash
git status --short --branch
git fetch origin --tags
git show --no-patch --oneline v2.22.0
```

- 실제 배포는 `docs/SERVER_GIT_OPERATIONS_ko.md`에 따라 clean 대상에 확인한 commit을
  fast-forward하고 변경 범위별 검증을 수행합니다. 이 명령 예시는 자동 업그레이드 스크립트가 아닙니다.
- 한국 데이터 경로에는 유효한 KIS 설정이 필요합니다. 기존 KRX/FDR/Naver fallback에 의존한
  배포는 새 자료 계약을 확인해야 합니다. 원격 앱 경로는 기존 인증/터널과 출처 의미를 보존해야 합니다.
- 앱 서버처럼 필요한 수정만 검증해 백포트하는 환경에는 전체 main 변경을 한 번에 덮어쓰지 않습니다.
- 원장 v1→v2, SHADOW→LIVE, BTC 데모→실자금은 태그 업데이트에 포함되는 자동 전환이 아닙니다.
  운영 DB·자격증명·과거 관측을 Git 명령으로 되돌리거나 재작성하지 않습니다.

## 참고 사항과 알려진 한계

- **태그에 코드가 포함됨, 기능이 기본 활성임, 운영 검증 완료, 수익성 입증은 서로 다릅니다.**
- 진입품질은 표본·결측·추적 시점의 제약이 남습니다. 전략 원장 수익과 브로커 실현 손익을
  분리하며, 기존 일반 후보 tracker를 정확한 7/14/30거래일 종가 연구로 간주하지 않습니다.
- 초분할/50→100 기반과 격리 no-order 효과는 실거래 자동 승급이 아닙니다. 비활성 효과
  연결부, 승인된 테스트 채팅 범위와 각 연구의 관측 상태를 유지합니다.
- BTC 실행 보강과 공유 위험 시험은 **Bybit demo 범위**입니다. 단타·분할·비중·국면 연구의
  기각/불충분 결과는 그대로이며 tick/주문장·대기열·실제 전진 성과의 공백이 남습니다.
- US SVR/CMF와 보유 자료는 기관의 실제 일별 순매수가 아닙니다. KR 누적은 **주식 수량**이며
  현재가를 곱한 값을 실제 순매수 금액으로 부르지 않습니다.
- 스크리닝의 +15% 가공 보정은 제거했지만 BUY의 별도 목표가 fallback 전체를 재설계한 것은
  아닙니다. 과거 고점을 돌파한 종목을 자동 탈락시키지 않으며, 미래 가격/수익을 보장하지 않습니다.
- KR 매크로/역발상 후보의 점수 컬럼 대소문자에 따른 재점수 경로 차이는 별도 검토 과제입니다.
  모든 추가 경로가 표준 여섯 트리거와 완전히 같아졌다고 주장하지 않습니다.
- UNKNOWN 전달은 실제 미전달과 다릅니다. 중복 방지를 위해 수동 확인이 필요한 사례가 남습니다.
- 실행 때 종료하는 기존 스크립트형 테스트와 과거 공급자 계약을 기대하는 테스트가 있어,
  저장소 전체 pytest를 한 번에 실행한 숫자 대신 관련 회귀·CI·운영 검증 범위를 명시합니다.

## 텔레그램 공지

### 한국어

```text
🚀 PRISM-INSIGHT v2.22.0 — 전략 원장 · BTC 안전성 · 분석·운영 신뢰성

8월 27일 이후의 331개 커밋과 109개 PR을 작업 단위로 묶었습니다. 최근 수정뿐 아니라 초반 개발부터 연구·운영 개선까지 함께 담았습니다.

📑 리포트·봇
· KR/US 모델·번역 PDF·평가 시간 제한 정비
· 분기 EPS·경쟁우위 근거 보강, 실패 PDF·내부 경로 노출 차단
· Priso 마스코트와 다국어 프로젝트 소개

⚙️ 매매 분석·실행
· Codex OAuth Fast와 BUY/SELL 독립 설정, timeout·프로세스 정리 강화
· 진입 직전 가격·점수·파일럿 예산 검증, 미체결 추격 상태 정합성
· US 오전 후보 보충, KR 레짐별 후보 상한, 가공 +15% 손익비 제거

📚 전략 원장·관측
· 후보→진입→청산 연결과 매매일지 영향 기록
· 계좌 자금·브로커 체결과 전략 성과 분리
· 초분할·50→100 파일럿 기반 및 주문 없는 격리 검증
※ CAPTURE·SHADOW·비활성 도구를 실거래 활성화로 보지 않습니다.

₿ BTC
· 데모 체결·정산·부분 노출 보호, native SL·TP 수명주기와 공유 위험 관리
· 사건 재생·전략 조합·비중·분할·하위 봉 연구를 재현하고, 입증되지 않은 결과도 보존
※ 실자금 전환이나 단타 수익성 입증은 아닙니다.

🛡️ 데이터·운영
· 한국 KIS 경로 정리, 인증된 원격 리포트 조회
· 미국 가격·거래량 보조 지표와 한국 5/20/30거래일 순매수 수량
· 미국 배치 MultiIndex 오류 수정, 모호한 알림 재전송 방지, 통합 검증 하네스

릴리즈노트:
https://github.com/dragon1086/prism-insight/releases/tag/v2.22.0

가상 운용·연구 및 소프트웨어 변경 안내이며 투자 권유가 아닙니다.
```

### English

```text
🚀 PRISM-INSIGHT v2.22.0 — Strategy ledgers · BTC safety · Analysis and operations

This release groups 331 commits and 109 PRs since August 27 into workstreams, covering early development as well as recent fixes.

📑 Reports and bots
· Aligned KR/US report models, translated-PDF delivery and evaluation time budgets
· Better quarterly EPS and competitive evidence; safer failed-report handling
· Priso mascot assets and multilingual project introductions

⚙️ Trading analysis and execution
· Codex OAuth Fast, independent BUY/SELL settings and safer timeout/process cleanup
· Fresh-quote, score and pilot-budget checks; consistent fill-chaser state
· Conditional US morning candidate capacity fill, KR regime caps and removal of fabricated +15% screening R/R

📚 Ledgers and observability
· Linked candidate, entry and exit evidence with journal influence
· Strategy results separated from broker funding and fill confirmation
· Micro-split and 50→100 pilot foundations, plus isolated no-order diagnostics
CAPTURE, SHADOW and disabled tools do not mean live trading activation.

₿ BTC
· Demo execution/settlement, partial-exposure protection, native SL/TP lifecycles and shared risk controls
· Reproducible event replay, strategy-mixture, allocation, split-entry and intrabar studies, including rejected hypotheses
No real-funds rollout or proof of scalp profitability is claimed.

🛡️ Data and operations
· KIS-based Korean data and authenticated remote report access
· US price-volume proxies and KR 5/20/30-session net-share windows
· US MultiIndex batch fix, safer ambiguous delivery and mandatory integration checks

Release notes:
https://github.com/dragon1086/prism-insight/releases/tag/v2.22.0

Software, virtual-operation and research updates; not investment advice.
```
