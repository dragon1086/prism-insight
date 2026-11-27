# PRISM-INSIGHT v2.26.0 — 재진입 재설계 · 피라미딩 복귀 · 미국 국면 K8b · 약세장 방어 · 인버스 ETF 레인

> **발행일**: 2026-11-27
> **범위**: `v2.25.0` (`3e147254`) → `69f993bd` · 커밋 **1095개** / 병합 PR **255개** / PR 없는 직접 커밋 **85개**
> **규모**: 파일 **555개**, **+40,235 / −7,943줄** · 2026-10-27–2026-11-27 KST
> **집계 기준**: 이 릴리즈 문서·감사 자료 작성 커밋은 위 통계에서 뺍니다. 새 태그에는 이 문서도 들어갑니다.

## 한눈에 보기

이전 정식 릴리즈는 **v2.25.0(태그 `3e147254`, 10/27)**입니다. 이번 버전은 그 뒤 한 달 동안 들어간 변경 1095개를 오래된 순서부터
하나씩 보고 작업 단위로 묶었습니다. 최근 변경이라고 더 크게 쓰지 않았습니다. 10월 말에는 BTC 저장 공간 정리와 매도 오작동 수리가,
11/7~11/10에는 피라미딩·재진입 비중·미국 국면 교체·인버스 레인이, 11/16 이후에는 재진입 신호 재설계와 약세장 방어 관문이 들어갔습니다.

- **재진입을 다시 설계(실전)**: 재진입 첫 비중을 절반으로 줄였다가(11/8) 한국 강세장만 원래 크기로 돌렸습니다(11/9~10).
  한국은 50일선 아래 재진입을 사지 않습니다. 미국은 기준 가격으로 되돌아오기를 기다리는 신호 대신 **20일선 회복 + 평균 이상 거래량**
  신호로 삽니다(11/16). 손절 뒤 바로 달리는 종목을 놓치던 구조를 고친 것입니다. 판단·매수 시각은 마감 직전(KR 15:08, 미국 15:30 ET)으로
  옮겼고, 미국은 손절한 종목에서 나온 신호만 삽니다(11/26).
- **크게 가는 종목에 더 싣기(실전)**: 강한 강세장에서 평균 단가보다 +5% 오른 종목을 **새 행으로 한 번 더 사는 피라미딩**을 되살렸습니다(11/8).
  증액 추격 한도 하한 1.5%, 1주 미만 증액은 1주로, 목표 비중까지 채우기, 종가 확인 증액은 마감 직전 당일 매수로 바꿨습니다.
  강세장에서는 보유 종목 수 대신 **실제 투자 비중**으로 칸을 세어 최대 16종목까지 삽니다(11/21).
- **약세장 방어(실전)**: 강세가 아닐 때 52주 고점의 75%도 안 되는 종목은 새로 사지 않습니다(11/21, KR·US).
  한국은 횡보·약세장에서 산 종목의 수익 보호선을 더 일찍(+3%) 켜고 더 좁게 잡습니다(11/10, 미국은 11/11 끔).
- **미국 국면 기준 교체(실전)**: 미국 6단계 국면을 S&P500 하나에서 S&P500·나스닥을 함께 보는 **K8b** 기준으로 바꿨습니다(11/10).
- **매도 오작동 수리**: 50일선 아래에서 산 재진입 종목이 같은 날 50일선 손실매도로 팔리던 문제(로보티즈·LITE), 오후 매도 점검이
  오늘 대신 전날 종가로 판단하던 문제(LLY), 미국 장 마감 뒤 시간외 가격이 판단에 섞이던 문제를 고쳤습니다.
- **인버스 ETF 레인(새로 생김)**: 약세장 대응으로 인버스 ETF 레인을 설계·연구하고, 손익비 2를 넘긴 한국 신호 3개만
  계좌당 1칸으로 사는 실매매 층을 만들어 11/11부터 운영합니다. 주식 매매와는 따로 움직입니다.
- **매매 지시문 정리**: BUY·SELL 지시문(KR·US × 한/영)에서 결과를 바꾸지 않는 중복·사문을 지우고 코드와 어긋난 문장을 맞췄습니다(SELL −21%, BUY −8%).
  미국 금융주는 영업이익 대신 세전이익·자기자본 비율로 재무 관문을 판정합니다.
- **운영·안전**: 종목코드 변경(BKH→HZN) 자동 대조, 수능일 같은 KRX 특수 거래시간 지정, KIS 토큰·시세 재시도, ClickStack 원장 전송 멈춤 경보,
  극단 공포 알림(정보용)을 넣었습니다. 매일 아침 감시 요약은 격주 2주 점검 리포트로 바꿨습니다.
- **BTC 데모**: 커밋 수로는 이번에도 가장 큰 묶음(비병합 커밋의 62%)입니다. 10월 말에는 저장 기록을 잃지 않고 압축·중복 제거하는 작업과
  7일 보존이 몰렸고, 판단 모델을 GPT-6.1 Terra로 바꿨습니다. 11월에는 두 시간봉 동시 수렴 때 진입 제한, 진입 비용·최대 손실 사전 검산,
  판단 지시 63KB→6.9KB 정리, **30분봉 최소·1시간봉 우선** 차트 기준을 넣었습니다. 모두 Bybit 데모이며 실자금 변경과 수익성 입증은 없습니다.
- **연구**: 91차례가 넘는 연구 기록이 쌓였습니다(매도 손잡이 5년, 재진입 구조 R1~R12, 최초 진입, 국면, VWAP, 포지셔닝, 미국 구조 탐색 등).
  운영에 올린 것은 위의 재진입·피라미딩·국면·약세장 관문이고, 나머지 대부분은 기각되어 `docs/RESEARCH_LESSONS_ko.md`에 남았습니다.
  연구 엔진은 운영 로직 전체를 따라가게 묶고, 운영 규칙이 바뀌면 엔진도 같이 바꾸도록 테스트로 고정했습니다.

## 이전 → 지금: 달라진 동작 (날짜순)

병합 날짜(UTC) 순서입니다. BTC 데모 변경은 같은 기간 여러 건을 한 줄로 묶었습니다.

