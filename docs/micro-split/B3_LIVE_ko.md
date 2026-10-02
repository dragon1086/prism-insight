# 초분할 LIVE (KR/US) — `micro-split-live-v1`

## 결정 (2026-10-02, 사용자 선택)

- "지금 LIVE 구현 착수, 완성되면 승인 후 전환". 실제 KIS 주문, 보유 DB, 메시지, 대시보드, 시그널, ClickStack까지
  한 묶음으로 구현하고 **기본 OFF로 배포**한다. 주문 없는 점검 후 사용자 승인으로 KR·US를 함께 켠다.
- 규칙은 B3 SHADOW(`oneil-adaptive-v3-ae`, [B3_ALL_ENTRIES_SHADOW_ko.md](B3_ALL_ENTRIES_SHADOW_ko.md))와 같다.
  LIVE는 같은 계획으로 실제 주문을 낸다.

## 켜기·끄기

| 설정 | 기본 | 의미 |
|---|---|---|
| `MICRO_SPLIT_LIVE_ENABLED` | `false` | LIVE 전체 스위치 |
| `MICRO_SPLIT_LIVE_MARKETS` | `KR,US` | 시장 제한 |

끄면 다음 진입부터 기존 1슬롯 전량 진입으로 돌아간다. 이미 열린 초분할 보유는 그대로 유지되고 손절·매도도
그대로 동작한다. 증액은 워커가 LIVE_OFF로 건너뛴다. 워커는 SHADOW 또는 LIVE 중 하나라도 켜져 있으면 돈다.
배치는 매번 `.env`를 새로 읽는다. 워커는 시작할 때만 읽으므로, 바꾼 뒤에는 `systemctl restart prism-b3-ae-shadow@kr prism-b3-ae-shadow@us`를 실행해야 한다.

## 흐름

1. **최초 진입**: 정규 배치의 신규 진입에만 적용한다. 피라미딩 추가·반등 시험매수·격리 런타임·US O'Neil 소유
   진입은 제외한다.
   - 실행가와 결정 시점 일봉 ATR14로 v3-ae 계획을 만든다.
   - 주문 금액은 `1슬롯 금액 × initial`로 정하고 원·센트 단위에서 내림한다. `strict_budget`이므로 지정가이고,
     1슬롯 금액으로 대체되지 않는다. 1주 미만이면 주문을 막고 전략 기록만 남긴다.
   - 보유 행 `scenario.micro_split`에 계약, 계획 해시, 1슬롯 금액, 현재 비중, 구간(INITIAL)을 기록한다.
   - 같은 계획으로 LIVE 캠페인(`runtime/b3-ae-shadow.sqlite`, mode=LIVE)을 연다.
   - 계획을 만들 수 없으면(일봉이나 손절가가 없을 때) 이유를 로그에 남기고 기존 1슬롯 진입을 한다.
2. **증액**: 워커(`prism-b3-ae-shadow@{kr,us}`)가 SHADOW와 같은 게이트로 ADD를 판단한다. LIVE 캠페인이면
   `tools/run_micro_split_add.py`를 시장별 별도 프로세스로 실행한다.
   - `BEGIN IMMEDIATE`로 보유 행 `scenario.micro_split`에 ADD 구간을 기록한다. 같은 봉에서 두 번 기록되지 않고,
     비중은 100%를 넘지 않는다.
   - 증가분 × 1슬롯 금액으로 KIS 지정가 주문을 낸다. `OrderIntent`의 `source_decision_id`는
     hash(campaign, bar_end)로 고유하다.
   - 텔레그램 "초분할 추가 매수"(비중 a%→b%, 추가 매수가, 평균 매수가, 주문 상태)를 보내고
     `micro_split.add_executed` 이벤트를 남긴다.
   - 전략 원장은 체결과 독립적이다. 주문이 실패해도 기록은 유지되며, 기존 진입과 같은 원칙이다.
3. **청산**: 기존 손절·추세이탈·AI 매도를 그대로 쓴다. 한 행이므로 보유 수량 전량을 매도한다. `sell_stock`은
   `BEGIN IMMEDIATE` 안에서 행을 다시 읽어 최신 구간을 반영한다. `trading_history.buy_price`에는 구간 가중
   평균 매수가를 기록하므로, `profit_rate × 비중`이 슬롯 수익률(Σ aᵢ·(청산가/pᵢ − 1))과 같다.

