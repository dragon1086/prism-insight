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
   - 텔레그램 "📈 추가 매수(비중 확대)"(비중 a%→b%, 추가 매수가, 평균 매수가, 손절가, 근거, 가속 구간이면 그 사실,
     주문 상태)를 보내고, Redis·GCP에 `ADD` 시그널을 발행하고, `micro_split.add_executed` 이벤트를 남긴다.
     메시지와 시그널은 주 계좌(첫 번째 계좌) 증액에서 한 번만 나간다(2026-10-05).
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
| 매수 메시지 | "초분할 비중: N% (1슬롯 기준) — 증액 시나리오: …(조건이 확인될 때만 증액하며), 손절 시 전량 매도합니다" (US 영문). 상위 셋업 가중이면 "1슬롯 기준, 상위 셋업 가중으로 기본 M%에서 상향"을 붙임. 고정 +2%·+4% 사다리 문구는 2026-10-02 시나리오 증액으로 바뀌었습니다 |
| 추가 매수 메시지 | "📈 추가 매수(비중 확대)": 비중 a%→b%, 추가 매수가, 평균 매수가, 손절가(손절 시 전량 매도), 근거(시나리오 종류·근거, 내부 id 없음), 가속 구간이면 "오늘 두 번째 추가 매수(최초 매수가 대비 +x%, 거래량 평소의 y배)", 주문 상태(접수 / 1주 미만이라 미주문 / 미접수, 사유 코드는 로그에만). US 영문 "📈 Position Add". 주 계좌만 |
| 매도 메시지 | 비중, 평균 매수가(증액 시), 슬롯 기준 손익, 주도주였으면 주도주 줄, 재진입이었으면 "🔁 재진입 종목 (…, k/3번째 시도)" |
| 포트폴리오 요약 | 종목별 비중·평균 매수가·슬롯 기준 손익, 주도주 줄, 재진입 줄, "사용 비중 x.xx/10 슬롯" |
| 누적 수익률 | `slot_weight.slot_fraction`이 `micro_split.allocation`을 먼저 읽음 |
| 대시보드 JSON | holdings/history의 `allocation`, `allocation_label`, `average_entry`, `add_count`, `slot_profit_rate`, summary의 `allocated_slots`, `slot_weighted_profit` |
| 대시보드 화면 | 보유 표 매수가 아래 비중 배지·평균가, 수익률 아래 슬롯 기준 수익률, 거래이력 카드 비중, 슬롯 사용률 카드에 실제 비중 합계 |
| 시그널 | BUY에 `position_fraction`(시험매수 0.5도 포함). 증액은 `ADD` 타입(`delta_fraction`, `allocation_before/after`, `signal_id`, `limit_price`, `stop_loss`). 재진입 BUY에 `entry_kind: REENTRY` (2026-10-05) |
| ClickStack | entry/exit 컨텍스트 `policy_context.micro_split`·`slot_allocation`, 속성 `prism.slot_allocation`, 증액 이벤트 `micro_split.add_executed` |

## 범위 밖 (이번 묶음에서 바꾸지 않음)

- 슬롯 수 게이트는 계속 종목 수(행 수)로 센다. 초분할 30% 종목도 한 슬롯을 차지한다.
- 주간 리포트와 봇의 트리거 등급 평균은 거래별 수익률(비가중) 그대로다.
- 매수·매도 프롬프트의 공유 지시문 "1슬롯 = 10%, 올인/올아웃" 문구는 그대로다. 매도는 여전히 전량이다.
  (2026-10-03 이후 초분할 LIVE 진입에는 종목별 부록 `buy_prompt_block`이 시스템 제약 4가 이번 매수에 적용되지 않는다고 명시한다.)
- ~~외부 구독자의 증액 추종은 없다.~~ **2026-10-05 사용자 결정으로 바뀜:** 증액은 `ADD` 시그널로 발행되고, 예제 구독자
  (`examples/messaging/gcp_pubsub_subscriber_example.py`)가 따라 산다. 규칙: 이미 보유한 종목만, `delta_fraction × 본인
  1슬롯 금액`, 본인 보유 평가액이 `allocation_after × 1슬롯`과 1슬롯을 넘지 않게 줄임, `signal_id`당 한 번(주문 전
  `runtime/subscriber_add_signals.jsonl`에 기록, 재시작·재전달에도 재주문 없음), 정규장에서만, 지정가·예산 엄수, 30분
  지난 시그널 무시(기존 가드), 건너뜀·실패는 `SUBSCRIBER_ALERT_CHAT_ID`로 알림. 끄기: 구독자 `.env`의
  `SUBSCRIBER_FOLLOW_ADDS=false`. ADD 타입을 모르는 과거 구독자는 로그만 남기고 주문하지 않는다.

## 승인 전 점검 (주문 없이)

1. 배포 후 `.env` OFF 상태에서 기존 진입·매도·메시지에 변화가 없는지 확인한다.
2. 운영 서버에서 `prepare_entry`를 읽기 전용으로 실행해 계획·금액이 맞는지 확인한다. 브로커 호출과 DB 쓰기는 하지 않는다.
3. 워커 health에서 `mode` 필드와 `orders_submitted=0`을 확인한다.
4. 사용자 승인 후 `MICRO_SPLIT_LIVE_ENABLED=true`로 바꾸고, 첫 진입의 메시지·DB·주문·ClickStack을 관측한다.

## 롤백

`.env`에서 `MICRO_SPLIT_LIVE_ENABLED=false`로 바꾼다. 열린 초분할 보유는 기존 매도 경로로 정상 청산된다.

## 진입 최소 점수 5점 (2026-10-02 추가, 사용자 결정)