| 병합일(UTC) | 영역 | 이전 (v2.25.0) | 지금 (v2.26.0) |
|---|---|---|---|
| 10-27 | 운영 데이터 | 한국 마스터 시총 0인 거래 종목 하나로 스냅샷 배치 중단, 미국 시세 KIS 초당 한도 오류로 실패 | 시총 0 종목만 빼고 진행, 초당 한도(EGW00201)는 재시도 (#1161, #1162) |
| 10-27~28 | 초분할 워커·운영 감시 | 미국 시세가 오래되거나 비면 오류, 정지 신호에 오류 종료, 이미 처리한 라이브러리 오류도 경보, 디스크 경보 없음 | 대기(WAIT)로 기록·조용히 종료, 처리된 오류 무시, 디스크 90% 경보(df와 같은 계산) (#1163–#1165, #1171, #1183–#1185) |
| 10-27~31 | BTC 저장·보존 | 판단 기록·감사·복구 기록을 원본 그대로 중복 저장, 백업이 디스크 한도를 넘김 | 원본을 잃지 않는 압축·참조 중복 제거, 백업 대조 뒤 과거분 압축, 대기 기록 7일 보존, 백업 디스크 사용 축소 (#1157–#1160, #1166–#1170, #1172–#1182, #1187–#1203, #1206–#1208, #1210, #1211, #1213–#1215, #1220–#1227, #1229, #1230, #1232, #1233, #1236, #1239–#1252, #1254, #1255, #1258, #1260–#1267) |
| 10-27 | BTC 진입 | 수렴 구간에도 신규 진입 | 1시간·30분 확정 MA10–MA35 간격이 함께 줄면 신규 진입 제한 (#1157–#1160) |
| 10-29 | BTC 판단 모델 | GPT-6.1 Sol | GPT-6.1 Terra(low) (#1198) |
| 10-29 | **50일선 손실매도** | 50일선 아래에서 산 재진입 종목도 매수 당일 50일선 손실매도로 팔림(로보티즈 −0.6%, LITE −1.5%) | 50일선 아래 매수분은 종가가 50일선 위로 한 번 나오기 전까지 제외, 매수 당일에는 아무 포지션에도 적용 안 함, 지시문 문구 수정 (#1204, #1218, #1228) |
| 10-29 | 텔레그램 | 번역 방송 전송 결과를 모르면 그대로 끝, Markdown 거절 재시도를 오류로 기록, 보유 없는 매도를 오류로 기록 | 한 번만 재전송, 경고로 기록, 매도 건너뜀으로 기록 (#1205, #1209, #1216, #1217) |
| 10-29~30 | DB 백업 | SQLite 백업이 사본을 두 번 만들어 디스크 부족 | 사본 한 번, 시간 안에 완료 (#1212, #1213) |
| 10-30 | **오후 매도 점검 가격** | 오후 점검이 오늘 고점으로 올린 보호선을 전날 종가와 비교해 매도(LLY 10/30) | 오후 점검(현지 14:00~마감+30분)의 현재가를 오늘 종가로 보고 판단(KR·US) (#1253) |
| 10-30 | 미국 매수 시그널 | 우리 KIS 주문이 1주 미만으로 막히면 BUY 시그널도 안 나가 구독자가 매도만 받음(LITE·LLY) | 우리 주문 결과와 관계없이 BUY 시그널 발행 (#1231) |
| 10-30 | 운영 안전 | 미국 잔고 조회 실패를 빈 포트폴리오로 봄, 목표가 없는 재진입 신호가 오류 경보, 테스트가 유지보수 방에 알림 가능 | 조회 실패는 보유 모름, 목표가 없으면 건너뜀, 테스트 알림 차단, 낡은 PR CI 취소 (#1234, #1237, #1257, #1259) |
| 10-30 | 시세 조회 | KIS 연결이 끊기면 조회 실패 | 읽기 전용 시세는 재시도 (#1238) |
| 10-30 | 메시지·대시보드 | 보유 메시지에 같은 AI 사유 반복 | 한 번만 (#1256) |
| 11-01 | BUY 직관·미국 원칙 | 규칙 예외를 허락하거나 테마를 이유로 쓰는 직관이 검사 없이 저장, 주간 정리가 새 미국 원칙을 지움 | 금지 표현 검사로 제외, 새 원칙 유지 (#1275, #1276) |
| 11-01 | 증액 원장 | 증액 계획의 돌파 가격·추격 한도가 원장에 없음 | 기록 (#1280) |
| 11-01~06 | BTC 판단·주문 | 좁은 이평선 띠 무시, 부분 체결 뒤 감축 막힘, 연구 호출이 정기 판단과 겹침, 우선 처리(priority) 요청 | 좁은 띠 반영, 안전한 감축 허용, 연구 호출 분리, 거래량 분포·이평선 거리·확정봉 진행 제공, 일반 OAuth (#1271–#1274, #1278, #1281–#1284, #1286–#1296) |
| 11-04 | KIS 토큰 | 캐시된 토큰이 만료되면 실패 | 한 번 재인증 (#1285) |
| 11-06 | 대시보드 | "매도 수익률 합계"가 비중 없는 단순 합(KR 315 vs 차트 335) | 차트와 같은 슬롯 가중 값, 누적 수익률 차트 2배 높이 (#1297) |
| 11-06 | 매수·손절 감시 | 매수 AI가 업종을 빠뜨리면 "업종 미확인"·업종 한도 통과, 미국 손절 감시가 장 전·후에도 판단(MSFT), 대기 예약매도에 수량 없음 | 종목 마스터로 업종 채움, 정규장만 판단, 요청 수량 저장 (#1298) |
| 11-06~07 | 백업 | 별도 서버 백업이 없는 서버는 사본이 쌓임, 앱 서버 DB 사본이 깨진 채 백업 | 로컬 사본 자동 정리, 깨진 사본 제외 (#1299, #1305) |
| 11-07 | 증액 눌림 구간 | 10세션 전체 종가로 판정해 막 뚫고 올라온 구간을 눌림으로 못 봄 | 처음 구간 위로 올라선 다음 세션부터 판정 (#1300) |
| 11-07 | 운영 감시 알림 | 매일 아침 요약과 [주의] 메시지 | 격주 2주 점검 리포트로 대체, 실제 장애·복구·디스크 경보는 그대로 (#1301) |
| 11-07~08 | **증액·피라미딩** | 돌파·눌림 증액에 AI가 거래량 증가 조건을 붙임, AI 추격 한도 0.3~1%, 피라미딩 없음 | 거래량 증가 조건은 가속 증액에만, 추격 한도 하한 1.5%, 대기 사유 기록, 강한 강세장 +5% 피라미딩 독립 행(11/8 LIVE), 2주 점검·구독자 추종 (#1304, #1307–#1317) |
| 11-07~09 | **재진입 첫 비중** | 재진입도 정규 매수와 같은 첫 비중 | 절반(11/8), 강세장은 원래 크기(11/9), 국면별 가상 비중 기록 (#1306, #1323, #1326) |
| 11-08 | 문서 | 일회성 기록 문서가 많음 | 결론은 연구 기록·로드맵으로 옮기고 정리 (#1318, #1319) |
| 11-08~10 | **인버스 ETF 레인** | 없음 | 설계·S1 골격, S2~S18 연구, 한국 관측 기록기(KQ_X10 등), 미국 28업종 기록기 (#1322, #1324, #1325, #1327–#1330, #1332, #1333, #1335, #1337, #1345, #1346) |
| 11-09 | KIS 토큰 | 동시 발급으로 1분당 1회 제한(EGW00133) 위반 | 발급 직렬화, 11/12부터 한도에 걸리면 한 번 기다림 (#1331, #1361) |
| 11-09~10 | BTC 보유 보호 | 보유 보호가 고정 | 상황에 맞춘 보유 보호, 진입 경제성 비교, 위쪽 목표·넓은 잡음 기준 (#1321, #1334, #1343) |
| 11-10 | **미국 국면** | S&P500 기준 6단계 | K8b(S&P500·나스닥 결합) 기록 → 운영 국면으로 교체, 매도 쪽도 같은 400일 자료 (#1339, #1341) |
| 11-10 | **재진입 관문** | 국면과 관계없이 같은 규칙 | 한국 50일선 아래 차단, 미국 강세 재진입은 다시 절반, 미국 K8b 강세 초입(1~20세션) 차단 (#1342, #1344) |
| 11-10 | **비강세 매수분 보호선** | 모든 포지션 +5%에서 켜고 ×0.92/×0.95 | 횡보·약세장에서 산 포지션은 +3%에서 켜고 ×0.94/×0.96(한국만, 미국은 11/11 끔) (#1336) |
| 11-10 | CI | 같은 저장소 PR에서 CLA 검사 누락 | pull_request 이벤트에서도 실행 (#1340) |
| 11-10~11 | 종가 확인 증액 | 확정 일봉을 기다려 다음 날 예약주문 → 구독자가 못 따라감 | 마감 직전(KR 15:20 동시호가, 미국 마감 15분 전) 판단·당일 매수, 한국 문자열 가격 처리 (#1348, #1356) |
| 11-10 | 미국 재무 판정 | 분기 미보고 칸을 N/A로, 영업이익 행이 없는 금융주는 재무 관문 근거 없음 | 미보고 분기 표시, 금융주는 세전이익(F1)·자기자본/총자산 5% 또는 차입금/자기자본 200%(F2) 결정론 판정 (#1349, #1350) |
| 11-10~11 | **매매 지시문 다이어트** | BUY·SELL 지시문에 모순 10·중복 17·사문 9, 거래량 해석 블록 8사본 | 정리(SELL −21%, BUY −8%), 거래량 블록 하나로, 실제 오후 점검 시각·매도 트리거 자리표시 계약 (#1352, #1357) |
| 11-10~14 | 인버스 실매매 | 없음 | 계좌당 1칸 실매매 층(기본 꺼짐) → 11/11 운영 켬, 미국 세션 날짜 수정, 검증된 3신호만·보유 전량 매도·시한 매도, KQ_RFAIL 관측 (#1351, #1353, #1354, #1362, #1372) |
| 11-11~19 | BTC 판단·위험 | 익절 계획에 고정 달러 기준, 진입 비용과 최대 손실 예비분 혼합, 진입·손절·분할 익절 따로 계산 | 고정 달러 제거, 비용·예비분 분리, 연결 계획 사전 검산, 판단 지시 63KB→6.9KB, 신규 진입 위험 상한은 제안만(사용자 선택 전 보류) (#1355, #1358–#1360, #1363, #1365–#1368, #1374–#1376, #1380, #1382, #1384, #1385, #1387–#1392, #1394, #1398, #1400, #1401, #1404–#1406, #1408, #1410, #1412) |
| 11-12 | 연구 | — | 앵커드 VWAP를 최초 진입·증액·재진입·매도에 시험, 채택 없음 (#1364) |
| 11-13~15 | 초분할 수량 | 1주 반올림으로 목표 비중에 못 미친 채 고정(JNJ), 다른 행의 미체결 증액 때문에 피라미딩 행 손절이 0주, 증액 1주 미만은 차단 | 목표 비중까지 채움(주계좌·구독자), 행별 손절은 체결 수량 기준, 1슬롯 안이면 1주로 증액 (#1369–#1371, #1379) |
| 11-15 | 원장 | — | 하락 추세 반등 자리 '안 사기' 플래그를 매수 판단 원장에 기록만 (#1378) |
| 11-15~16 | **미국 재진입 신호** | 가격이 기준가로 돌아와야 발동 → 손절 뒤 바로 달리는 종목을 못 삼 | 20일선 회복 + 평균 이상 거래량(MA20R) 관측(KR은 강세 앵커만 기록) → 미국 실전, 첫 비중 국면 무관 ×1 (#1381, #1383) |
| 11-16 | **재진입 판단 시각** | KR 14:00 / 미국 13:50 ET, 추정 거래량 | KR 15:08 / 미국 15:30 ET, 실측 거래량 비중 반영 (#1386) |
| 11-17~18 | 종목코드 변경 | BKH→HZN 변경 뒤 옛 코드의 멈춘 시세를 이틀 감시 | 증권사 보유와 장부 자동 대조(시뮬레이터 전용 보유 제외, 옛 id 문자 비교) (#1393, #1395, #1396) |
| 11-18 | 운영 감시·연구 엔진 | 감시 마감 시각이 옛 재진입 시각 | 새 시각에 맞춤, 연구 엔진이 운영 규칙 상수를 따르는지 테스트로 고정(11/22 상수 추가) (#1397, #1402, #1421) |
| 11-19 | **KRX 특수 거래일** | 정규장 09:00~15:30 고정 → 수능일(10:00~16:30)에 장중 주문이 종가·다음 날 주문으로 나감 | 날짜 지정 `KRX_SESSION_OVERRIDE` (#1409) |
| 11-19 | 미국 시세 | KIS가 빈 시세를 주면 매수 판단 건너뜀 | 한 번 재시도 (#1407) |
| 11-20 | 미국 대시보드 | KIS 지수 조회 실패 시 지수 선 사라짐, 결제 대기 매도 대금 빠짐 | 지수 유지, 실총자산에 포함 (#1411, #1413) |
| 11-20~21 | BTC 차트 기준 | 15분·12시간봉도 판단에 사용 | 30분 최소, 1시간 > 30분 > 4시간 > 1일 > 1주 우선 (#1414, #1415, #1418) |
| 11-21 | **약세장·강세장 칸** | 비강세장에도 고점에서 깊게 빠진 종목 신규 매수, 강세장도 보유 10행 상한 | 비강세 52주 고점×0.75 미만 신규 매수 차단, 강세장은 투자 비중 합계 기준 10칸·최대 16행(둘 다 KR·US 운영 켬) (#1416, #1420) |
| 11-21 | 원장 감시 | ClickStack 원장 전송이 3일 멈춰도 모름 | 워치독 경보, 공유 원장 읽기 스트리밍·BTC trace 조회 복구 (#1417, #1419) |
| 11-21 | 책 지식 | — | 256~257쪽, 5장 결론 카드 (#1186) |
| 11-24 | 재진입 안전 | 규칙 재진입은 업종을 Unknown으로 저장, 미국 인수 확정 종목도 재진입 | 업종 저장, 합병 주총 공시(PREM14A 등) 종목 제외 (#1425) |
| 11-25 | 대시보드 | 재진입 감시 목록 없음 | 관심 종목 탭에 한국·미국 재진입 감시 카드(가격 대신 가까움·보통·멂) (#1426, #1427) |
| 11-25 | 알림 | — | QQQ RSI ≤30·공포탐욕 ≤10·VIX ≥30 동시 충족 시 극단 공포 알림(정보용, 주문 없음) (#1428), BTC 검증 기록 재사용 (#1429) |
| 11-26 | **미국 종가 판단** | KIS 미국 시세·당일 일봉이 정규장 밖 거래를 따라 움직임(17:10 ET 기록 286건 중 240건이 확정 종가와 다름) | 추세 청산은 정규장 밖 건너뜀, 휴장·조기 폐장 반영, 장기 보유 감시 마감 20:45 ET (#1430) |
| 11-26 | **미국 재진입 출처** | 손절·보류·관문 차단 출처 신호 모두 주문 | 손절한 종목 감시에서 나온 신호만 주문(보류 출처는 기록만) (#1431) |
| 11-26 | 원장 | 보유 점검이 올린 손절가가 원장에는 옛 값(나무가), 규칙 재진입 점수가 0 | 실제 반영 값 기록, 점수는 모름으로 (#1432) |

## 투자자께: 매수부터 매도·재진입까지 무엇이 달라졌나

"실전"은 실제 계좌 주문에 쓰인다는 뜻이고, "관측"은 주문 없이 기록만 한다는 뜻입니다. 상태는 11/27 운영 서버 설정을 읽어 확인했습니다.

| 단계 | 이전(v2.25.0) | 지금(v2.26.0) | 상태 |
|---|---|---|---|
| 국면 | 미국은 S&P500 하나로 6단계 | 미국은 S&P500·나스닥 결합 K8b | 실전 |
| 신규 매수 | 국면과 관계없이 고점 대비 위치 제한 없음 | 비강세장에서 52주 고점×0.75 미만은 사지 않음 | 실전 |
| 칸(슬롯) | 보유 10행 상한 | 강세장은 투자 비중 합계 10칸·최대 16행 | 실전 |
| 증액 | 다음 날 예약 종가 확인 증액, 1주 미만 차단, AI 추격 한도 그대로 | 마감 직전 당일 매수, 1주로 올림, 목표 비중까지 채움, 추격 하한 1.5% | 실전 |
| 피라미딩 | 없음 | 강한 강세장 +5%에서 같은 종목 새 행(최대 3행) | 실전 |
| 매도 | 50일선 손실매도가 재진입 당일에도, 오후 점검이 전날 종가로, 미국은 시간외 가격도 | 매수 당일·50일선 아래 매수분 예외, 오후 가격=오늘 종가, 미국은 정규장 가격만 | 실전 |
| 수익 보호선 | 모든 포지션 같은 폭 | 한국 횡보·약세장 매수분은 +3%에서 켜고 더 좁게 | 실전(한국) |
| 재진입 신호 | 기준가 복귀형(한국·미국) | 한국은 기존 신호 + 50일선 관문, 미국은 20일선 회복 + 거래량 | 실전 |
| 재진입 비중 | 정규 매수와 같음 | 기본 절반, 한국 강세장·미국 20일선 회복은 원래 크기 | 실전 |
| 재진입 시각·출처 | KR 14:00 / 미국 13:50 ET, 모든 출처 | KR 15:08 / 미국 15:30 ET, 미국은 손절 출처만 | 실전 |
| 인버스 ETF | 없음 | 검증된 한국 3신호, 계좌당 1칸 | 실전(별도 레인) |

### 재진입: 손절 뒤 다시 달리는 종목을 따라가기

- **첫 비중 절반 → 강세장만 원래대로**: 연구 재현에서 재진입이 안 하는 것보다 손해였고(감시당 KR −0.39·US −0.69%p), 첫 비중을 반으로
  줄이면 큰 종목 수는 그대로 손실만 줄었습니다. 11/8 절반으로 줄였고(#1306), 강세장 재진입은 기대값이 플러스라 11/9 원래 크기로 돌렸습니다(#1326).
  미국 강세 재진입은 옛 국면·K8b 모두에서 손실이라 11/10 다시 절반으로 되돌렸습니다(#1342).
- **한국 50일선 관문**: 판단 가격(오늘 종가로 간주)이 50일선 아래인 한국 재진입은 손실이 많고 큰 종목이 거의 없어 사지 않습니다(#1342).
- **미국 강세 초입 차단**: K8b가 강세로 바뀐 지 1~20세션인 미국 재진입은 사지 않습니다(#1344).
- **미국 20일선 회복 신호**: 운영 재진입 신호들은 가격이 기준가로 돌아와야 발동해, 손절 뒤 바로 달리는 큰 종목을 못 샀습니다.
  "전일 종가 ≤ 20일선 < 현재가, 거래량 ≥ 20일 평균" 신호는 5년 재현에서 10슬롯 계좌 +58%(재진입 없음 +3%), 낙폭 −43→−38%였습니다.
  미국은 이 신호로 사고 첫 비중은 국면과 관계없이 원래 크기입니다. 한국은 칸 경쟁으로 손해라 현행을 유지하고 강세 신호만 기록합니다. (#1381, #1383)
- **마감 직전 판단**: 연구는 종가·확정 거래량으로 판정했는데 운영은 장중 추정으로 판단해, 장중에만 넘었다가 종가에 실패한 신호(손익비 4.69)를
  종가 확정 신호(손익비 5.45)와 섞어 샀습니다. 판단을 KR 15:08 / 미국 15:30 ET로 늦췄습니다. (#1386)
- **미국은 손절 출처만**: 매수 보류·관문 차단에서 나온 재진입이 정규 매수와 손절 재진입의 칸을 먼저 차지했습니다. 2026 미국 감시 재생에서 손절 출처
  61건은 승률 23%·평균 이익 +19.7%·평균 손실 −3.3%·손익비 6.04(AMD 제외 3.21)였습니다. 보류 출처는 기록만 합니다. (#1431)
- **안전장치**: 목표가가 없는 신호는 오류 대신 건너뛰고(#1259), 규칙 재진입의 업종을 저장하며, 미국 합병 주총 공시가 있는 인수 확정 종목은 사지 않습니다(#1425).
  대시보드 관심 종목 탭에서 재진입 감시 목록을 볼 수 있습니다(#1426, #1427).
- 11/24 전수 재확인에서 "한국 재진입을 끄자"는 권고는 철회했습니다(재진입 전부 끔 −13.8%p, 이득은 강세장, 직접 커밋 `ecd95020`).

### 증액·피라미딩: 크게 가는 종목에 더 싣기

- **피라미딩 복귀**: 강한 강세장(strong_bull·parabolic)에서 같은 종목의 평균 단가 대비 +5% 이상이고 행이 3개 미만이면 **독립 행**으로 한 번 더 삽니다.
  새 행도 초분할 첫 비중·자기 증액 계획·손절·주도주 판정을 따로 갖습니다. 보유 8종목 미만이면 빈 칸 2개를 남깁니다. 11/8 운영 켬. (#1310, #1311)
- **증액이 막히던 원인 제거**: 돌파·눌림 증액에 AI가 붙이던 거래량 증가 조건은 가속 증액에만 쓰게 했고(#1304), AI가 0.3~1%로 쓰던 추격 한도는 1.5~2.0%로
  맞췄습니다(#1308). 눌림 구간은 처음 구간 위로 올라선 뒤부터 셉니다(#1300). 2주 점검에서 조건을 충족한 증액 6건이 모두 1주 미만으로 막혀,
  1슬롯 안이면 1주로 올려 삽니다(#1379). 1주 반올림으로 목표 비중에 못 미친 계좌는 목표까지 채웁니다(#1369).
- **종가 확인 증액은 당일**: 다음 날 예약주문이라 구독자가 따라가지 못했습니다. KR 15:20 동시호가, 미국 마감 15분 전에 판단해 당일 삽니다. (#1348, #1356)
- **강세장 돈 기준 칸**: 초분할로 일부만 채운 행이 10행 상한을 먼저 채워 돈이 남는데도 새 종목을 못 샀습니다. 강세장에서만 "투자 비중 합계 + 한 칸 ≤ 10칸,
  최대 16행"으로 셉니다. 11번째 이후 매수의 손익비가 1 미만이면 끕니다. (#1420)
- 피라미딩 행 손절이 다른 행의 미체결 증액 때문에 0주로 나가던 문제와, 행별 손절 수량을 주문이 아닌 체결 수량으로 세게 한 수리가 함께 들어갔습니다. (#1370, #1371)

### 매도·보유: 잘못 파는 경우 없애기

- **50일선 손실매도 예외**: 50일선 아래에서 산 포지션은 '50일선 아래'가 새 정보가 아니어서 조금만 빠져도 당일 팔렸습니다(로보티즈 −0.6%).
  매수 후 종가가 50일선 위로 한 번 나오기 전까지 이 매도를 쓰지 않고, 매수 당일에는 모든 포지션에 쓰지 않습니다(LITE −1.5%). 손절가·−7% 하드스탑은 그대로입니다. (#1204, #1218, #1228)
- **오후 가격 = 오늘 종가**: 스크리닝과 다른 단계는 오후 실행 가격을 오늘 종가로 보는데 매도만 전날 종가를 썼습니다. LLY는 오늘 실적 급등 고점으로 올린 보호선을
  전날 종가와 비교해 팔렸습니다(당일 종가는 보호선 위). KR·US 매도 판단을 모두 맞췄습니다. (#1253)
- **횡보·약세장 매수분 보호선**: 매수 시점 국면이 횡보·약세였던 포지션은 +3%에서 보호선을 켜고 ×0.94(현재 강세)/×0.96(현재 횡보·약세)로 좁힙니다.
  미국은 재현 고점(장중 최고가)과 운영 고점(점검 시각 가격)이 달라 다시 집계한 결과 −0.92%p라 11/11 껐고, 한국(+0.22%p)만 씁니다. (#1336, 직접 커밋 `d5f0eabb`)
- **미국 정규장 가격**: KIS 미국 시세는 마감 뒤에도 시간외 거래를 따라 움직여, 17:10 ET 기록 286건 중 240건이 확정 종가와 달랐습니다(중앙값 0.2%, 최대 7.8%).
  추세 청산은 정규장 밖을 건너뛰고, 휴장·조기 폐장을 반영합니다. 손절 감시도 정규장만 판단합니다. (#1298, #1430)
- 매도 규칙 연구(보호선 폭 국면별, 매도 손잡이 13안·5년, 이평선 손절 유예)는 모두 기각되어 매도 규칙은 이 범위 밖에서는 그대로입니다.

### 매수: 약세장에서는 덜 사기

- **비강세 52주 고점 관문**: 강세가 아닐 때 정규 신규 매수의 판단 가격이 52주 확정 최고가 × 0.75 미만이면 사지 않습니다. 증액과 규칙 재진입은 제외, 최고가를
  모르면 막지 않습니다. 11개월 실제 후보 재적용 기준선은 KR +7.5%, US −1.1%였습니다. (#1416)
- **매매 지시문 다이어트**: 감사에서 찾은 모순 10·중복 17·사문 9를 정리했습니다. 판단 결과를 바꾸지 않는 문장만 지우고, 코드와 어긋난 문장은 코드 기준으로 맞췄습니다.
  KR SELL 13,142→10,341자, KR BUY 24,455→22,575자 등입니다. 옛·새 지시문 재생 비교를 거쳐 병합했습니다. (#1352, #1357)
- **미국 금융주 재무 판정**: 분기 손익계산서에 영업이익 행이 없는 금융주는 세전이익 흑자(F1), 은행은 자기자본/총자산 ≥ 5%·그 외는 차입금/자기자본 < 200%(F2)로
  판정합니다. 다른 종목의 프롬프트는 바이트 단위로 같습니다. (#1349, #1350)
- 매수 AI가 업종을 빠뜨리면 종목 마스터로 채우고(#1298), BUY 직관은 규칙 예외·테마 근거 표현이 있으면 저장하지 않습니다(#1276).

## 1. 매도·보유 규칙 (KR·US)

- `tools/trend_exit_seller.py` `ma50_exit_armed`: 매수가 < 매수 직전 50일선이면 매수 후 완성 일봉 종가가 50일선 위로 한 번 나오기 전까지 TIER1.5 미적용.
  이력 부족·조회 실패는 기존 규칙(모름을 통과로 채우지 않음). `on_entry_session` 분기로 매수 당일 미적용, 요약 키 `entry_day_ma50`. SELL 지시문 KR·US × 한/영 문구와
  검토 해시 갱신. (#1204, #1218, #1228)
- `prism_core/sell_facts.py`: 오후 점검(현지 14:00~마감+30분, 거래일)이면 오늘 봉(종가=현재가, 거래량=누적)을 붙여 2단계 보호선·3단계 추세 약화를 계산하고,
  보호선 이탈은 오늘 봉으로만 판정합니다. (#1253)
- `SELL_TIGHT_NONBULL_ENABLED`(시장 목록, 기본 빈 값): `cores/oneil_fallback.py`·`prism-us/` 사본의 `trail_params(..., entry_regime)`. 운영 `KR`. (#1336)
- `prism_core/time_windows.us_regular_session`으로 정규장 판정을 합치고 휴장·조기 폐장 반영, 미국 추세 청산 연속 이탈 횟수는 정규장 가격으로만 셉니다.
  crontab은 배포 때 20:20/20:40/20:55 ET로 옮겼습니다. (#1430)
- 계좌에 보유가 없는 매도는 오류가 아니라 건너뜀으로 기록합니다(#1217). 미국 잔고 조회 실패는 빈 포트폴리오가 아니라 보유 모름입니다(#1257).

## 2. 재진입

- `REENTRY_V3_FIRST_SIZE_FACTOR`(운영 0.5), `REENTRY_V3_BULL_FULL_SIZE`(시장 목록, 운영 `KR`), `REENTRY_REGIME_SIZE_SHADOW`(국면별 가상 비중 기록, 운영 켬).
  보유 행 `micro_split.reentry_size`·`reentry_regime_shadow`에 기록. (#1306, #1323, #1326, #1342, #1344)
- `REENTRY_V3_MA50_GATE_MARKETS=KR`(사유 `below_ma50`), `REENTRY_V3_K8B_EARLY_BULL_MARKETS=US`(사유 `k8b_early_bull`). K8b 라벨 함수는 `prism_core/us_regime_k8b.py`로
  옮겨 미국 데이터 준비와 재진입이 같은 함수를 씁니다. 값이 없으면 막지 않습니다. (#1342, #1344)
- 20일선 회복 규칙 `MA20R`: `prism_core/reentry_campaign.py`의 관측 팔(#1381)과 운영 규칙(#1383, `REENTRY_V3_MA20R_LIVE_MARKETS=US`). 손절 = 앵커 뒤 저점×0.99(국면 상한),
  목표 +20%. 첫 비중은 `REENTRY_MA20R_FULL_SIZE` 사유로 국면 무관 ×1.
- `ORDER_DEADLINES` KR 15:13 / 미국 15:40 ET, 판단 실행 KR 15:08 / 미국 15:30 ET, 시각별 실측 거래량 비중(KR 0.86·US 0.79) 사용. 감시 마감도 새 시각으로. (#1386, #1397)
- `REENTRY_V3_STOP_SOURCE_ONLY_MARKETS=US`(사유 `heldoff_source`), 50일선·K8b 초입 관문 뒤·일일 한도 앞에서 거릅니다. (#1431)
- 목표가 없는 신호 `no_target`(#1259), 업종 저장·미국 PREM14A/DEFM14A/PREM14C/DEFM14C 차단(#1425), `tools/export_reentry_watch.py` 읽기 전용 감시 목록(#1426, #1427).

## 3. 매수 판단·지시문·기억

- 미국 BUY 시그널은 우리 KIS 주문 결과와 관계없이 발행합니다(이슈 #1219, LITE·LLY). (#1231)
- 주간 직관 검사 `tracking/buy_intuitions.validate()`에 금지 표현 검사(KR 276 하나 제외), 미국 새 원칙이 주간 정리에서 지워지지 않게. (#1275, #1276)
- 업종 누락 보완(`_sector_source=system`), 대기 예약매도 요청 수량 저장. (#1298)
- `PRISM_US_FINANCIAL_F_GATE`(기본 켜짐), 로그 `[SECTOR_BUY][US]`. (#1350)
- 지시문 다이어트: 거래량 해석 블록은 `prism_core/volume_prompt_block.py` 하나로, 실제 오후 매도 점검 시각과 `sell_triggers` 자리표시 계약. (#1352, #1357)
- `NONBULL_52W_GATE_MARKETS`(운영 `KR,US`), `BULL_MONEY_SLOT_MARKETS`(운영 `KR,US`, BUY 입력의 보유 표시만 n/16). (#1416, #1420)

## 4. 초분할 증액·피라미딩

- 증액 원장에 돌파 가격·추격 한도(#1280), 시나리오별 대기 사유를 세션당 한 번(#1309), 2주 점검에 피라미딩·칸 부족·증액 대기 사유(#1312).
- `PYRAMID_ROWS_MICRO_SPLIT_ENABLED`(운영 켬), `PYRAMID_RESERVE_SLOTS=2`, `PYRAMID_RESERVE_BELOW_TICKERS=8`. 같은 종목 피라미딩 행은 업종 종목 수에서 한 종목으로 셉니다.
  구독자도 피라미딩 행 증액·매도를 따라갑니다. (#1310, #1311, #1313)
- 증액 연구 정정: 강세장 연구가 종가 모델이라 증액을 적게 셌고(장중 35.7%), 장중 체결로 다시 확인했습니다. 증액 크기·추격 한도 연구와 전일 고가 증액 트리거
  AI 재생은 미채택. (#1307, #1314, #1315, #1317)
- `prism_core/b3_ae_worker.py` `in_close_window`·`provisional_daily`(완성된 5분봉으로 임시 일봉). (#1348, #1356)
- `one_share_floor_cash()`(보유 수량 미확인이면 계속 차단, 원장 `cash_basis=one_share_floor`), 목표 비중까지 채우기, 행별 손절 수량. (#1369–#1371, #1379)

## 5. 스크리닝·국면·시장 데이터

- `US_REGIME_K8B_MODE=off|shadow|live`(운영 `live`). K8b를 모르면 옛 라벨로 돌아가고 `legacy_regime`·`k8b_live_applied`를 기록합니다. 매도 쪽 `_compute_live_regime`은
  S&P500·나스닥 400일을 받습니다. (#1339, #1341)
- 미국 Market Pulse 오전 배치 정책은 11/18 사용자 결정으로 live 복귀(직접 커밋 `7b3f1c9c`). 이론 근거만 있던 국면 플래그 4개와 `PULSE_PILOT_REEXPOSURE`는
  11/13 끔/관측으로 돌렸습니다(직접 커밋 `411c1b7a`, `6ba25633`, `1bdd2922`).
- KIS: 시총 0 종목 제외(#1161), 미국 초당 한도 재시도(#1162), 끊긴 연결 재시도(#1238), 토큰 만료 재인증(#1285), 토큰 동시 발급 직렬화와 분당 한도 대기(#1331, #1361),
  미국 빈 시세 재시도(#1407).
- `KRX_SESSION_OVERRIDE=날짜=시작-끝`: 정규장·주문 창을 그날만 바꿉니다. 예약표 조건부 이동은 운영 crontab에서 합니다. (#1409, 직접 커밋 `62082093`)

## 6. 인버스 ETF 레인 (약세장 대응, 별도 레인)

- 설계·골격: `prism_inverse/` 패키지와 설계 문서 v1.0(정본 `docs/knowledge/books/trade-like-an-oneil-disciple/INVERSE_ETF_DESIGN_ko.md`). (#1322)
- 연구: 지수 인버스(S2), 주도주 묶음(S3), 두 갈래·비중 연동(S4·S5), 업종 동인(S6), 미국 약세 조기 감지·확인(S7·S8), 첫 이탈 × 약세 국면, 한국 조정·미국 단일종목(S12),
  청산 구조(S17·S18). 대부분 탈락했고, 사용자 기준 "손익비 2"를 진전 없는 포지션 청산으로 맞췄습니다(US 2.11·KR 2.51, 연 15% 목표는 미달).
  (#1324, #1325, #1327–#1329, #1332, #1333, #1337, #1346, 직접 커밋 `3e4d1902`)
- 기록기: 한국 관측 기록기(주문 없음), KQ_X10·KQ_FR·K200_SEMI, 미국 28업종 합성 −2배, 코스닥150 반등 실패 KQ_RFAIL. (#1330, #1335, #1345, #1372)
- 실매매 층: 계좌당 1칸(한국은 한 번에 하나, 미국은 1칸 3등분·최대 3업종), 다음 날 시가 예약주문. 실주문은 손익비 2를 넘긴 한국 세 신호만, 시세 결측으로
  가상 청산이 안 나면 KR 21일·US 35일 뒤 전량 매도, 매도 수량은 그 시점 보유 전량. 미국 예약 시각은 KIS 해외 예약 접수창 때문에 10:10 KST로 옮겼습니다.
  11/11 운영 켬. (#1351, #1353, #1354, #1362)

## 7. 텔레그램 메시지·대시보드

- 번역 방송 전송 결과를 모르면 한 번 재전송(리포트·포트폴리오 방송 포함), Markdown 거절 뒤 일반 글 재시도는 경고, 보유 메시지 AI 사유 중복 제거. (#1205, #1209, #1216, #1256)
- 대시보드: 매도 수익률 카드를 슬롯 가중 값으로(KR +335.18%, US +67.06% 기준), 누적 수익률 차트 높이 320→640, 미국 지수 선 유지, 결제 대기 대금 포함, 재진입 감시 카드. (#1297, #1411, #1413, #1426, #1427)

## 8. 분석 원장·관측

- 매수 판단 원장에 하락 추세 반등 자리 플래그(S1~S4, 지수 M2) 기록만. (#1378)
- 공유 원장 읽기를 스트리밍으로 바꾸고 BTC trace 조회 복구. (#1419)
- 보유 점검 이벤트에 실제 반영된 손절가·목표가, 규칙 재진입 점수는 모름(None). (#1432)

## 9. 운영 감시·알림·보안

- 초분할 워커(B3): 미국 시세 오래됨·비어 있음·야후 일봉 결측은 대기로, systemd 정지는 조용히 종료. (#1163, #1164, #1171, #1183)
- 워치독: 처리된 라이브러리 오류 무시, 디스크 90% 경보(df 계산), 재진입 새 시각, ClickStack 원장 전송 멈춤 경보. (#1165, #1184, #1185, #1397, #1417)
- 운영 감시 요약·[주의] 발송은 `OPS_WATCHDOG_SUMMARY_ENABLED`·`OPS_WATCHDOG_NOTICES_ENABLED`(기본 꺼짐)로 끄고 2주 점검(`--every 14 --anchor 2026-10-18`)으로 대체. (#1301)
- SQLite 백업 사본 한 번·로컬 전용 서버 정리·깨진 앱 서버 사본 제외·실패 기록·실행 권한 복구. (#1212, #1213, #1260, #1261, #1299, #1305)
- 테스트는 유지보수 방 알림을 보낼 수 없게, 낡은 PR CI 자동 취소, CLA 검사 수리. (#1234, #1237, #1340)
- 종목코드 변경 자동 대조 `tools/holdings_reconcile.py`(BKH→HZN 계기). (#1393, #1395, #1396)
- 극단 공포 알림 `tools/watch_extreme_fear.py`(16:45 ET, 자료가 없거나 오래되면 '미충족'이 아니라 오류). (#1428)

## 10. 하네스·연구 기록

- 연구 엔진(full-ops)이 운영 규칙 상수를 따르는지 고정하는 테스트와, 11/21 실전 규칙 상수 추가. (#1402, #1421)
- 코드·프롬프트를 늘리기 전에 삭제·통합을 먼저 검토하는 규칙(직접 커밋 `5eadc908`), KR·US·BTC 일회성 문서 정리(#1318, #1319), 책 256~257쪽 카드(#1186), VWAP 연구(#1364).
- PR 없이 들어간 연구 기록 85개(아래 목록). 주요 결론:
  - 매도: 보호선 폭 국면별·후보 모집단, 매도 손잡이 격자·5년 v3~v6, 이평선 손절 유예(실매매 81건 전부 악화) 모두 기각. 비강세 매수분 보호선만 한국 채택.
  - 재진입: 흔들기 거래량 문턱, 국면별 손절 변형, 청산 관리 9안, 수익 구조 R1~R12, AI 재진입 v1·v2 기각. 운영 기록 기반 깔때기에서 20일선 회복 신호 채택(미국).
    11/26~27 81~91차: 보류 출처 끄기가 근거가 가장 강함(→ #1431), 강세만 재진입·증액 크기·칸 수익률 규칙은 미달.
  - 최초 진입·국면: 다음 날 확인, 주도주 깔때기, 국면 계기판, 국면별 비중, 거절 관문 결과 모두 미채택. 미국 국면은 K8b로 재정의(채택), 한국 국면은 유지.
  - 포지셔닝(공매도·대차·신용·선물 미결제약정), 약세장 후보 손절 필터 71개, 약세 단계·경과일 진입, 미국 구조 탐색 60~82차 모두 채택 없음.
  - BTC: 감시 시간 초과·자기 보호 주문 오정지 수리 기록, 백업 분리 교훈.
- 결론은 `docs/RESEARCH_LESSONS_ko.md`, 가설은 `docs/HYPOTHESIS_REGISTRY_ko.md`에 날짜와 함께 남았습니다.

## 11. BTC 데모

- **저장 공간(10/27~31)**: 판단 기록(review tape)·모델 호출·재무·감사·복구·계좌·스냅샷 기록을 먼저 읽는 쪽을 준비한 뒤 쓰는 쪽을 바꾸는 순서로,
  원본을 잃지 않는 압축과 참조 중복 제거를 넣었습니다. 백업과 대조된 과거분만 압축하고, 검증된 대기(WAIT) 기록은 7일 뒤 정리합니다. SELinux·재부팅 뒤에도
  정리가 안전하게 돌게 했습니다. (#1166, #1167, #1169, #1170, #1173–#1182, #1187–#1191, #1193–#1197, #1199–#1203, #1206–#1208, #1210, #1211, #1213–#1215, #1222–#1227, #1229, #1230, #1232, #1233, #1236, #1239, #1240, #1242, #1244–#1246, #1249, #1252, #1254, #1258, #1260–#1263, #1265–#1267, #1290, #1291, #1360, #1363)
- **판단·진입**: 두 시간봉 동시 수렴 시 신규 진입 제한과 좁은 단일 띠 주의(#1158, #1159, #1273, #1278), 거래량 분포·이평선 거리·확정봉 진행·보유 움직임 요약
  (#1284, #1286–#1288), 진입 경제성 비교·손익비 검사(#1316, #1321), 익절 고정 달러 제거·비용과 최대 손실 예비분 분리·연결 계획 사전 검산(#1365, #1366, #1368),
  장세·진입 목적별 시나리오 정리(#1303), 진입 방식 안의 대안 보존(#1375, #1394, #1404), 판단 정책 단순화(#1380), 신규 진입 위험 상한은 제안만(#1390).
- **보유·청산**: 상황에 맞춘 보유 보호와 청산 지시 통합(#1334), 위쪽 목표·넓은 잡음 기준(#1343), 보유 판단의 15분 반응·수익 반납 비교(#1392), 전량 청산 수량 보존과
  목표 배분(#1384, #1385), 체결·지연 체결 뒤 익절 복구(#1374, #1376), 부분 체결 뒤 감축(#1282, #1408, #1412), 미정산 진입 관리(#1293).
- **차트 기준**: 15분봉 재도입(#1389) 뒤 사용자 기준에 따라 30분 최소·1시간 우선으로 정리(#1415, #1418), 형성 중인 봉 제외(#1387).
- **안정성**: 모델 GPT-6.1 Terra(#1198), 일반 OAuth(#1296), OAuth 실패·응답 종료 보존(#1355, #1367), 금지된 무포지션 판단 생략(#1358, #1359), 연구 호출 분리·과부하 대기
  (#1283, #1400, #1401), 감시 시간 초과는 그 주기만 막음(#1192, #1221), 이력 조회가 보호를 막지 않게(#1271, #1382, #1405, #1414), 대기·조정 취소 구분(#1157, #1160),
  기타 진단(#1242, #1272, #1274, #1281, #1289, #1292, #1398, #1410, #1429).
- **재생·백테스트**: 지원 안 되는 청산 첨부 거부, PostOnly 취소, 고정 파동 기준 재생 복원, 합성 봉 제외, 보유 판단 보존. (#1294, #1295, #1388, #1391, #1406)
- **문서**: 저장 공간 안전장치, 연구 비용·재생 효율 교훈, 보호 브랜치, 제안·최종 검토 구분. (#1159, #1168, #1172, #1220, #1241, #1243, #1248, #1250, #1251, #1255, #1264, #1319)
- 24시간 무경보와 비용 차감 뒤 개선(M5)은 이번 범위에서도 입증되지 않았습니다.

## 개발자용 상세 — 동일 가중치 커밋 집계

`v2.25.0..69f993bd`의 **1095개 커밋을 모두 오래된 순서부터 확인**했습니다. 각 커밋은 1표이며 날짜·최근성·변경 줄 수·작성자·PR 크기에 가중치를 더 주지 않았습니다.
비병합 커밋은 소속 PR의 주된 목적 하나에만 배정하고, 병합 커밋은 따로 셌습니다. PR이 없는 직접 커밋 **85개**도 포함했습니다(BTC 5개, 인버스 1개, 나머지는 연구·하네스 문서).
BTC 비병합 커밋이 390개로 많은 것은 BTC 작업이 작은 커밋을 많이 남기기 때문이며, 커밋 수가 중요도·완성도·수익성 점수라는 뜻은 아닙니다.

<details>
<summary>1095개 커밋의 주제별 집계 펼치기</summary>

| 작업 묶음 | 커밋 | 비율 |
|---|---:|---:|
| 매도·보유(50일선 예외·오후 종가·비강세 보호선·미국 정규장) (`sell_hold`) | 9 | 0.8% |
| 재진입(비중·관문·20일선 회복·시각·출처) (`reentry`) | 13 | 1.2% |
| 매수 판단·지시문 다이어트·52주 관문·돈 기준 칸 (`buy_rules`) | 12 | 1.1% |
| 초분할 증액·피라미딩 (`micro_split`) | 31 | 2.8% |
| 스크리닝·국면(K8b)·시장 데이터·KIS (`screening_regime_data`) | 13 | 1.2% |
| 인버스 ETF 레인 (`inverse`) | 31 | 2.8% |
| 텔레그램 메시지·대시보드 (`messages_dashboard`) | 11 | 1.0% |
| 분석 원장·관측 (`observability`) | 5 | 0.5% |
| 운영 감시·백업·알림·CI (`ops_security`) | 26 | 2.4% |
| 하네스·연구 기록·가설 장부 (`harness_research`) | 86 | 7.9% |
| BTC 데모(저장·판단·위험·주문·재생) (`btc`) | 390 | 35.6% |
| 병합 커밋 (`merge`) — PR 병합 255개 + 동기화 병합 213개 | 468 | 42.7% |
| **합계** | **1095** | 100% |

</details>

<details>
<summary>오래된 작업 누락 점검: 기간별 집계</summary>

| 작성일 구간 (KST) | 비병합 | 병합 | 합계 |
|---|---:|---:|---:|
| 2026-10-27 – 2026-10-29 | 102 | 98 | 200 |
| 2026-10-30 – 2026-10-31 | 196 | 144 | 340 |
| 2026-11-01 – 2026-11-07 | 57 | 47 | 104 |
| 2026-11-08 – 2026-11-10 | 111 | 82 | 193 |
| 2026-11-11 – 2026-11-16 | 76 | 46 | 122 |
| 2026-11-17 – 2026-11-21 | 59 | 41 | 100 |
| 2026-11-22 – 2026-11-27 | 26 | 10 | 36 |
| **합계** | **627** | **468** | **1095** |

10/30 하루가 292개로 가장 많은 것은 BTC 저장 공간 작업이 몰렸기 때문입니다. 구간과 관계없이 모든 PR을 같은 무게로 다뤘습니다.

</details>

전체 SHA·작성일·제목·단일 분류·PR 연결과 PR별 규모는
[릴리즈 감사 자료](https://github.com/dragon1086/prism-insight/blob/v2.26.0/docs/release_audits/v2.26.0.json)에 있습니다.
PR 연결은 제목 추측이 아니라 GitHub가 기록한 병합 커밋과 병합 부모 사이 Git 도달 가능성으로 확인했습니다.

<details>
<summary>PR 없이 들어간 직접 커밋 85개 펼치기</summary>

| 커밋 | 작성일 | 제목 |
|---|---|---|
| `0516a309` | 2026-10-28 | docs(research): H-1028-hardgate screening hard-gate prefilter not adopted |
| `c1a1c7e1` | 2026-10-29 | docs(btc): record monitor timeout incident repair |
| `53941d9c` | 2026-10-30 | docs(btc): record own native stop false halt repair |
| `0576ec1c` | 2026-10-30 | docs: preserve BTC backup isolation lessons |
| `afc6a6d2` | 2026-11-04 | docs: record H-1103 conditional replacement result (not adopted) |
| `67416082` | 2026-11-06 | docs: record trailing-band regime study (H-1106-trailband) |
| `e4393c6e` | 2026-11-06 | docs: record trailing-band candidate-population study (H-1106-trailband-v2) |
| `cd2e52a3` | 2026-11-06 | docs: record post-trailing reentry and runner threshold studies (H-1106) |
| `75565ad9` | 2026-11-06 | docs: record sell-lever grid study (H-1106-sell-levers) |
| `c476ce62` | 2026-11-07 | docs: record sell-lever v2 regime study (H-1106-sell-levers v2) |
| `1f728c46` | 2026-11-08 | docs: record re-entry research conclusions 11/06-11/08 and register H-1108-resilient |
| `390d041c` | 2026-11-08 | docs: register re-entry shakeout volume threshold hypothesis (Ahnlab near miss) |
| `2494e010` | 2026-11-09 | docs: H-1108-shakeout-vol replay result (rejected in KR and US) |
| `a95f6c4a` | 2026-11-09 | docs: re-entry v3 by regime on real stop-outs only (KR bull positive, US negative) |
| `e2216a30` | 2026-11-09 | docs: US re-entry stop variants (S3 close-confirm passes formally); allow per-market re-entry stop |
| `a15e3d0b` | 2026-11-09 | docs: re-entry exit management 9 variants + bull-only post-hoc (US whipsaw grace, KR bull MA50) |
| `8cb99eb8` | 2026-11-09 | docs: re-entry profit-structure search R1-R6 (KR bull-only sizing, US thin edge, timing switches rejected) |
| `b1761e98` | 2026-11-09 | docs: R7 long-history check rejects re-entry stop grace variants |
| `f7fea943` | 2026-11-09 | docs: record re-entry account replay rounds R8-R10 |
| `a0c29e7a` | 2026-11-09 | docs: record R11 O'Neil MA/volume re-entry filter result (all 16 arms fail) |
| `ec11fd6a` | 2026-11-09 | docs: record R12 test-design audit and MA-only re-entry filter result |
| `04961fd6` | 2026-11-09 | docs: mark H-1109 bull re-entry full size LIVE |
| `063fe52c` | 2026-11-09 | docs: record 5-year sell-lever study v3-v6 (H-1106-sell-levers, H-1109-bull-ma50-hold) |
| `672bd1b7` | 2026-11-09 | docs: record 11-09 re-entry AI v1/v2 and bear-market selection research |
| `adee8dfa` | 2026-11-10 | docs: first-entry studies 11-09 (next-day confirm, leader entry funnel, regime gauge, T-B AI replay) |
| `82241ad5` | 2026-11-10 | docs: first-entry regime sizing and T-B report overlap count (11-10) |
| `c49f8550` | 2026-11-10 | docs: narrow regime-gauge closure to the gauge method; index regime definition untested |
| `b0e149e6` | 2026-11-10 | docs: index regime definition review (H-1110-regime-def) and US breadth regime hypothesis |
| `be7f7432` | 2026-11-10 | docs: US regime redesign study (H-1110-us-regime-v2) and FTD-phase hypotheses |
| `1c4b91e9` | 2026-11-10 | docs: US six-stage regime redefinition (K8b shadow approved), KR kept |
| `bac5d8dc` | 2026-11-10 | docs: record US K8b shadow deployment |
| `868cedc3` | 2026-11-10 | docs: US K8b regime is LIVE |
| `01b77255` | 2026-11-10 | docs: record H-1110 re-entry trend gate and US half size |
| `cf1f8fd4` | 2026-11-10 | docs: H-1109-tight-nonbull LIVE for KR and US |
| `f9e79f2c` | 2026-11-10 | docs: US new-buy by K8b bull age lesson |
| `cc0dba8d` | 2026-11-10 | docs: H-1109-tight-nonbull post-hoc check (slots never bind) and stop rule |
| `aaf33d63` | 2026-11-10 | docs: regime-size US rejection re-verified under K8b labels |
| `a1f35975` | 2026-11-10 | docs: bear-select US re-scored under K8b; A9 registered as observe-only |
| `d8ec5c7c` | 2026-11-10 | docs(research): steady-leader 2b BUY-prompt A/B replay — direction mismatch, not adopted |
| `536431d0` | 2026-11-10 | docs(research): rejection-gate outcome study — no first-entry gate to relax; sector-cap eff8 bull registered observe-only |
| `81cab81a` | 2026-11-10 | docs(research): bull fundamental-gate BUY-prompt A/B — direction mismatch, not adopted |
| `67630e01` | 2026-11-10 | docs(research): 2a breakout-target threshold 95→90 BUY replay — rejected, (b) resistance condition is the bottleneck |
| `54fb6b9e` | 2026-11-10 | docs(research): US fundamental-gate data gaps — sparse yfinance quarter (fixed #1349) vs banks without operating income |
| `56b3821f` | 2026-11-11 | docs(sector): record US financial F1/F2 gate deployment (3a072015) |
| `54b7c082` | 2026-11-11 | docs(research): trading prompt diet — audit, user decisions, old-vs-new replay check (#1352) |
| `58cc60cd` | 2026-11-11 | docs(research): note first scheduled run of the sparse-quarter table fix |
| `e7420018` | 2026-11-11 | docs: record sell-guard bridge study and replay-vs-production peak gap (H-1109-tight-nonbull) |
| `d5f0eabb` | 2026-11-11 | docs: tight non-bull sell guard back to KR only (US off, 2026-11-11 user decision) |
| `aff7b1cf` | 2026-11-12 | docs: register H-1112 positioning hypotheses (KR short/loan/credit, BTC OI, K200 futures OI regime) — replay only |
| `411c1b7a` | 2026-11-13 | docs(flags): record PULSE_PILOT_REEXPOSURE turned off on db-server (2026-11-13) |
| `6ba25633` | 2026-11-13 | docs(flags): correct when PULSE_PILOT_REEXPOSURE was found on (8/29 backup, 7/12 release notes) |
| `1bdd2922` | 2026-11-13 | docs(flags): record four theory-only regime flags turned off/shadow on db-server (2026-11-13) |
| `49945ad6` | 2026-11-13 | docs: H-1112 positioning research (KR short/loan/credit long+short, BTC OI, K200 futures OI) — all no adoption |
| `3476d9c5` | 2026-11-14 | docs: record MA-break sell grace check (real trades, all worse) |
| `3e4d1902` | 2026-11-14 | docs: inverse lane improvement replay (OI exit, credit switch rejected; KOSDAQ rally-fail entry weak pass) |
| `e288b82b` | 2026-11-15 | docs(research): weak-regime candidate stop-out filter screen, 71 filters, none adopted (2026-11-15) |
| `7c7e7afe` | 2026-11-15 | docs(research): fix typo in 11-15 row |
| `35b09978` | 2026-11-15 | docs(research): bear stage and elapsed-days entry study, no cell passed (2026-11-15) |
| `06c0aba1` | 2026-11-16 | docs(research): re-entry funnel from production records — prize is large, three design layers miss it (2026-11-16) |
| `8fd5cfbd` | 2026-11-16 | docs(research): re-entry redesign — strength-recovery signals pass in US, KR bull-only (2026-11-16) |
| `1028ebb1` | 2026-11-16 | docs(research): re-entry redesign account check — US MA20-recovery re-entry +48%p, KR crowded out (2026-11-16) |
| `5eadc908` | 2026-11-16 | docs: consolidate rules before growing code or prompts |
| `9b171ae3` | 2026-11-16 | docs: re-entry 11.6h first scheduled run dates after deployment |
| `7b3f1c9c` | 2026-11-18 | docs: market pulse batch policy back to live (2026-11-18, user decision) |
| `62082093` | 2026-11-19 | docs: record the 2026-11-19 exam-day session override and cron shift |
| `1ba5b4b0` | 2026-11-21 | docs: register US follow-up hypotheses and record 43-45 research conclusions |
| `f0649c16` | 2026-11-21 | docs: mark 52w-breakout trigger 5y evidence as unconfirmed (survivorship), record round 46 |
| `9913ec3d` | 2026-11-21 | docs: register US narrow-rally and high-vol hypotheses, record regime-map research (47-48) |
| `2c04737a` | 2026-11-21 | docs: record follow-up falsification of the US narrow-rally hypothesis (round 49) |
| `b40b048d` | 2026-11-21 | docs: record staircase-criterion research (50-51) and register KR core-hold drawdown hypothesis |
| `01ca63ee` | 2026-11-21 | docs: record bull money-slot LIVE deployment and register KR accumulation-day hypothesis |
| `ef411375` | 2026-11-23 | docs: registry — 58th-round re-measure weakens two observe hypotheses; close vspike |
| `43ff3783` | 2026-11-23 | docs: registry — 59th-round 10-draw re-measure of three observe hypotheses |
| `f3da411a` | 2026-11-23 | docs: research lessons — US structure search rounds 60-76 (no adopted edge, verdicts unconfirmed) |
| `ecd95020` | 2026-11-24 | docs: withdraw the KR re-entry off advice after the full-ops recheck (2026-11-24) |
| `6e1796ef` | 2026-11-25 | docs: research lessons — US engine repair and re-judgement rounds 77-82 (no adopted edge, gap-hold trigger inconclusive) |
| `116fa778` | 2026-11-26 | docs: research lessons — holding-side levers round 83 (AI early sells, US sector cap, trend-weakness sell: none adopted) |
| `920680f9` | 2026-11-26 | docs: research lessons — engine repair round 84 (held-off re-entry watch source; US baseline lower, adopted plan holds) |
| `15535b0d` | 2026-11-26 | docs: research lessons — held-off re-entry source design arms (round 85, no arm passes, measurement only) |
| `1a6368a5` | 2026-11-26 | docs: research lessons — why level-based re-entry is not reproduced by the engine (round 86) |
| `b0e96417` | 2026-11-26 | docs: research lessons — US re-entry design round 87 (held-off source: off has the strongest evidence, no arm passes; engine order fix) |
| `9806853d` | 2026-11-26 | docs: research lessons — adopted rule set re-judged by approval seed, KR re-entry engine repair (round 88) |
| `5bc67da4` | 2026-11-26 | docs: research lessons — KR re-entry buy-gate gap and post-re-entry adds (round 89) |
| `f7909cf7` | 2026-11-27 | docs: research lessons — bull-only re-entry and add size both fail to qualify (round 90) |
| `69f993bd` | 2026-11-27 | docs: research lessons — re-entry slot-yield rules all fail (round 91) |

</details>

## 개발자용 상세 — PR별 변경 규모

<details>
<summary>병합 PR 255개 펼치기</summary>

| PR | 제목 | 규모 |
|---|---|---|
| [#1157](https://github.com/dragon1086/prism-insight/pull/1157) | fix: preserve pending confirmation for cancellation-only BTC WAIT | 3 files, +98/−2 |
| [#1158](https://github.com/dragon1086/prism-insight/pull/1158) | feat: BTC 두 시간프레임 동시 수렴 시 신규 진입 제한 | 16 files, +530/−17 |
| [#1159](https://github.com/dragon1086/prism-insight/pull/1159) | docs: BTC 동시 수렴 제한 배포와 첫 정기 판단 기록 | 2 files, +10/−0 |
| [#1160](https://github.com/dragon1086/prism-insight/pull/1160) | fix: distinguish BTC adjustment cancellation pending | 3 files, +64/−3 |
| [#1161](https://github.com/dragon1086/prism-insight/pull/1161) | fix(kr-snapshot): exclude trading issue with zero master cap instead of aborting batch | 2 files, +38/−3 |
| [#1162](https://github.com/dragon1086/prism-insight/pull/1162) | fix(us-trading): retry US price query on KIS per-second limit (EGW00201) | 2 files, +38/−0 |
| [#1163](https://github.com/dragon1086/prism-insight/pull/1163) | fix(b3): record a stale US quote as WAIT in the shadow ladder | 2 files, +23/−1 |
| [#1164](https://github.com/dragon1086/prism-insight/pull/1164) | fix(b3): exit cleanly when systemd stops the worker between ticks | 2 files, +25/−1 |
| [#1165](https://github.com/dragon1086/prism-insight/pull/1165) | fix(watchdog): ignore library errors the program already handles | 2 files, +8/−3 |
| [#1166](https://github.com/dragon1086/prism-insight/pull/1166) | fix: reject missing prior volume in BTC replay | 2 files, +93/−1 |
| [#1167](https://github.com/dragon1086/prism-insight/pull/1167) | fix: preserve BTC model timeout category in replay | 3 files, +50/−1 |
| [#1168](https://github.com/dragon1086/prism-insight/pull/1168) | docs: record BTC replay and convergence evidence | 2 files, +12/−0 |
| [#1169](https://github.com/dragon1086/prism-insight/pull/1169) | fix: bind BTC review transport source identity | 3 files, +28/−2 |
| [#1170](https://github.com/dragon1086/prism-insight/pull/1170) | fix: identify BTC incident monitor timeout checkpoint | 2 files, +40/−3 |
| [#1171](https://github.com/dragon1086/prism-insight/pull/1171) | fix(b3): wait instead of erroring when Yahoo returns no quote | 3 files, +9/−3 |
| [#1172](https://github.com/dragon1086/prism-insight/pull/1172) | docs: record BTC execution replay limitations | 3 files, +29/−0 |
| [#1173](https://github.com/dragon1086/prism-insight/pull/1173) | fix: inspect BTC research guard without stopping calls | 3 files, +93/−1 |
| [#1175](https://github.com/dragon1086/prism-insight/pull/1175) | fix: record BTC monitor deadline call timing | 5 files, +209/−11 |
| [#1176](https://github.com/dragon1086/prism-insight/pull/1176) | fix: classify BTC fresh safety rejection without raw values | 4 files, +41/−2 |
| [#1177](https://github.com/dragon1086/prism-insight/pull/1177) | fix: preserve BTC submission uncertainty in health events | 3 files, +28/−0 |
| [#1178](https://github.com/dragon1086/prism-insight/pull/1178) | fix: read compressed BTC review records in exporter | 3 files, +122/−7 |
| [#1179](https://github.com/dragon1086/prism-insight/pull/1179) | fix: read compressed BTC review evidence in runtime | 12 files, +196/−8 |
| [#1180](https://github.com/dragon1086/prism-insight/pull/1180) | fix: compress newly stored BTC review tapes | 8 files, +73/−11 |
| [#1181](https://github.com/dragon1086/prism-insight/pull/1181) | fix: prepare BTC recovery event readers for compression | 8 files, +66/−5 |
| [#1182](https://github.com/dragon1086/prism-insight/pull/1182) | fix: compress new BTC model wire records | 6 files, +67/−4 |
| [#1183](https://github.com/dragon1086/prism-insight/pull/1183) | fix(b3): treat a Yahoo bar miss in US add-plan inputs as missing input | 2 files, +21/−0 |
| [#1184](https://github.com/dragon1086/prism-insight/pull/1184) | fix(watchdog): alert when the server disk passes 90% | 2 files, +46/−0 |
| [#1185](https://github.com/dragon1086/prism-insight/pull/1185) | fix(watchdog): compute disk use like df | 1 files, +2/−1 |
| [#1186](https://github.com/dragon1086/prism-insight/pull/1186) | docs(knowledge): 256~257쪽, 5장 결론 (K-SIZE-02) | 3 files, +24/−4 |
| [#1187](https://github.com/dragon1086/prism-insight/pull/1187) | Prepare lossless readers for deduplicated BTC review tapes | 13 files, +564/−17 |
| [#1189](https://github.com/dragon1086/prism-insight/pull/1189) | Deduplicate newly stored BTC review tapes | 8 files, +149/−17 |
| [#1190](https://github.com/dragon1086/prism-insight/pull/1190) | Prepare lossless readers for BTC financial evidence references | 6 files, +268/−2 |
| [#1191](https://github.com/dragon1086/prism-insight/pull/1191) | Deduplicate new BTC financial evidence records | 3 files, +75/−1 |
| [#1192](https://github.com/dragon1086/prism-insight/pull/1192) | fix(btc): incident monitor timeout blocks only that cycle | 3 files, +22/−21 |
| [#1193](https://github.com/dragon1086/prism-insight/pull/1193) | Export observed BTC quote timing for execution replay | 3 files, +197/−0 |
| [#1194](https://github.com/dragon1086/prism-insight/pull/1194) | Prepare lossless BTC audit input reference readers | 11 files, +332/−7 |
| [#1195](https://github.com/dragon1086/prism-insight/pull/1195) | Deduplicate newly written BTC audit inputs | 2 files, +89/−1 |
| [#1196](https://github.com/dragon1086/prism-insight/pull/1196) | Prepare lossless BTC recovery reference readers | 10 files, +389/−10 |
| [#1197](https://github.com/dragon1086/prism-insight/pull/1197) | Deduplicate BTC recovery journal records | 5 files, +152/−8 |
| [#1198](https://github.com/dragon1086/prism-insight/pull/1198) | Use GPT-6.1 Terra for BTC demo scenarios | 9 files, +14/−14 |
| [#1199](https://github.com/dragon1086/prism-insight/pull/1199) | Compress BTC review event storage losslessly | 6 files, +37/−12 |
| [#1200](https://github.com/dragon1086/prism-insight/pull/1200) | Add backup-matched BTC review compaction | 8 files, +683/−0 |
| [#1201](https://github.com/dragon1086/prism-insight/pull/1201) | feat: compact backed BTC review tapes losslessly | 2 files, +218/−0 |
| [#1202](https://github.com/dragon1086/prism-insight/pull/1202) | feat: compact backed BTC recovery events losslessly | 2 files, +226/−0 |
| [#1203](https://github.com/dragon1086/prism-insight/pull/1203) | feat: compact backed BTC audit inputs losslessly | 2 files, +194/−0 |
| [#1204](https://github.com/dragon1086/prism-insight/pull/1204) | fix(trend-exit): 50일선 아래 매수 포지션은 50일선 위 종가 전까지 50일선 손실 매도 제외 | 6 files, +93/−7 |
| [#1205](https://github.com/dragon1086/prism-insight/pull/1205) | fix(telegram): resend once when a translated broadcast delivery is unknown | 4 files, +37/−12 |
| [#1206](https://github.com/dragon1086/prism-insight/pull/1206) | fix: reduce BTC account evidence storage | 4 files, +34/−3 |
| [#1207](https://github.com/dragon1086/prism-insight/pull/1207) | feat: prepare lossless BTC accounting audit references | 8 files, +252/−1 |
| [#1208](https://github.com/dragon1086/prism-insight/pull/1208) | fix: avoid duplicate BTC accounting audit evidence | 4 files, +141/−4 |
| [#1209](https://github.com/dragon1086/prism-insight/pull/1209) | fix(telegram): resend once for translated report and portfolio broadcasts | 6 files, +28/−6 |
| [#1210](https://github.com/dragon1086/prism-insight/pull/1210) | feat: prepare lossless BTC recovery input references | 7 files, +127/−6 |
| [#1211](https://github.com/dragon1086/prism-insight/pull/1211) | fix: avoid duplicate BTC recovery input evidence | 4 files, +78/−3 |
| [#1212](https://github.com/dragon1086/prism-insight/pull/1212) | fix: reduce SQLite backup staging disk usage | 5 files, +200/−22 |
| [#1213](https://github.com/dragon1086/prism-insight/pull/1213) | fix: complete SQLite backups within disk limits | 3 files, +17/−3 |
| [#1214](https://github.com/dragon1086/prism-insight/pull/1214) | feat: retain BTC review tapes for seven days | 13 files, +1,616/−9 |
| [#1215](https://github.com/dragon1086/prism-insight/pull/1215) | fix: respect backup deadline while scanning references | 3 files, +38/−3 |
| [#1216](https://github.com/dragon1086/prism-insight/pull/1216) | fix(telegram): log a Markdown rejection retried as plain text as a warning | 2 files, +17/−3 |
| [#1217](https://github.com/dragon1086/prism-insight/pull/1217) | fix(ops): treat a sell with no shares in the account as a skip, not an error | 4 files, +29/−5 |
| [#1218](https://github.com/dragon1086/prism-insight/pull/1218) | fix(trend-exit): 50일선 손실매도는 매수 당일 적용 안 함 | 6 files, +65/−6 |
| [#1220](https://github.com/dragon1086/prism-insight/pull/1220) | docs: record BTC storage safeguards and retention status | 3 files, +8/−1 |
| [#1221](https://github.com/dragon1086/prism-insight/pull/1221) | fix(btc): incident monitor treats own unsaved orders as owned | 2 files, +33/−2 |
| [#1222](https://github.com/dragon1086/prism-insight/pull/1222) | test: verify BTC WAIT retention against runtime output | 1 files, +15/−0 |
| [#1223](https://github.com/dragon1086/prism-insight/pull/1223) | fix: identify versioned BTC incident evidence | 2 files, +22/−1 |
| [#1224](https://github.com/dragon1086/prism-insight/pull/1224) | feat: read BTC model event tape references | 2 files, +72/−1 |
| [#1225](https://github.com/dragon1086/prism-insight/pull/1225) | feat: deduplicate BTC model calls in review tapes | 6 files, +89/−14 |
| [#1226](https://github.com/dragon1086/prism-insight/pull/1226) | fix: index BTC recovery evidence by decision slot | 2 files, +16/−0 |
| [#1227](https://github.com/dragon1086/prism-insight/pull/1227) | feat: prune verified recovery WAIT tapes after seven days | 3 files, +397/−13 |
| [#1228](https://github.com/dragon1086/prism-insight/pull/1228) | fix(prompts): 50일선 매수 당일 예외 문구 — 다음 거래일부터 적용 | 4 files, +8/−6 |
| [#1229](https://github.com/dragon1086/prism-insight/pull/1229) | fix: bound BTC archive receiver command size | 2 files, +56/−2 |
| [#1230](https://github.com/dragon1086/prism-insight/pull/1230) | fix: reduce BTC backup temporary disk allocation | 5 files, +408/−15 |
| [#1231](https://github.com/dragon1086/prism-insight/pull/1231) | fix(us): publish the BUY signal whatever our own KIS order did | 2 files, +40/−37 |
| [#1232](https://github.com/dragon1086/prism-insight/pull/1232) | fix: keep BTC replay and test events out of shared ledger | 7 files, +111/−10 |
| [#1233](https://github.com/dragon1086/prism-insight/pull/1233) | fix: prepare lossless BTC review event readers | 17 files, +491/−20 |
| [#1234](https://github.com/dragon1086/prism-insight/pull/1234) | test: blank ops-alert targets so a test run never reaches the maintenance chat | 2 files, +15/−0 |
| [#1236](https://github.com/dragon1086/prism-insight/pull/1236) | feat: remove duplicate BTC review request storage | 11 files, +855/−18 |
| [#1237](https://github.com/dragon1086/prism-insight/pull/1237) | fix: prevent wasteful PR runs and preserve BTC work lessons | 5 files, +24/−2 |
| [#1238](https://github.com/dragon1086/prism-insight/pull/1238) | fix(market-data): retry a dropped KIS connection on read-only quotes | 2 files, +49/−1 |
| [#1239](https://github.com/dragon1086/prism-insight/pull/1239) | feat: restore deduplicated BTC accounting children | 3 files, +74/−9 |
| [#1240](https://github.com/dragon1086/prism-insight/pull/1240) | feat: reduce duplicate BTC audit and preserved tape storage | 6 files, +138/−8 |
| [#1241](https://github.com/dragon1086/prism-insight/pull/1241) | docs: preserve BTC research and avoid repeated experiments | 8 files, +345/−0 |
| [#1242](https://github.com/dragon1086/prism-insight/pull/1242) | fix: identify changed BTC safety context fields | 3 files, +53/−1 |
| [#1243](https://github.com/dragon1086/prism-insight/pull/1243) | docs: BTC 연구 비용과 저장공간 시행착오 기록 | 1 files, +3/−0 |
| [#1244](https://github.com/dragon1086/prism-insight/pull/1244) | feat: prepare lossless BTC snapshot readers | 20 files, +157/−11 |
| [#1245](https://github.com/dragon1086/prism-insight/pull/1245) | feat: compress BTC decision snapshots losslessly | 6 files, +38/−7 |
| [#1246](https://github.com/dragon1086/prism-insight/pull/1246) | feat: stage evidence-bound BTC WAIT event retention | 12 files, +2,006/−21 |
| [#1248](https://github.com/dragon1086/prism-insight/pull/1248) | docs: respect protected branch rules for BTC notes | 1 files, +1/−0 |
| [#1249](https://github.com/dragon1086/prism-insight/pull/1249) | Compress BTC recovery rechecks without losing evidence | 5 files, +60/−3 |
| [#1250](https://github.com/dragon1086/prism-insight/pull/1250) | docs: prevent BTC evidence classification mistakes | 1 files, +7/−0 |
| [#1251](https://github.com/dragon1086/prism-insight/pull/1251) | docs: record BTC replay and observation efficiency lessons | 1 files, +4/−0 |
| [#1252](https://github.com/dragon1086/prism-insight/pull/1252) | fix: compress BTC account evidence losslessly | 12 files, +131/−17 |
| [#1253](https://github.com/dragon1086/prism-insight/pull/1253) | fix(sell): the afternoon review's price counts as today's close (KR/US) | 8 files, +198/−80 |
| [#1254](https://github.com/dragon1086/prism-insight/pull/1254) | feat: add bounded backup-matched BTC account compaction | 8 files, +1,301/−3 |
| [#1255](https://github.com/dragon1086/prism-insight/pull/1255) | docs: BTC 운영 시행착오를 하네스에 반영 | 2 files, +14/−0 |
| [#1256](https://github.com/dragon1086/prism-insight/pull/1256) | fix(messaging): do not repeat a stored AI reason in the hold message | 2 files, +13/−1 |
| [#1257](https://github.com/dragon1086/prism-insight/pull/1257) | fix(us-trading): a failed balance query is unknown holdings, not an empty portfolio | 2 files, +93/−3 |
| [#1258](https://github.com/dragon1086/prism-insight/pull/1258) | fix: BTC 계좌 압축과 B3 읽기 공존 | 3 files, +212/−8 |
| [#1259](https://github.com/dragon1086/prism-insight/pull/1259) | fix(reentry): skip a live re-entry signal without a BUY target instead of erroring | 2 files, +14/−1 |
| [#1260](https://github.com/dragon1086/prism-insight/pull/1260) | fix: SQLite 백업 실패를 백업 로그에 기록 | 2 files, +35/−3 |
| [#1261](https://github.com/dragon1086/prism-insight/pull/1261) | fix: 백업 스크립트 실행 권한 복구 | 2 files, +8/−0 |
| [#1262](https://github.com/dragon1086/prism-insight/pull/1262) | fix: BTC 7일 정리를 재부팅 후에도 안전하게 실행 | 9 files, +330/−35 |
| [#1263](https://github.com/dragon1086/prism-insight/pull/1263) | fix: allow BTC daily retention service on SELinux | 3 files, +6/−2 |
| [#1264](https://github.com/dragon1086/prism-insight/pull/1264) | docs: distinguish BTC proposal from final review | 1 files, +2/−0 |
| [#1265](https://github.com/dragon1086/prism-insight/pull/1265) | fix: read compressed BTC accounting audit evidence | 4 files, +59/−16 |
| [#1266](https://github.com/dragon1086/prism-insight/pull/1266) | perf: compress BTC accounting audit evidence | 3 files, +36/−5 |
| [#1267](https://github.com/dragon1086/prism-insight/pull/1267) | perf: compact verified BTC accounting audit history | 4 files, +522/−0 |
| [#1271](https://github.com/dragon1086/prism-insight/pull/1271) | fix: prevent BTC history lookup from blocking protection | 3 files, +106/−6 |
| [#1272](https://github.com/dragon1086/prism-insight/pull/1272) | fix: measure BTC incident monitor stages | 2 files, +97/−2 |
| [#1273](https://github.com/dragon1086/prism-insight/pull/1273) | fix: weigh narrow BTC MA bands before entries | 3 files, +17/−1 |
| [#1274](https://github.com/dragon1086/prism-insight/pull/1274) | fix: preserve safe BTC pending execution diagnostics | 2 files, +30/−0 |
| [#1275](https://github.com/dragon1086/prism-insight/pull/1275) | fix(us-memory): keep new US principles through weekly stale cleanup | 2 files, +40/−4 |
| [#1276](https://github.com/dragon1086/prism-insight/pull/1276) | fix(memory): drop BUY intuitions that excuse an enforced rule or use a theme as the reason | 2 files, +17/−0 |
| [#1278](https://github.com/dragon1086/prism-insight/pull/1278) | fix: clarify BTC single-frame convergence guidance | 3 files, +3/−3 |
| [#1280](https://github.com/dragon1086/prism-insight/pull/1280) | feat: 증액 계획 원장에 돌파 가격·추격 한도 기록 | 6 files, +29/−5 |
| [#1281](https://github.com/dragon1086/prism-insight/pull/1281) | fix: preserve filled BTC targets during adjustment | 8 files, +676/−13 |
| [#1282](https://github.com/dragon1086/prism-insight/pull/1282) | fix: allow safe reduction after partial exit fills | 2 files, +252/−6 |
| [#1283](https://github.com/dragon1086/prism-insight/pull/1283) | fix: prevent BTC research calls from overlapping scheduled decisions | 8 files, +546/−7 |
| [#1284](https://github.com/dragon1086/prism-insight/pull/1284) | BTC 진입 판단에 거래량 분포와 이평선 거리 제공 | 10 files, +269/−12 |
| [#1285](https://github.com/dragon1086/prism-insight/pull/1285) | fix(market-data): re-auth once when a cached KIS client's token expires | 4 files, +76/−1 |
| [#1286](https://github.com/dragon1086/prism-insight/pull/1286) | BTC: 확인된 보유 가격 움직임 요약 | 4 files, +327/−6 |
| [#1287](https://github.com/dragon1086/prism-insight/pull/1287) | BTC: 포지션 없는 선택 지시 축약 | 4 files, +214/−1 |
| [#1288](https://github.com/dragon1086/prism-insight/pull/1288) | BTC: 계산된 확정봉 가격 진행 제공 | 2 files, +125/−4 |
| [#1289](https://github.com/dragon1086/prism-insight/pull/1289) | fix: align BTC gap thesis with v2 entry contract | 3 files, +26/−12 |
| [#1290](https://github.com/dragon1086/prism-insight/pull/1290) | fix: reuse BTC retention candidate inventory | 4 files, +137/−4 |
| [#1291](https://github.com/dragon1086/prism-insight/pull/1291) | fix: align BTC event retention with safe window | 2 files, +9/−9 |
| [#1292](https://github.com/dragon1086/prism-insight/pull/1292) | fix: show final BTC wait selection reason | 2 files, +95/−4 |
| [#1293](https://github.com/dragon1086/prism-insight/pull/1293) | fix: scope management of unsettled BTC entry | 7 files, +1,510/−15 |
| [#1294](https://github.com/dragon1086/prism-insight/pull/1294) | fix: reject unsupported BTC backtest exit attachments | 2 files, +52/−0 |
| [#1295](https://github.com/dragon1086/prism-insight/pull/1295) | fix: cancel marketable PostOnly orders in BTC replay | 2 files, +68/−0 |
| [#1296](https://github.com/dragon1086/prism-insight/pull/1296) | fix: use standard OAuth tier for BTC decisions | 14 files, +50/−31 |
| [#1297](https://github.com/dragon1086/prism-insight/pull/1297) | fix: 매도 수익률 합계를 차트와 같은 슬롯 가중으로, 누적 수익률 차트 2배 확대 | 3 files, +8/−6 |
| [#1298](https://github.com/dragon1086/prism-insight/pull/1298) | fix: fill missing BUY sector, US hard stop only in regular hours, keep queued sell quantity | 12 files, +131/−10 |
| [#1299](https://github.com/dragon1086/prism-insight/pull/1299) | fix: 별도 서버 백업이 없는 서버도 DB 사본을 자동 정리 | 2 files, +101/−28 |
| [#1300](https://github.com/dragon1086/prism-insight/pull/1300) | fix: count add-plan pullback zone only after the rise through it | 5 files, +47/−3 |
| [#1301](https://github.com/dragon1086/prism-insight/pull/1301) | feat: biweekly review replaces the watchdog's daily summary and notices | 7 files, +92/−2 |
| [#1303](https://github.com/dragon1086/prism-insight/pull/1303) | feat: BTC 장세·진입 목적별 시나리오 판단 정리 | 4 files, +52/−22 |
| [#1304](https://github.com/dragon1086/prism-insight/pull/1304) | fix: keep volume-increase conditions off breakout and pullback add scenarios | 4 files, +25/−5 |
| [#1305](https://github.com/dragon1086/prism-insight/pull/1305) | fix: stop backing up the torn app-server DB copy and give db-server snapshots more time | 2 files, +25/−57 |
| [#1306](https://github.com/dragon1086/prism-insight/pull/1306) | feat(reentry): re-entry first size factor (REENTRY_V3_FIRST_SIZE_FACTOR) | 3 files, +76/−4 |
| [#1307](https://github.com/dragon1086/prism-insight/pull/1307) | docs: add size and chase limit study (not adopted) | 1 files, +4/−2 |
| [#1308](https://github.com/dragon1086/prism-insight/pull/1308) | fix: raise AI-written add chase limits to a 1.5% floor | 6 files, +29/−7 |
| [#1309](https://github.com/dragon1086/prism-insight/pull/1309) | feat: record each add scenario's wait reasons once per session | 4 files, +67/−1 |
| [#1310](https://github.com/dragon1086/prism-insight/pull/1310) | feat: restore pyramiding as independent micro-split rows (flagged) | 26 files, +1,382/−56 |
| [#1311](https://github.com/dragon1086/prism-insight/pull/1311) | docs: pyramiding rows are LIVE (2026-11-08) | 1 files, +2/−1 |
| [#1312](https://github.com/dragon1086/prism-insight/pull/1312) | feat: 2주 점검에 피라미딩·칸 부족·증액 대기 사유 추가 | 3 files, +137/−3 |
| [#1313](https://github.com/dragon1086/prism-insight/pull/1313) | feat: 구독자가 피라미딩 칸 증액·매도를 따라가게 | 6 files, +136/−22 |
| [#1314](https://github.com/dragon1086/prism-insight/pull/1314) | docs: 증액 연구 정정(장중 체결)·전일 고가 가설 등록 | 2 files, +2/−0 |
| [#1315](https://github.com/dragon1086/prism-insight/pull/1315) | docs: 전일 고가 증액 트리거 AI 재생 결과(미채택) | 2 files, +2/−1 |
| [#1316](https://github.com/dragon1086/prism-insight/pull/1316) | fix: BTC 신규 진입 손익비 검사와 보유 판단 수치 보완 | 17 files, +561/−25 |
| [#1317](https://github.com/dragon1086/prism-insight/pull/1317) | docs: 장중 체결로 피라미딩·추격 하한·증액 크기 재확인 | 2 files, +2/−0 |
| [#1318](https://github.com/dragon1086/prism-insight/pull/1318) | docs: KR·US·공통 일회성 기록 정리(결론은 연구 기록으로) | 23 files, +16/−1,305 |
| [#1319](https://github.com/dragon1086/prism-insight/pull/1319) | docs: BTC 일회성 기록 정리(결론은 로드맵으로) | 83 files, +91/−3,564 |
| [#1321](https://github.com/dragon1086/prism-insight/pull/1321) | feat: compare BTC entry economics before drafting candidates | 7 files, +329/−10 |
| [#1322](https://github.com/dragon1086/prism-insight/pull/1322) | feat: inverse ETF lane S1 skeleton + design v1.0 docs (no orders) | 41 files, +3,801/−3 |
| [#1323](https://github.com/dragon1086/prism-insight/pull/1323) | feat: 재진입 국면별 첫 비중 SHADOW (H-1109) | 6 files, +98/−4 |
| [#1324](https://github.com/dragon1086/prism-insight/pull/1324) | docs: inverse lane S2 result - dev-period rejection | 5 files, +41/−17 |
| [#1325](https://github.com/dragon1086/prism-insight/pull/1325) | docs: leader-group inverse S3 result (H-1109-leader-group-inverse) | 5 files, +18/−2 |
| [#1326](https://github.com/dragon1086/prism-insight/pull/1326) | feat(reentry): 강세장 재진입 첫 비중 원래대로 (H-1109 LIVE 스위치) | 8 files, +75/−4 |
| [#1327](https://github.com/dragon1086/prism-insight/pull/1327) | docs: inverse two-track S4 + weight-coupling S5 results | 5 files, +26/−2 |
| [#1328](https://github.com/dragon1086/prism-insight/pull/1328) | docs: inverse sector-driver S6 result (H-1109-inverse-sector-driver) | 3 files, +13/−0 |
| [#1329](https://github.com/dragon1086/prism-insight/pull/1329) | docs: US bear early-detection S7 result (H-1109-us-bear-early) | 3 files, +11/−1 |
| [#1330](https://github.com/dragon1086/prism-insight/pull/1330) | feat(inverse): KR inverse signal observe recorder (no orders, off by default) | 12 files, +961/−4 |
| [#1331](https://github.com/dragon1086/prism-insight/pull/1331) | fix(kis-auth): 토큰 동시 발급 직렬화 (EGW00133 1분당 1회 제한) | 2 files, +49/−27 |
| [#1332](https://github.com/dragon1086/prism-insight/pull/1332) | docs: US bear confirmation S8 result + KR inverse observe first run | 3 files, +12/−0 |
| [#1333](https://github.com/dragon1086/prism-insight/pull/1333) | docs: KR inverse first-break x bear regime (exploratory, registered) | 2 files, +2/−0 |
| [#1334](https://github.com/dragon1086/prism-insight/pull/1334) | feat: adapt BTC holding protection and consolidate exit guidance | 5 files, +334/−2 |
| [#1335](https://github.com/dragon1086/prism-insight/pull/1335) | feat: KR inverse observe - add KQ_X10 signal (no orders) | 7 files, +51/−20 |
| [#1336](https://github.com/dragon1086/prism-insight/pull/1336) | feat(sell): trail early and tight for positions bought in sideways/bear regimes (H-1109-tight-nonbull) | 13 files, +218/−56 |
| [#1337](https://github.com/dragon1086/prism-insight/pull/1337) | docs: inverse S12 - KR tuning, US single-stock inverse (exploratory) | 2 files, +6/−0 |
| [#1339](https://github.com/dragon1086/prism-insight/pull/1339) | feat: record US K8b regime label as SHADOW next to the production regime | 2 files, +214/−0 |
| [#1340](https://github.com/dragon1086/prism-insight/pull/1340) | fix(ci): run the CLA check on same-repo pull_request events | 2 files, +16/−2 |
| [#1341](https://github.com/dragon1086/prism-insight/pull/1341) | feat: US K8b regime LIVE switch | 3 files, +39/−4 |
| [#1342](https://github.com/dragon1086/prism-insight/pull/1342) | feat(reentry): KR 50-day line gate, US bull re-entry back to half size | 8 files, +95/−7 |
| [#1343](https://github.com/dragon1086/prism-insight/pull/1343) | feat: give BTC holding plans explicit upside and wider noise references | 5 files, +234/−4 |
| [#1344](https://github.com/dragon1086/prism-insight/pull/1344) | feat(reentry): US K8b early-bull re-entry gate, bull-age size arms (record only) | 14 files, +210/−24 |
| [#1345](https://github.com/dragon1086/prism-insight/pull/1345) | feat: inverse recorders — S18 exits, KR KQ_FR/K200_SEMI, US 28-sector recorder | 7 files, +390/−83 |
| [#1346](https://github.com/dragon1086/prism-insight/pull/1346) | docs: inverse S17/S18 registered, recorder v3/US v1 deploy notes | 3 files, +5/−0 |
| [#1348](https://github.com/dragon1086/prism-insight/pull/1348) | feat(add-plan): 종가 확인 추가매수를 마감 직전 판단·당일 매수로 (KR 15:20 동시호가 / US 마감 15분 전) | 3 files, +86/−26 |
| [#1349](https://github.com/dragon1086/prism-insight/pull/1349) | fix(us-report): quarterly table marks source-unreported quarters instead of N/A cells | 2 files, +54/−3 |
| [#1350](https://github.com/dragon1086/prism-insight/pull/1350) | feat(us-buy): deterministic F1/F2 block for financials without an operating income line | 4 files, +313/−0 |
| [#1351](https://github.com/dragon1086/prism-insight/pull/1351) | feat: inverse S5 live lane — one account slot, next-open reserved orders (default off) | 15 files, +560/−5 |
| [#1352](https://github.com/dragon1086/prism-insight/pull/1352) | refactor(prompts): trading prompt diet — BUY/SELL × KR/US × ko/en (audit D/X/S/M) | 23 files, +400/−728 |
| [#1353](https://github.com/dragon1086/prism-insight/pull/1353) | fix: inverse recorder default date = session date in the market's zone (US first run recorded 28 MISSING) | 2 files, +24/−2 |
| [#1354](https://github.com/dragon1086/prism-insight/pull/1354) | docs: inverse live lane on (11/11), US first-run date defect noted | 2 files, +2/−2 |
| [#1355](https://github.com/dragon1086/prism-insight/pull/1355) | fix: preserve BTC OAuth failures instead of reporting contract errors | 11 files, +174/−13 |
| [#1356](https://github.com/dragon1086/prism-insight/pull/1356) | fix(add-plan): coerce the KR string quote price in the provisional close bar | 2 files, +3/−0 |
| [#1357](https://github.com/dragon1086/prism-insight/pull/1357) | fix(prompts): actual afternoon sell-review time + sell_triggers placeholder contract (diet follow-ups) | 6 files, +11/−9 |
| [#1358](https://github.com/dragon1086/prism-insight/pull/1358) | fix: skip prohibited flat BTC model reviews to avoid needless timeouts | 3 files, +45/−1 |
| [#1359](https://github.com/dragon1086/prism-insight/pull/1359) | fix: activate safe BTC flat observations and bounded usage replay | 13 files, +302/−41 |
| [#1360](https://github.com/dragon1086/prism-insight/pull/1360) | fix: prevent BTC backup accumulation from blocking retention | 5 files, +152/−3 |
| [#1361](https://github.com/dragon1086/prism-insight/pull/1361) | fix(kis-auth): wait out the per-minute token issue limit once instead of failing | 2 files, +48/−9 |
| [#1362](https://github.com/dragon1086/prism-insight/pull/1362) | fix: inverse live lane guards (validated families only, whole-holding sell, stuck-trade timeout) | 6 files, +45/−12 |
| [#1363](https://github.com/dragon1086/prism-insight/pull/1363) | fix: size BTC backup validation deadline for current archive volume | 2 files, +19/−1 |
| [#1364](https://github.com/dragon1086/prism-insight/pull/1364) | docs: anchored VWAP research across entry, adds, re-entry and sell (none adopted) | 1 files, +1/−0 |
| [#1365](https://github.com/dragon1086/prism-insight/pull/1365) | fix: BTC 익절 계획의 고정 달러 기준 제거 | 5 files, +26/−24 |
| [#1366](https://github.com/dragon1086/prism-insight/pull/1366) | feat: BTC 진입 예상비용과 최대손실 예비분 분리 | 10 files, +226/−18 |
| [#1367](https://github.com/dragon1086/prism-insight/pull/1367) | fix: BTC OAuth 응답 종료 이벤트 누락 방지 | 8 files, +227/−38 |
| [#1368](https://github.com/dragon1086/prism-insight/pull/1368) | feat: BTC 진입·손절·분할익절 연결 계획 사전 검산 | 10 files, +405/−20 |
| [#1369](https://github.com/dragon1086/prism-insight/pull/1369) | fix: micro-split adds fill the broker holding up to the allocation (main accounts + subscriber) | 5 files, +188/−38 |
| [#1370](https://github.com/dragon1086/prism-insight/pull/1370) | fix: a pyramid row's stop-loss is never zeroed by another row's unfilled add | 4 files, +78/−18 |
| [#1371](https://github.com/dragon1086/prism-insight/pull/1371) | fix(pyramid-rows): 행별 손절 수량을 주문이 아닌 체결 수량으로 | 6 files, +163/−12 |
| [#1372](https://github.com/dragon1086/prism-insight/pull/1372) | feat: inverse recorder adds KOSDAQ150 rally-fail family KQ_RFAIL (observe only) | 7 files, +77/−10 |
| [#1374](https://github.com/dragon1086/prism-insight/pull/1374) | fix: restore BTC take-profits during delayed execution history | 2 files, +159/−0 |
| [#1375](https://github.com/dragon1086/prism-insight/pull/1375) | fix: preserve BTC pullback choices within bounded entry menus | 2 files, +21/−3 |
| [#1376](https://github.com/dragon1086/prism-insight/pull/1376) | fix: cover resting BTC fills in pending target installation | 4 files, +64/−5 |
| [#1378](https://github.com/dragon1086/prism-insight/pull/1378) | feat: decision snapshot records the downtrend-rally 'do not buy' flags (ledger only) | 3 files, +94/−1 |
| [#1379](https://github.com/dragon1086/prism-insight/pull/1379) | fix(micro-split): add below one share orders one share within the slot | 2 files, +37/−4 |
| [#1380](https://github.com/dragon1086/prism-insight/pull/1380) | refactor: simplify BTC decision policy and remove duplicate model inputs | 64 files, +704/−2,074 |
| [#1381](https://github.com/dragon1086/prism-insight/pull/1381) | feat(reentry): observe-only MA20-recovery re-entry arm (US all, KR bull anchors) | 6 files, +95/−1 |
| [#1382](https://github.com/dragon1086/prism-insight/pull/1382) | fix: avoid scattered BTC cursor history reads without weakening validation | 4 files, +40/−3 |
| [#1383](https://github.com/dragon1086/prism-insight/pull/1383) | feat(reentry): US re-entry trades the MA20 recovery rule (REENTRY_V3_MA20R_LIVE_MARKETS) | 13 files, +244/−42 |
| [#1384](https://github.com/dragon1086/prism-insight/pull/1384) | fix: preserve full BTC exit quantities without rounding leftovers | 6 files, +88/−26 |
| [#1385](https://github.com/dragon1086/prism-insight/pull/1385) | feat: allow goal-compatible BTC exit allocations | 7 files, +85/−17 |
| [#1386](https://github.com/dragon1086/prism-insight/pull/1386) | feat(reentry): judge and buy near the close (KR 15:08 / US 15:30 ET) with measured volume shares | 7 files, +49/−27 |
| [#1387](https://github.com/dragon1086/prism-insight/pull/1387) | fix: exclude forming candle closes from BTC pullback anchors | 4 files, +62/−1 |
| [#1388](https://github.com/dragon1086/prism-insight/pull/1388) | feat: restore reusable fixed-wave BTC backtest benchmarks | 5 files, +142/−7 |
| [#1389](https://github.com/dragon1086/prism-insight/pull/1389) | fix: restore minimum15m BTC timing analysis | 15 files, +151/−42 |
| [#1390](https://github.com/dragon1086/prism-insight/pull/1390) | fix: BTC 신규 진입 위험 상한 제안 (사용자 선택 전 적용 보류) | 8 files, +105/−11 |
| [#1391](https://github.com/dragon1086/prism-insight/pull/1391) | fix: BTC 과거 재생의 합성 봉을 가격 근거에서 제외 | 5 files, +69/−1 |
| [#1392](https://github.com/dragon1086/prism-insight/pull/1392) | fix: BTC 보유 판단에서 15분 반응과 수익 반납 비교 | 8 files, +97/−8 |
| [#1393](https://github.com/dragon1086/prism-insight/pull/1393) | feat(ops): broker-vs-ledger holdings reconcile for corporate actions (BKH→HZN) | 4 files, +591/−0 |
| [#1394](https://github.com/dragon1086/prism-insight/pull/1394) | fix: BTC 진입방식 안의 비용 통과 대안 누락 보완 | 6 files, +64/−1 |
| [#1395](https://github.com/dragon1086/prism-insight/pull/1395) | fix(ops): holdings reconcile skips simulator-only holdings | 2 files, +33/−9 |
| [#1396](https://github.com/dragon1086/prism-insight/pull/1396) | fix(ops): holdings reconcile compares legacy_holding_id as text | 2 files, +5/−5 |
| [#1397](https://github.com/dragon1086/prism-insight/pull/1397) | fix(ops): watchdog re-entry deadlines follow the new run times | 1 files, +3/−2 |
| [#1398](https://github.com/dragon1086/prism-insight/pull/1398) | fix(btc): evaluate fixed opportunity windows continuously and report review gaps | 3 files, +86/−0 |
| [#1400](https://github.com/dragon1086/prism-insight/pull/1400) | fix(btc): isolate research calls from active operating model incidents | 4 files, +66/−12 |
| [#1401](https://github.com/dragon1086/prism-insight/pull/1401) | fix(btc): honour provider overload cooldown without interrupting protection | 12 files, +233/−18 |
| [#1402](https://github.com/dragon1086/prism-insight/pull/1402) | test: keep the full-ops research engine in step with production rules | 3 files, +62/−0 |
| [#1404](https://github.com/dragon1086/prism-insight/pull/1404) | fix(btc): preserve entry evidence when adding stop alternatives | 4 files, +54/−7 |
| [#1405](https://github.com/dragon1086/prism-insight/pull/1405) | fix(btc): stream every historical cursor without retaining full history | 6 files, +52/−11 |
| [#1406](https://github.com/dragon1086/prism-insight/pull/1406) | fix(btc): preserve holding reviews between sampled entry decisions | 3 files, +109/−7 |
| [#1407](https://github.com/dragon1086/prism-insight/pull/1407) | fix(us): retry an empty KIS quote before skipping a buy review | 2 files, +30/−10 |
| [#1408](https://github.com/dragon1086/prism-insight/pull/1408) | fix: keep partial reductions available when recovery entry gate changes | 3 files, +53/−1 |
| [#1409](https://github.com/dragon1086/prism-insight/pull/1409) | feat(kr): date-scoped KRX session override for special trading hours | 3 files, +45/−9 |
| [#1410](https://github.com/dragon1086/prism-insight/pull/1410) | feat: show BTC research review, call and settlement evidence separately | 5 files, +734/−0 |
| [#1411](https://github.com/dragon1086/prism-insight/pull/1411) | fix: keep US dashboard index series when KIS index lookup fails | 2 files, +40/−1 |
| [#1412](https://github.com/dragon1086/prism-insight/pull/1412) | fix: allow strictly protective tightening through recovery gate changes | 6 files, +134/−14 |
| [#1413](https://github.com/dragon1086/prism-insight/pull/1413) | fix: count settling sale proceeds in US real total assets | 5 files, +24/−6 |
| [#1414](https://github.com/dragon1086/prism-insight/pull/1414) | fix: collect BTC market facts after slow history reads | 5 files, +115/−7 |
| [#1415](https://github.com/dragon1086/prism-insight/pull/1415) | feat: align BTC chart policy with 30m minimum and hourly priority | 31 files, +293/−87 |
| [#1416](https://github.com/dragon1086/prism-insight/pull/1416) | feat(buy-gate): 비강세 52주 고점 관문 (기본 꺼짐) | 7 files, +59/−0 |
| [#1417](https://github.com/dragon1086/prism-insight/pull/1417) | fix(ops): ClickStack 원장 전송 멈춤 경보 (워치독) | 2 files, +64/−0 |
| [#1418](https://github.com/dragon1086/prism-insight/pull/1418) | fix: enforce BTC 30m judgment boundary for mark speed | 7 files, +45/−4 |
| [#1419](https://github.com/dragon1086/prism-insight/pull/1419) | fix: stream shared ledger reads and restore BTC trace lookup | 5 files, +57/−19 |
| [#1420](https://github.com/dragon1086/prism-insight/pull/1420) | feat: bull-regime money-based slots (BULL_MONEY_SLOT_MARKETS, default off) | 8 files, +168/−31 |
| [#1421](https://github.com/dragon1086/prism-insight/pull/1421) | test: pin the 11/21 live rule constants in the research engine contract | 1 files, +8/−0 |
| [#1425](https://github.com/dragon1086/prism-insight/pull/1425) | fix(reentry): resolved sector is stored and merger-proxy stocks are skipped (KR/US) | 7 files, +31/−3 |
| [#1426](https://github.com/dragon1086/prism-insight/pull/1426) | feat: show the re-entry watch list on the dashboard holdings tab | 5 files, +338/−0 |
| [#1427](https://github.com/dragon1086/prism-insight/pull/1427) | fix: move the re-entry watch card to the watchlist tab | 3 files, +7/−7 |
| [#1428](https://github.com/dragon1086/prism-insight/pull/1428) | feat: daily extreme-fear alert (information only) | 3 files, +157/−0 |
| [#1429](https://github.com/dragon1086/prism-insight/pull/1429) | fix: reuse verified BTC review evidence within the order guard | 3 files, +96/−3 |
| [#1430](https://github.com/dragon1086/prism-insight/pull/1430) | fix: US sell loop and close jobs use regular-session, settled prices | 11 files, +61/−23 |
| [#1431](https://github.com/dragon1086/prism-insight/pull/1431) | feat(reentry): US re-entry buys stop-exit watches only (H-1126) | 5 files, +53/−1 |
| [#1432](https://github.com/dragon1086/prism-insight/pull/1432) | fix(ledger): hold-review events carry the applied stop/target; rule re-entry scores recorded as unknown | 4 files, +43/−10 |

</details>

## 검증

- 커밋 집합을 `git rev-list v2.25.0..69f993bd`와 대조해 1095개 전부가 한 번씩, 하나의 분류로만 들어갔는지 스크립트로 확인했습니다.
  병합 PR 255개의 병합 커밋 SHA를 GitHub 기록과 대조했고, 이 문서가 255개 PR과 직접 커밋 85개를 모두 언급하는지 스크립트로 확인했습니다.
- `v2.25.0` 태그는 `3e147254`(PR #1156, v2.25.0 노트 갱신 병합)를 가리킵니다. 이번 범위는 그 다음부터입니다.
- 운영 서버(db-server)는 `45c204fc`(#1432)에 있음을 읽기 전용으로 확인했습니다. 그 뒤 main 커밋(`69f993bd`까지)은 연구 기록 문서뿐입니다.
  같은 확인에서 다음 스위치가 켜져 있었습니다: `US_REGIME_K8B_MODE=live`, `PYRAMID_ROWS_MICRO_SPLIT_ENABLED=true`, `REENTRY_V3_FIRST_SIZE_FACTOR=0.5`,
  `REENTRY_V3_BULL_FULL_SIZE=KR`, `REENTRY_V3_MA50_GATE_MARKETS=KR`, `REENTRY_V3_K8B_EARLY_BULL_MARKETS=US`, `REENTRY_V3_MA20R_LIVE_MARKETS=US`,
  `REENTRY_V3_STOP_SOURCE_ONLY_MARKETS=US`, `SELL_TIGHT_NONBULL_ENABLED=KR`, `NONBULL_52W_GATE_MARKETS=KR,US`, `BULL_MONEY_SLOT_MARKETS=KR,US`,
  `PULSE_PILOT_REEXPOSURE=false`. 인버스 레인은 crontab의 인라인 설정으로 돕니다(한국 17:10, 미국 10:10 KST).
- **아직 관측하지 않은 것(이 문서에서 확인 안 함)**: 미국 재진입 손절 출처 제한(#1431)과 미국 정규장 가격 수정(#1430)의 첫 정기 실행, 비강세 52주 관문·강세장 돈 기준 칸의
  첫 적용 사례, 인버스 레인의 첫 실주문·체결, 극단 공포 알림 첫 발송.
- 테스트·스모크는 실제 주문이나 정기 배치의 성공을 뜻하지 않으며, 관측·재생 결과는 수익성의 증명이 아닙니다.

## 업데이트 방법

```bash
git status --short --branch
git fetch origin --tags
git show --no-patch --oneline v2.26.0
```

- 실제 배포는 `docs/SERVER_GIT_OPERATIONS_ko.md`에 따라 clean 대상에 확인한 commit을 fast-forward하고 변경 범위별 검증을 합니다. 위 명령은 자동 업그레이드가 아닙니다.
- 새 스위치는 대부분 **코드 기본값이 꺼짐**입니다. 운영과 같게 쓰려면 위 "검증"의 값을 `.env`에 넣습니다. 예외로 `PRISM_US_FINANCIAL_F_GATE`는 기본 켜짐입니다.
- **재진입 시각**: 재진입 판단 cron을 KR 15:08 / 미국 15:30 ET로 옮겨야 합니다(#1386). 감시 마감도 같이 바뀌었습니다(#1397).
- **미국 종가 작업**: 장기 보유·재진입 마감 작업 cron을 20:20/20:40/20:55 ET로 옮깁니다(#1430).
- **KRX 특수 거래일**: `KRX_SESSION_OVERRIDE=YYYY-MM-DD=HH:MM-HH:MM`(#1409). 예약표 이동은 crontab에서 직접 합니다.
- **인버스 레인**: `INVERSE_SIGNALS_MODE`, `INVERSE_LANE_LIVE_MARKETS`를 cron 줄에 넣습니다. 기본은 꺼짐이고, 실주문은 사용자 승인 사항입니다.
- **운영 감시**: 아침 요약·[주의]를 다시 받으려면 `OPS_WATCHDOG_SUMMARY_ENABLED=true`, `OPS_WATCHDOG_NOTICES_ENABLED=true`. 2주 점검은 `tools/two_week_review.py --every 14 --anchor 2026-10-18`.
- **구독자**: 피라미딩 행 추종과 목표 비중 채우기를 쓰려면 구독자 예제를 최신으로 갱신하고 재시작합니다.

## 참고 사항과 알려진 한계

- **태그에 코드가 포함됨, 기능이 켜져 있음, 운영 검증 완료, 수익성 입증은 서로 다릅니다.**
- 재진입 변경(비중·관문·20일선 회복·시각·출처)은 짧은 기간에 여러 차례 이어졌습니다. 각 변경의 효과를 따로 떼어 보기 어려우니 2주 점검에서 실기록으로 다시 봅니다.
- 미국 20일선 회복 재진입은 5년 재현 결과로 올렸고, 실전 표본은 아직 적습니다. 한국에서는 칸 경쟁 때문에 손해라 쓰지 않습니다.
- 강세장 돈 기준 칸은 강세장에서 보유 종목 수를 16개까지 늘립니다. 11번째 이후 매수의 손익비가 1 미만이면 끕니다.
- 비강세 52주 관문은 약세장에서 "안 사는" 판단을 늘리는 방향입니다. 막힌 후보의 +30% 도달 비율이 통과 후보보다 높으면 끕니다.
- 인버스 레인의 손익비 2는 과거 재현 결과이고, 연 15% 목표에는 못 미쳤습니다. 미국 쪽 실주문은 검증된 신호가 없어 사실상 기록만 합니다.
- 미국 장 마감 뒤 KIS 가격은 시간외 거래를 따라 움직입니다. 한국도 같은 현상이 있으나 확정 시각 측정 뒤 옮길 예정입니다.
- BTC 변경은 데모 범위이고, 어떤 변경도 수익성을 입증하지 않았습니다. 신규 진입 위험 상한(#1390)은 사용자 선택 전이라 적용하지 않았습니다.

## 텔레그램 공지

v2.25.0 공지가 나가지 않아 **v2.25.0과 v2.26.0을 합친 공지**입니다(10/5~11/27).

### 한국어

```text
🚀 PRISM-INSIGHT v2.25.0 + v2.26.0 업데이트

v2.25.0 안내를 드리지 못해 두 버전을 함께 알려 드립니다. 10월 5일부터 11월 27일까지 커밋 1,892개, PR 479개입니다. 방향은 같습니다. 크게 가는 종목을 일찍 팔지 않고, 털리면 다시 올라타고, 약한 장에서는 덜 삽니다.

🏃 크게 가는 종목 오래 들기
· 목표가에 닿아도 팔지 않고, 그때부터 고점 대비 손절선으로 관리합니다
· 20거래일 넘게 +15% 이상 오른 '증명된 종목'은 50일선을 이틀 연속 깰 때만 팔고, 크게 오른 종목은 시장당 2개까지 200일선 이탈까지 장기 보유합니다
· 주도주가 시장보다 10%p 넘게 약하면서 20일선을 깨면 팝니다
· 재진입한 날 바로 50일선 손절로 팔리던 문제와, 오후 점검이 전날 종가로 판단하던 문제를 고쳤습니다
· 미국은 장 마감 뒤 시간외 가격이 판단에 섞이지 않게 했습니다

📈 크게 가는 종목에 더 싣기
· 강한 강세장에서 +5% 오른 종목은 한 번 더 사는 피라미딩을 되살렸습니다
· 증액이 1주 미만이라 막히던 문제를 고치고, 종가 확인 증액은 마감 직전에 당일 매수합니다
· 강세장에서는 종목 수가 아니라 실제 투자 비중으로 칸을 세어 최대 16종목까지 삽니다

🔁 다시 올라타기(재진입)
· AI 재점검 대신 규칙 신호로 삽니다
· 첫 비중은 절반, 한국 강세장은 원래 크기로 삽니다. 한국은 50일선 아래면 사지 않습니다
· 미국은 '20일선 회복 + 평균 이상 거래량' 신호로 바꿔, 손절 뒤 바로 달리는 종목을 따라갑니다
· 판단은 장 마감 직전(한국 15:08, 미국 15:30)에 하고, 미국은 손절한 종목에서 나온 신호만 삽니다

🛡 약한 장에서는 덜 사기
· 강세장이 아닐 때 52주 고점의 75%도 안 되는 종목은 새로 사지 않습니다
· 한국은 횡보·약세장에서 산 종목의 수익 보호선을 더 일찍, 더 좁게 잡습니다
· 미국 시장 국면을 S&P500과 나스닥을 함께 보는 기준으로 바꿨습니다
· 약세장용 인버스 ETF 레인을 새로 만들어, 검증된 신호만 계좌당 한 칸으로 운영합니다

🎯 매수 판단
· 약세·횡보장에도 하루 3종목을 고릅니다
· 매수·매도 지시문을 실제 실행 규칙과 맞추고 중복 문장을 줄였습니다(매도 지시문 21% 축소)
· 미국 금융주는 은행에 맞는 재무 기준으로 판정합니다

📰 뉴스·테마(설명 자료)
· 한국·미국 세부 테마 지도를 만들고, 시그널 얼럿과 보고서에 '오늘 테마 흐름'을 붙였습니다
· 뉴스·테마는 매매 근거가 아니라 설명 자료로만 씁니다

📱 메시지·대시보드
· 매수·증액·주도주 전환·재진입 알림을 한국어 한 양식으로 정리했습니다
· 대시보드를 새로 디자인하고, 수익률 합계를 차트와 같은 비중 기준으로 맞췄습니다
· 재진입 감시 목록을 관심 종목 탭에서 볼 수 있습니다

⚙️ 기록·운영
· 스크리닝부터 매도까지 한 번호로 이어지는 분석 원장과 실제 체결 확인을 넣었습니다
· 종목코드 변경 자동 대조, 수능일 같은 특수 거래시간 지정, 원장 전송 멈춤 경보를 넣었습니다
· 극단 공포 구간(QQQ RSI 30 이하, 공포탐욕 10 이하, VIX 30 이상)이면 알림을 보냅니다(정보용)

₿ BTC 데모
· 판단 모델을 GPT-6.1 Terra로 바꾸고, 30분봉 이상·1시간봉 우선으로 차트를 봅니다
· 두 시간봉이 함께 좁아지면 신규 진입을 쉬고, 진입 비용과 최대 손실을 미리 검산합니다
· 결과를 모르는 주문을 기록을 지우지 않고 격리·복구하고, 저장 기록은 잃지 않게 압축합니다(데모 계좌, 수익성 미입증)

※ 연구 결론: 사자마자 손절되는 것은 큰 종목을 잡는 입장료입니다. 개선은 증명된 종목을 오래 들고, 털린 종목에 다시 올라타고, 약한 장에서 덜 사는 데서 찾습니다

릴리즈노트:
https://github.com/dragon1086/prism-insight/releases/tag/v2.26.0
https://github.com/dragon1086/prism-insight/releases/tag/v2.25.0

가상 운용·연구 및 소프트웨어 변경 안내이며 투자 권유가 아닙니다.
```

### English

```text
🚀 PRISM-INSIGHT v2.25.0 + v2.26.0 update

We missed the v2.25.0 notice, so this covers both releases: October 5 to November 27, 1,892 commits and 479 PRs. The direction is the same. Don't sell big winners early, get back on after a shakeout, and buy less in weak markets.

🏃 Holding big winners longer
· Reaching the target price is no longer a sell reason; from there a trailing stop takes over
· A "proven" stock (20+ trading days, +15% or more) is sold only after two closes below the 50-day line, and up to two big winners per market are held until the 200-day line breaks
· A leader that trails the index by more than 10 points and closes below its 20-day line is sold
· Fixed same-day 50-day-line sells after a re-entry, and afternoon reviews that used yesterday's close
· US decisions no longer use after-hours prices

📈 Adding to big winners
· Pyramiding is back: in a strong bull market a stock up 5% gets one more independent position
· Adds smaller than one share now buy one share, and close-confirmed adds buy the same day just before the close
· In bull markets, slots count money actually invested, not tickers, up to 16 names

🔁 Getting back on (re-entry)
· Rule signals buy directly instead of an AI recheck
· Re-entries start at half size; Korea uses full size in bull markets and skips names below the 50-day line
· The US now re-enters on a 20-day-line recovery with above-average volume, so it follows stocks that run right after a stop-out
· Decisions run just before the close (Korea 15:08, US 15:30 ET), and the US buys only signals from stopped-out names

🛡 Buying less in weak markets
· Outside bull markets, no new buys below 75% of the 52-week high
· In Korea, positions bought in sideways or bear markets start their profit stop earlier and tighter
· The US market regime now reads the S&P 500 and the Nasdaq together
· A new inverse ETF lane trades only validated signals, one slot per account

🎯 Buy decisions
· Weak and sideways markets pick three names a day
· Buy and sell prompts now match the executed rules, with duplicates removed (sell prompts 21% shorter)
· US financials are judged with bank-appropriate balance-sheet rules

📰 News and themes (context only)
· New Korea and US theme maps add a "today's theme flow" block to alerts and reports
· News and themes are context, never a trading rule

📱 Messages and dashboard
· Buy, add, leader and re-entry notices share one Korean format
· The dashboard was redesigned, and the return total now matches the slot-weighted chart
· The re-entry watch list is on the watchlist tab

⚙️ Records and operations
· An analysis ledger links screening to exit under one trace number, with fill confirmation
· Automatic ticker-change reconciliation, special trading-hour overrides (such as exam day) and a ledger-shipping stall alert
· An information-only alert fires when QQQ RSI ≤ 30, Fear & Greed ≤ 10 and VIX ≥ 30 all hold

₿ BTC demo
· Decisions moved to GPT-6.1 Terra, with 30-minute minimum charts and the 1-hour chart first
· New entries pause when two timeframes converge, and entry cost and maximum loss are checked in advance
· Orders with unknown results are isolated and recovered without rewriting history, and stored records are compressed losslessly (demo account, profitability not proven)

From the research: early stop-outs are the entry fee for catching big winners. Gains come from holding proven stocks, getting back on after shakeouts, and buying less in weak markets.

Release notes:
https://github.com/dragon1086/prism-insight/releases/tag/v2.26.0
https://github.com/dragon1086/prism-insight/releases/tag/v2.25.0

Software, virtual-operation and research updates; not investment advice.
```