## 불변조건

- 보유 행의 `buy_price`는 **최초 진입가로 유지**한다. 이유는 세 가지다.
  - positions 원장 지문과 KR pending 점검이 일치해야 한다.
  - −7% 손절과 트레일링의 기준이 SHADOW·리플레이와 같아야 한다.
  - US O'Neil 경로와 같은 방식이다.
  
  평균 매수가는 메시지·대시보드·이력에서 계산해 보여준다.
- 1슬롯(100%)이 상한이다. 초분할 행은 #288 피라미딩 대상에서 빠진다(`LEGACY_PYRAMID_BLOCKED_MICRO_SPLIT`).
  두 번째 행이 생겨 매도가 `floor(qty/N)`로 쪼개지는 일은 없다.
- 증액 주문은 정규장의 당일 지정가 주문이다. 손절가는 증액가보다 낮으므로 손절 전에 미체결 매수가 먼저
  체결된다. 남은 주문은 장 마감에 소멸한다. 장외 예약 증액은 없다(워커는 정규장에서만 판단).

## 표기

| 표면 | 내용 |
|---|---|
| 매수 메시지 | "초분할 비중: N% (1슬롯 기준) — +2%·+4% 확인 시 80%·100%까지 추가 매수, 손절 시 전량" (US 영문) |
| 추가 매수 메시지 | 비중 a%→b%, 추가 매수가, 평균 매수가, 주문 상태 |
| 매도 메시지 | 비중, 평균 매수가(증액 시), 슬롯 기준 손익 |
| 포트폴리오 요약 | 종목별 비중·평균 매수가·슬롯 기준 손익, "사용 비중 x.xx/10 슬롯" |
| 누적 수익률 | `slot_weight.slot_fraction`이 `micro_split.allocation`을 먼저 읽음 |
| 대시보드 JSON | holdings/history의 `allocation`, `allocation_label`, `average_entry`, `add_count`, `slot_profit_rate`, summary의 `allocated_slots`, `slot_weighted_profit` |
| 대시보드 화면 | 보유 표 매수가 아래 비중 배지·평균가, 수익률 아래 슬롯 기준 수익률, 거래이력 카드 비중, 슬롯 사용률 카드에 실제 비중 합계 |
| 시그널 | BUY에 `position_fraction`(시험매수 0.5도 포함). 증액 시그널은 보내지 않음 |
| ClickStack | entry/exit 컨텍스트 `policy_context.micro_split`·`slot_allocation`, 속성 `prism.slot_allocation`, 증액 이벤트 `micro_split.add_executed` |

## 범위 밖 (이번 묶음에서 바꾸지 않음)

- 슬롯 수 게이트는 계속 종목 수(행 수)로 센다. 초분할 30% 종목도 한 슬롯을 차지한다.
- 주간 리포트와 봇의 트리거 등급 평균은 거래별 수익률(비가중) 그대로다.
- 매수·매도 프롬프트의 "1슬롯 = 10%, 올인/올아웃" 문구는 그대로다. 매도는 여전히 전량이다.
- 외부 구독자의 증액 추종은 없다. 구독자는 최초 비중만 따른다.

## 승인 전 점검 (주문 없이)

1. 배포 후 `.env` OFF 상태에서 기존 진입·매도·메시지에 변화가 없는지 확인한다.
2. 운영 서버에서 `prepare_entry`를 읽기 전용으로 실행해 계획·금액이 맞는지 확인한다. 브로커 호출과 DB 쓰기는 하지 않는다.
3. 워커 health에서 `mode` 필드와 `orders_submitted=0`을 확인한다.
4. 사용자 승인 후 `MICRO_SPLIT_LIVE_ENABLED=true`로 바꾸고, 첫 진입의 메시지·DB·주문·ClickStack을 관측한다.

## 롤백

`.env`에서 `MICRO_SPLIT_LIVE_ENABLED=false`로 바꾼다. 열린 초분할 보유는 기존 매도 경로로 정상 청산된다.