- 초분할 진입은 처음에 1슬롯의 30~80%만 사므로, 시장 국면과 관계없이 **buy_score ≥ 5**면 진입한다.
  기존 국면별 하한(횡보·약세 8점, 강한 약세 9점)을 대신하고, AI가 제시한 min_score보다도 우선한다.
- 적용 조건: 신규 진입이고, 피라미딩 추가나 반등 시험매수가 아니며, 결정 시점 일봉으로 초분할 계획을 실제로 세울 수 있어야 한다.
  주문 시점에 계획을 다시 세우지 못하면 그 진입은 건너뛴다. 완화된 점수로 1슬롯 전량을 사는 일은 없다.
- 바뀌지 않는 것:
  - 보유 종목이 7개 이상이면 '6점 이상만 진입' 포트폴리오 제약(공유 BUY 지시문). 2026-10-04에 초분할 부록 `buy_prompt_block`의
    "바뀌지 않는 것" 목록과 기준 문장에 명시했습니다. 전에는 부록이 "국면 무관 5점"만 말해 우선순위가 정해지지 않았습니다
    (US 자료: 이 규칙으로 막힌 5점 종목은 진입당 −1.30%, 같은 기간 −1.61%, 95% 신뢰구간 [−3.0, −0.5]로 규칙이 손실을 막음).
  - AI의 진입 거절(미진입 단독 사유, 1단계 펀더멘털, 추세 게이트)
  - 최종 매수 게이트의 국면별 모멘텀·추가 확인·손익비·손실 한도
  - 업종 3종목 제한
  - 보유 10종목
- 근거(탐색 분석, `research/candidate-microsplit-explore`): KR·US 후보 1,636건을 봤다. 4점 이하는 대체로 손실이었다.
  5~6점은 상승기에 KR +4.75%, US +2.91%였다(1슬롯 기준). 약세기에는 초분할 기준 거래당 −0.3~−0.7%로 손실이 작았다.
- 기록: 시나리오의 `_entry_score_policy`(mode `micro_split_floor`, required 5, legacy_required)와 로그 `[MICRO_SPLIT_SCORE]`.
  최종 게이트의 `score_policy.micro_split_floor`.
- 되돌리기: `.env`에 `MICRO_SPLIT_MIN_SCORE=off`를 넣으면 기존 국면 하한으로 돌아간다. 숫자를 넣으면 기준값이 바뀐다.

## 상위 셋업 가중 (2026-10-04 추가, 사용자 승인 설계 2)

- **규칙**: buy_score ≥ 8(LLM이 쓴 원래 점수)이고 트리거가 강한 트리거이면 최초 비중을 한 단계 크게 시작합니다.
  최초 비중 = min(0.80, B3 최초 비중 + 0.20).
  - KR 강한 트리거: "일중 상승률 상위주", "갭 상승 모멘텀 상위주"
  - US 강한 트리거: "Intraday Rise Top", "Gap Up Momentum Top"
  - B3 최초 비중이 이미 80%이면 바뀌지 않으며 기록도 남기지 않습니다.
- **적용 범위**: 정규 배치의 신규 초분할 진입만입니다. 재진입 v3(`require_micro_plan`)은 트리거 종류를 넘기지 않으므로 가중하지 않습니다.
- **원장 무결성**: B3 계획(`initial_nominal`, `plan_hash`)과 B3 가상 원장(LIVE 캠페인의 비교용 사다리)은 변동성 기준 최초 비중을 그대로
  씁니다. 가중은 보유 행 `scenario.micro_split.conviction_tilt = {"base", "tilted", "reason": "CONVICTION_TOP_SETUP", "buy_score",
  "trigger_type"}`에 명시적으로 남기고, `allocation`과 첫 다리(INITIAL)의 비중은 가중된 값입니다.
- **같은 값을 쓰는 곳**: 주문 금액(`scaled_cash`), 사용 슬롯(`used_slots`·`slot_fraction`), 비중 표시(`display`·`allocation_line`),
  대시보드 필드, 매매일지 줄, 시그널 `position_fraction`, 증액 안전장치(현재 비중보다 큰 목표·직전 매수분 이하·위험 한도),
  매수 메시지("상위 셋업 가중으로 기본 M%에서 상향")가 모두 보유 행의 가중된 비중을 읽습니다.
- **프롬프트에는 알리지 않음(2026-10-04 결정)**: "8점 이상이면 더 크게 산다"를 LLM에 알리면 점수를 올릴 유인이 생겨 매수
  판단의 정확도라는 핵심과 어긋납니다. 그래서 강한 트리거 종목의 BUY 프롬프트도 다른 종목과 바이트 단위로 같습니다.
- **add_plan 목표 재기준(코드)**: LLM은 변동성 기준 최초 비중(예상 약 X%)을 보고 목표를 씁니다. 가중이 적용되면 코드가 각
  시나리오 목표를 목표' = min(1.00, 목표 + (가중값 − 기본값))으로 옮기고 5%p 단위로 다시 맞춥니다(계획한 증액 폭 유지). 그 뒤 평소
  검증(현재 비중보다 큰 목표, 0.25 이하, 직전 매수분 이하)을 그대로 거치며, 계획 기록에 `rebased: true`, `rebase_delta`를
  남깁니다(계획 해시에 포함). 원문 JSON은 이력의 원문 해시로 남습니다.
- **비상 스위치**: `.env`에 `MICRO_SPLIT_CONVICTION_TILT=false`(기본 켜짐). 끄면 다음 진입부터 B3 최초 비중으로 돌아가고 목표 재기준도
  하지 않습니다. 이미 열린 보유는 기록된 비중 그대로입니다.
- **로그**: `[MICRO_SPLIT][KR|US] <ticker> initial=<가중 후> b3_initial=<B3> tilt=CONVICTION_TOP_SETUP cash=<금액>`.
