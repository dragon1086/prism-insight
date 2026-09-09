# KR legacy ADX v2: 공개 시세 종목 정체성 교정 사전 등록

등록: 2026-09-09T18:44:24Z. v2 일봉 재조회·성과 분석 전 고정합니다.
원본 trial과 자료는 바꾸거나 덮어쓰지 않습니다. v1의 결과 및 시세 파일은
PROVIDER_IDENTITY_UNVERIFIED로 격리하며 유효한 KR 주식 검증 자료로 사용하지 않습니다.

## 교정 사유와 이미 본 정보

v1은 요청한 .KS/.KQ에 OHLC가 있다는 사실만 확인했고, 재사용한 US 수집기가
symbol, instrumentType, exchangeTimezoneName을 보존하지 않았습니다.
부모 연구자의 별도 동일 history-response 확인에서 005930.KS는 KSC/EQUITY,
005930.KQ는 KOE/MUTUALFUND로 나타났습니다. 같은 여섯 자리 코드와 한국 시간대라도
두 응답이 모두 주식인 것은 아닙니다. CAN SLIM 연구의 주식 모집단 계약을 지키기
위해 같은 응답에 결합된 공개 identity 필드를 보존하고 검증합니다.

v1의 적격 표시 2행과 결과를 이미 보았습니다. H1~H3 차이 +8.153%p, H4 -12.515%p를
열람했으나 identity가 검증되지 않아 그 결과를 폐기·격리합니다. v2는 독립 holdout이
아니며 이를 숨기거나 v1에서 유리한 규칙을 선택하지 않습니다.

## 고정 입력·규칙

- source artifact 2a457cc01ee4e3030a2b7f3dadc7994a9ccfa8fceef125a9318e433791f6bc5a,
  logical 8dd731e18bb51395e31d355b2cfa5f39175e7d026710db1842118c5786320ba0.
- 동일 KR 209행·분리된 legacy book, 동일 두 trigger 88행, 동일 고정 마감
  2026-09-09T16:18:06.031436Z, 동일 진입일 전 60봉 proxy를 사용합니다.
- H1 ADX14>=20; H2 ADX14>=25와 +DI>-DI; H3 ADX3봉 엄격 상승과 +DI>-DI;
  H4 ER20>=0.30와 net20>0. 기존 Wilder 구현을 그대로 사용하며 튜닝하지 않습니다.
- 원본 날짜 기준 70/30, 30일 embargo와 청산 purge, 날짜 bootstrap 1000회,
  seed20260910, 최소30행/20날짜, 승자 보존·최고 승자 제거 및 추가 편도0/10/30bps
  비용 stress는 그대로입니다. v1/v2의 H1~H4 시도를 보수적으로 family8로 공개해
  Bonferroni 99.375% 탐색 구간을 사용합니다. 층별 수치를 별도 유의성 주장에 쓰지 않습니다.

## v2 identity 계약

54개 코드의 .KS/.KQ 108개 요청을 새로 실행합니다. v1 OHLC에 나중 metadata를
붙여 검증한 것처럼 표시하지 않습니다. 각 history 응답의 OHLC와 동일 호출에서
이미 채워진 metadata만 읽습니다. 별도 info/history_metadata 추가 조회는 하지 않습니다.

유효 주식 응답은 다음 조건을 모두 충족해야 합니다.

1. instrumentType은 정확히 EQUITY.
2. canonical meta.symbol은 요청한 동일 6자리 코드와 suffix에 정확히 일치.
3. .KS이면 exchangeName=KSC, .KQ이면 exchangeName=KOE.
4. exchangeTimezoneName=Asia/Seoul, 일봉 timestamp offset도 +09:00.
5. firstTradeDate가 유한한 Unix 시각으로 존재하고 해당 기록의 진입일 정규장 시작보다
   늦지 않아야 합니다. 값이 없으면 알 수 없음으로 제외합니다.
6. 완료 정규장 OHLC가 존재하고 기존 가격·calendar·기업행위 검사를 통과해야 합니다.

동일 코드에서 두 응답 모두 유효 EQUITY이면 여전히 AMBIGUOUS이며 가격·수익률로
선택하지 않습니다. MUTUALFUND/다른 유형·symbol 불일치·exchange/timezone 미상은
구체적인 이유로 제외합니다. provider의 current identity 검증일 뿐 ISIN·과거 issuer
연속성·상장 이전/폐지 이력의 인증은 아닙니다.

원본 decision ID, 판단 시각, regime, policy, 배분, 브로커 체결 및 canonical
전략 연결을 만들지 않습니다. source_record_ref와 legacy_book_ref를 유지합니다.
모든 원본행과 coverage 제외 사유를 남기며 US 연구와 합산하지 않습니다.

## 파일과 판정

v1 등록·raw·시세·결과·보고서는 그대로 보존하고 별도 격리 설명을 남깁니다.
v2는 identity-v2/round2로 구분한 신규 raw·시세·결과·한국어 보고서를 만듭니다.
실제 조회 시각, metadata allowlist, 원본 응답 hash, 등록 hash를 포함합니다.
결과가 좋든 나쁘든 threshold를 바꾸거나 자동 SHADOW/LIVE로 승격하지 않습니다.
판정은 CONTINUE_CAPTURE입니다. 대체 후보·포트폴리오 수익률을 계산하지 않습니다.
