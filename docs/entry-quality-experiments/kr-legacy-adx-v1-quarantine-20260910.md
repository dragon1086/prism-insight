# KR legacy ADX v1 자료 격리

상태: **PROVIDER_IDENTITY_UNVERIFIED**. v1을 검증된 KR 주식 표본이나 성과 근거로
사용하지 않습니다. 기존 파일은 삭제하거나 수정하지 않았습니다.

- 수집 artifact: e5840a94341a6943246ea3028b990e2562a23e75d53a9a305a8a6d6194601a8b
- 최종 표시 결과 artifact: 23b9266c1216361f7f6730369de352b534bbc6bb4535981dafd8298dcafb3da8
- v1 등록: kr-legacy-adx-20260910.md
- 격리 대상: workspace의 kr-legacy-adx-raw-20260910, kr-legacy-adx-market-data-20260910.json,
  kr-legacy-adx-study-20260910*.json/.md, kr-legacy-adx-frozen-raw-20260910.tar.gz.

105개 비어 있지 않은 응답의 날짜 offset은 +09:00이지만 원본 수집기에서 canonical
symbol, instrumentType, exchangeTimezoneName을 버렸습니다. offset만으로 한국
주식 identity를 확인할 수 없습니다. v1의 적격 표시 2행도 모두 정체성 미검증입니다.

별도 사전 등록한 kr-legacy-adx-identity-v2-20260910.md에 따라 동일 응답 metadata와
OHLC를 새로 수집합니다. v1 가격열에 새 metadata를 사후 결합하지 않습니다.
v1 결과를 이미 보았다는 사실과 확대된 검정 family를 v2에 공개합니다.
