# BTC Funding execution 정산 차단 교정

## 사건과 원인

사용자가 실제 거래 생명주기와 비용 포함 성과 검증을 요청한 후, 읽기 전용 운영 Packet에서
기록된 진입 0.1 BTC와 청산 0.1 BTC가 일치하나 최종 정산이 없는 상태를 확인했다.
운영 DB 최신 금융 증거 2개와 최근 회계 결과의 `financial_rows_unmatched`를 대조했다.

실제 입력은 Trade execution 2개 + Funding execution 1개, TRADE transaction 2개 +
SETTLEMENT transaction 1개였다. Funding의 execId=SETTLEMENT의 tradeId였고 주문 ID,
시각·수량·방향·종목이 일치했다. 거래 수수료와 cashFlow는 0, execFee는 funding의 반대 부호였다.
그러나 기존 코드는 모든 non-Trade execution을 unmatched로 넣어 정상 펀딩까지 차단했다.

원본 DB·주문을 변경하지 않은 메모리 복사 진단에서, 이 정확한 펀딩 쌍만 대조하면
매매 체결 정합성 검사를 지나 펀딩 일정 완전성 검사까지 진행했다. 과거 일정이 보존되지
않아 이 진단을 정산 성공으로 주장하지 않았다. 수정 후 정상 예약 실행이 새 일정·잔량·
미체결 주문을 다시 확인해야 최종 정산이 가능하다.

공식 계약: [Bybit execution 종류](https://bybit-exchange.github.io/docs/v5/enum),
[Transaction Log](https://bybit-exchange.github.io/docs/v5/account/transaction-log).

## 범위와 변경 전 계획

M0/M1 실행·회계 정합성 오류 수정이다. 전략·프롬프트·위험 예산·스케줄·계좌를 바꾸지
않으며 신규 주문, 강제 청산, 수동 정산 완료 처리는 하지 않는다.

1. 실제 응답 형태를 격리 fixture로 재현해 기존 unmatched 실패를 확인한다.
2. Funding만 exact SETTLEMENT와 1:1로 대조한다. 종목·USDT·양방향 ID·방향·시각·수량,
   fee=0, cashFlow=0, execFee=-funding을 Decimal로 검증한다.
3. 일치한 Funding execution은 경제적으로 두 번째 현금 흐름이 아니라 원장 대조 근거다.
   trade 수량·거래 수수료에 더하지 않고 기존 funding 경로에서 한 번만 반영한다.
4. 상대 행 부재·충돌·틀린 값·알 수 없는 execution 종류는 계속 차단한다.
   Funding execution이 없는 SETTLEMENT는 기존 일정·노출 검증 경로를 유지한다.
5. 기존 펀딩 일정·포지션·주문 종료·거래 비용 완전성 검사를 모두 유지한다.
6. 회귀·전체 테스트·정확한 head CI·운영 Python 격리 검증 뒤 clean/ff-only 배포한다.
   실제 복구는 자연 스케줄러에서 확인하고 새로운 정산/전송 증거가 없으면 미확인으로 쓴다.

## 연구 버전의 분리

동시에 실행 중인 A/B/C 백테스트는 사전등록된 3b5a4a69 소스의 별도 worktree에서
계속 실행한다. 본 안전성 교정으로 진행 중 tape의 소스 해시를 바꾸지 않는다.
해당 시뮬레이터는 펀딩 거래 원장은 생성하지만 Funding execution 응답 형태는 만들지
않았으므로 이 실제 인터페이스 결함을 포착하지 못했다. 역사적 백테스트와 실제 API
집행 패리티를 구분하며, 옛 연구 결과를 수정본의 전체 정합성 인증으로 사용하지 않는다.

## 검증 기준

정확한 펀딩 쌍, 지급·수취/롱·숏, 수량·수수료·시각·통화·ID·방향 불일치, 중복,
BustTrade, 일정 누락을 포함한다. capture_financial_evidence→reconcile_scenario의 실제
모듈 경로로 한 번만 과금되고 모든 증거가 확인돼야 flat settlement가 생성되는지 검증한다.
