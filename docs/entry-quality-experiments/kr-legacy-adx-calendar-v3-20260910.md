# KR legacy ADX v3: 두 휴장일 연구 달력 교정 사전 등록

등록: 2026-09-09T19:01:48Z. v3 재생 이전입니다.
v1/v2 등록·raw·시세·성과 파일을 그대로 보존하며 v2의 새 OHLC 조회 없이 재생합니다.
운영 달력·cron·거래 코드·필터·임곗값은 변경하지 않습니다.

## 교정 근거

설치된 XKRX 달력에는 2026-06-03, 2026-07-17이 거래일로 남아 있었지만
삼성증권의 직접 주식시장 휴장 공지 두 건이 이를 반증합니다.

- 지방선거일 2026-06-03:
  https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=23996
- 제헌절 2026-07-17:
  https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=24145
- 두 공지는 주식시장을 명시하고 유가증권·코스닥·코넥스시장 업무규정 제5조를
  근거로 듭니다. 공지 본문을 읽고 부모 검토자가 정확히 두 날짜의 연구 overlay를 승인했습니다.
- 날짜·URL·범위·짧은 사실 투영은 kr-calendar-facts-v3-20260910.json에 보존합니다.
  canonical facts SHA256은 d98832baf64ca693315a2b17feeae0029cab8db95e1bb036708375867e7a09fb입니다.
  이는 원본 HTTP 전체 응답 해시가 아니라 위 짧은 사실 투영의 해시입니다.

v2에서 이 두 날짜는 54개 주식 응답 모두에 일봉이 없었습니다.
2026-03-27은 54개 raw 응답에 모두 있으며 다섯 코드의 OHLC가 부적합했습니다.
**3월 27일은 휴장일로 바꾸지 않으며 부적합 가격을 보간·수정하지 않습니다.**
원래 기록 진입일이 비거래일이면 다음 거래일로 이동시키지 않고 제외합니다.

## 이미 본 결과와 변경하지 않을 항목

v2의 44행 결과와 H1/H2/H3/H4 대응 차이 약 -2.109/-2.101/-4.075/-5.126%p를
이미 보았습니다. 독립 holdout을 주장하지 않습니다. 동일 원본 209행·legacy book,
두 trigger, 마감 2026-09-09T16:18:06.031436Z, 전 진입일 60개 완료봉,
H1~H4/Wilder, 원본 날짜 70/30 경계, 30일 embargo·청산 purge,
추가 편도0/10/30bps, 승자 보존·최고 승자 제거를 유지합니다.
종목 identity 계약도 동일 응답 EQUITY/exact symbol/exchange/timezone/firstTradeDate로 유지합니다.

v1~v3의 네 가설 시도를 보수적으로 family12로 공개합니다. 최소30행·20날짜에서
같은 seed20260910의 날짜 cluster bootstrap1000회에 Bonferroni99.5833% 탐색 구간을
적용합니다. 순서 통계 하위2/상위997 index를 사용하며 표본 부족 시 null입니다.
규칙 순위를 보고 임곗값을 바꾸거나 특정 비용·구간을 선택하지 않습니다.

## 입력과 결과

- 원본 artifact: 2a457cc01ee4e3030a2b7f3dadc7994a9ccfa8fceef125a9318e433791f6bc5a.
- 원본 logical: 8dd731e18bb51395e31d355b2cfa5f39175e7d026710db1842118c5786320ba0.
- v2 시세 artifact: e5f9749562b4b19abecae1b7daa4160ef50c7a99f75d4b8c94cf40c3caa064e3.
- 원래 calendar version, 정확히 두 날짜의 overlay, 사실/등록 해시와
  v2 부모 해시를 가진 새 파생 시세 envelope를 생성합니다. raw·가격·metadata는 바꾸지 않습니다.
- 결과는 별도 v3 JSON/한국어 보고서입니다. 제외된 원본 승자 커버리지도 표시하되
  제외 행의 지표·대응 성과를 임의로 채우지 않습니다.
- 브로커 체결 필터, canonical 전략·ISIN 인증, 포트폴리오 자본/수익률·대체 후보를
  새로 만들지 않습니다. US 자료와 합산하지 않습니다.

판정은 CONTINUE_CAPTURE입니다. 이 작업은 방어 가능한 KR 과거 탐색 진단을
완료하는 것이며 canonical 전략 성과 검증이나 SHADOW/LIVE 승격이 아닙니다.
