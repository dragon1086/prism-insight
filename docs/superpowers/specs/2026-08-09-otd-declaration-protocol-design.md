# OTD — 매매 선언 프로토콜 설계

> **Status**: Draft · **Date**: 2026-08-09 · **Issue**: [#328](https://github.com/dragon1086/prism-insight/issues/328)
> **Protocol version**: `otd/0.1`

---

## 1. 배경과 목적

이슈 #328의 원래 취지는 다음과 같다:

> *"같은 시스템을 돌리더라도 LLM의 확률적인 특성상 다른 종목으로 포트폴리오가 구성되므로,
> 자금의 응집력이 약해서 장기적으로 전략이 생존한다는 걸 증명"*

이를 검증하려면 **여러 전략의 매매 판단을 같은 잣대로 채점**할 수 있어야 한다.
그런데 각자 로컬에서 돌리는 시스템의 **수익률을 신고받는 방식은 100% 위조 가능**하므로 리더보드가 성립하지 않는다.

**OTD(Open Trading Declaration)** 는 이 문제를 다음 한 문장으로 뒤집는다:

> **실적을 신고받지 말고, 판단을 사전에 선언받아라. 성과는 서버가 계산한다.**

### 시스템 무관성

OTD는 PRISM-INSIGHT 전용이 아니다. **모든 시스템 트레이딩이 대상**이다.
PRISM은 첫 번째 클라이언트일 뿐이며, 프로토콜에는 PRISM 고유 개념이 들어가지 않는다.

---

## 2. 설계 원칙

세 문장이 이 설계의 전부다.

1. **파생값을 신고받지 않는다.** 수익률·잔고·계좌규모·슬랏 수는 받지 않는다. 전부 서버가 계산하거나 애초에 불필요하다.
2. **서버가 받은 시각보다 앞선 가격은 절대 인정하지 않는다.** 이것이 위조 방지의 유일한 뿌리다.
3. **상태를 받지 말고 이벤트를 받아 서버가 접는다(fold).** 포지션·NAV는 선언 이벤트를 재생해 재구성한다.

원칙 3의 부수 효과로 **분할매수·분할매도가 별도 설계 없이 자동 해결**된다. 그냥 이벤트가 여러 개일 뿐이다.

---

## 3. 범위

### 포함

- 전략 등록 / 선언 이벤트 수신 / 무결성 봉인
- 서버측 포지션·NAV 재구성 및 채점
- 리더보드 산출

### 명시적 비포함 (Non-goals)

이것은 **판단 리더보드**이지 **실계좌 성과 리더보드**가 아니다. 따라서 다음은 프로토콜에서 제외한다.

| 제외 항목 | 이유 |
|---|---|
| **미체결 처리** | 서버가 수신 시점 실측가로 체결을 확정하므로 미체결이라는 개념 자체가 없다 |
| **지정가·예약주문** | "산다 했제"만 받는다. 주문 유형은 판단이 아니라 집행의 문제다 |
| **장 마감 후 선언 규칙** | 그 시각 최종가로 체결 확정. 별도 규칙 불필요 |
| **계좌 규모·잔고** | 모든 전략을 NAV=1.0으로 정규화하므로 받을 이유가 없다 |
| **슬랏(보유종목 수) 개념** | weight 기반이므로 슬랏 10개든 20개든 자동으로 비교 가능하다 |
| **브로커 연동·API 키** | 클라이언트 로컬을 절대 떠나지 않는다. 프로토콜은 이를 알지 못한다 |
| **레버리지·공매도** | v0.1에서는 금지 (`weight > 0`, `Σweight ≤ 1.0`) |

### 비포함으로 하고 싶지만 반드시 포함해야 하는 3가지

단순화 욕심에 빼면 리더보드가 무의미해지는 항목들이다.

1. **수정주가 처리 (분할·배당)** — 빼면 버그다. 액면분할 한 번에 -50%가 찍히고 이는 판단과 무관하다.
2. **`Σweight ≤ 1.0` 제약** — 안 걸면 누군가 `weight: 10.0`으로 레버리지 10배 선언해 리더보드를 장악한다.
3. **거래비용** — KR 거래세 0.18%만으로도 월 100회전 전략과 3회전 전략 사이에 **18%p 차이**가 난다. 순위가 뒤집힌다.
   단, 복잡할 필요 없이 **시장별 상수 하나**로 충분하다.

---

## 4. 프로토콜 명세

### 4.1 전략 등록 (전략당 1회)

```json
{
  "strategy_id":   "prism-kr-gpt5",
  "display_name":  "PRISM KR (GPT-5)",
  "author":        "dragon1086",
  "description":   "오닐 추세추종 + 매크로 국면 기반 스윙",
  "markets":       ["KRX"],
  "started_at":    "2026-09-01"
}
```

등록 시 **전략 전용 API 키**가 발급된다. 이 키로만 해당 전략에 선언을 쓸 수 있다.

**전략은 삭제할 수 없다.** 등록 시점부터의 전 이력이 영구히 공개된다.
이는 "여러 전략을 등록해 놓고 잘된 것만 홍보"하는 생존편향 조작을 막는 유일한 방법이다.

### 4.2 선언 이벤트

클라이언트가 보내는 유일한 메시지다.

```json
{
  "protocol":    "otd/0.1",
  "strategy_id": "prism-kr-gpt5",
  "seq":         42,
  "ts":          "2026-08-09T00:12:00Z",
  "market":      "KRX",
  "symbol":      "005930",
  "action":      "buy",
  "weight":      0.10,
  "reduce":      null,
  "price":       71200,
  "reason":      "20일선 눌림목, 거래량 회복",
  "meta":        { "final_score": 0.83, "selection_channel": "top-down" }
}
```

| 필드 | 필수 | 설명 |
|---|:---:|---|
| `protocol` | ✅ | 프로토콜 버전 |
| `strategy_id` | ✅ | 등록된 전략 ID |
| `seq` | ✅ | 전략별 단조증가 정수. **누락·중복·재전송 감지용** |
| `ts` | ⬜ | 클라이언트 시각. **참고용일 뿐 채점에 쓰이지 않는다** |
| `market` | ✅ | `KRX` \| `NASDAQ` \| `NYSE` \| … |
| `symbol` | ✅ | 종목 코드 |
| `action` | ✅ | `buy` \| `sell` |
| `weight` | buy만 | 그 시점 NAV 대비 투입 비중 (0 < w ≤ 1) |
| `reduce` | sell만 | 현 보유 수량 중 청산 비율 (0 < r ≤ 1, `1.0` = 전량) |
| `price` | ⬜ | 신고 체결가. 없으면 서버 실측가를 사용 |
| `reason` | ⬜ | 자유 텍스트. 채점에 영향 없음. 리더보드에 노출 |
| `meta` | ⬜ | 시스템별 자유 필드. 스키마 강제 없음 |

**분할매수** = `buy` 이벤트 여러 개. **분할매도** = `reduce: 0.3` 세 번.
프로토콜에 분할 개념이 따로 없다.

### 4.3 클라이언트 계산 규칙 (참여자 가이드)

> **이 절이 프로토콜에서 가장 자주 깨지는 지점이다.**
> 참여자마다 계산 기준이 다르면 리더보드 전체가 무의미해지므로, 정의를 여기서 못 박는다.

#### 클라이언트가 계산할 값은 2개뿐이다

| 값 | 언제 | 공식 |
|---|---|---|
| `weight` | 매수 시 | `이번 매수에 투입한 금액 ÷ 전략 자본(NAV)` |
| `reduce` | 매도 시 | `이번에 매도한 수량 ÷ 매도 직전 보유 수량` |

**둘 다 나눗셈 한 번이다.** 평단·손익·수익률·NAV 추이를 계산할 필요가 없다.

#### 클라이언트가 절대 계산하면 안 되는 값

수익률 · 실현손익 · 평균단가 · MDD · 승률 · 손익비 · 포지션 비중 — **전부 서버가 계산한다.**
클라이언트가 이 값들을 보내더라도 채점에 사용되지 않는다 (§2 원칙 1).

#### NAV(전략 자본)의 정의 — 가장 중요

> **NAV = 전략이 운용하는 현금 + 전략이 보유한 모든 종목의 현재 평가액**

두 가지를 반드시 지켜야 한다.

1. **현금만 쓰면 안 된다.** 보유 종목 평가액을 포함한 총액이 분모다.
2. **전략 전용 자본만 센다.** 같은 계좌에 다른 목적의 자금이 섞여 있으면 제외한다.
   계좌 1억 중 5천만원만 이 전략이 운용한다면 NAV는 5천만원이다.

#### 흔한 오류 (반례)

| ❌ 잘못된 계산 | 무슨 일이 벌어지나 |
|---|---|
| `weight = 투입금액 ÷ 예수금` | 분모가 작아 weight가 과대평가됨. 보유가 많을수록 심해져 `Σweight > 1.0` 거부 폭증 |
| `weight = 투입금액` (절대금액) | 스케일 완전 붕괴. 100,000 같은 값이 들어와 즉시 거부 |
| `weight = 투입금액 ÷ 계좌 전체` (다른 자금 포함) | weight가 과소평가됨. 실제보다 소극적인 전략으로 기록됨 |
| `reduce = 매도금액 ÷ 총자산` | 완전히 다른 의미. 거의 항상 지나치게 작은 값 |
| `reduce = 매도수량 ÷ 최초 매수수량` | 이미 분할매도한 뒤라면 틀림. **분모는 언제나 "매도 직전" 보유 수량** |
| 실제로 30% 팔고 `reduce: 0.5` 전송 | 아무도 못 막는다. 다만 **자기 성과만 부정확해질 뿐 이득이 없다** |

#### "대충 던지는" 참여자는 어떻게 막나

**막지 않는다. 막을 수 없고, 막을 필요도 없다.**

부정확하게 보고하면 **자기 전략의 기록만 부정확해진다.** 유리해지는 방향이 없다.
`weight`를 부풀리면 `Σweight ≤ 1.0` 에 걸려 거부되고, 줄이면 성과가 축소 기록된다.
`reduce`를 부정확하게 보내면 서버 모델 포지션이 실계좌와 벌어져 이후 채점이 자기 실제 판단과 멀어진다.

대신 **틀렸다는 사실을 즉시 알 수 있게** 만든다 — 아래 검산 응답과 드리프트 소멸 성질이 그 장치다.

#### 서버 검산 응답 (echo-back)

워커가 fold를 마치면 각 선언에 결과가 채워진다. 클라이언트는 `GET /declarations/{id}` 또는
`GET /portfolio` 로 이를 조회해 자기 계좌와 대조할 수 있다.

```json
{ "status": "accepted",
  "fill_price": 20000,
  "position_after": { "symbol": "005930", "avg_cost": 10000, "weight": 0.0909 },
  "nav_after": 1.0998,
  "realized_pnl": 0.0498,
  "realized_return": 0.996 }
```

**계산을 클라이언트가 하지 않고, 서버가 해서 돌려준다.** 이것이 기준 불일치를 없애는 핵심 장치다.

> v0.1에서 이 응답은 **비동기**다. INSERT 직후에는 `status: pending` 이며,
> 워커가 시세 실측과 fold를 마친 뒤(수 초) 값이 채워진다.
> 클라이언트는 즉시 확인할 필요가 없고, 다음 거래 전에 `GET /portfolio` 로 확인하면 충분하다.

#### 드리프트는 누적되지 않는다

`reduce` 를 부정확하게 보내면 서버 모델의 보유 수량이 실계좌와 벌어진다.
그러나 이 괴리는 **해당 포지션을 전량 청산(`reduce: 1.0`)하는 순간 소멸한다.**
다음 매수는 `weight` 로 새로 시작하므로 수량 오차가 이월되지 않는다.

즉 드리프트는 **포지션 단위로 격리**되며 전략 전체로 번지지 않는다.
(실현손익 금액을 통해 NAV에 남는 잔여 영향은 있으나, 부분매도를 정확히 보고하면 0이다.)

#### 서버측 이상 감지

서버는 다음을 감지해 참여자에게 경고한다. **순위에는 반영하지 않는다** — 처벌이 아니라 교정이 목적이다.

| 신호 | 추정 원인 |
|---|---|
| `Σweight` 가 지속적으로 0.1 미만 | 분모를 계좌 전체로 잡았거나 절대금액 혼동 |
| `weight` 거부율 20% 초과 | 분모를 예수금으로 잡음 |
| `reduce > 1.0` 시도 | 분모를 최초 매수 수량으로 잡음 |
| 매수만 있고 매도가 없음 | 매도 이벤트 발행 누락 |

### 4.4 서버가 덧붙이는 필드 (클라이언트가 쓸 수 없음)

```json
{
  "received_at":            "2026-08-09T00:12:03.418Z",
  "market_price_at_receipt": 71150,
  "fill_price":             71200,
  "price_verified":         true,
  "status":                 "accepted",
  "nav_before":             1.0834,
  "nav_after":              1.0834,
  "prev_hash":              "a3f1…",
  "hash":                   "9c02…"
}
```

`received_at` 이 **채점에 쓰이는 유일한 권위 시각**이다.

---

## 5. 체결가 결정과 밴드 검증

### 원칙

> 서버가 수신한 시각보다 앞선 가격은 절대 인정하지 않는다.

### 절차

```
① INSERT 즉시 received_at 봉인 (DB가 생성, 클라이언트 접근 불가)
② 워커가 수 초 내 해당 종목 현재가를 실측 → market_price_at_receipt
③ 신고 price 가 실측가 ±BAND 이내면 accepted, 밖이면 실측가로 대체
④ fill_price 확정 → 이후 수정 불가
```

**분봉 이력이 필요 없다.** 사후에 "그 시각 가격대"를 조회하는 대신, **수신 시점에 서버가 직접 찍는다.**
PRISM의 `cores/kis_market_snapshot.py: fetch_kis_intraday_snapshot` 이 이 역할을 그대로 수행할 수 있다.

**워커의 수 초 지연은 무해하다.** 조작자가 노리는 것은 `received_at` **이전**의 가격인데,
`received_at` 이 DB에 박히는 순간 그것은 원천 불가능하기 때문이다.

### BAND 값

v0.1 기본값 **±1.0%**. 실측 시점과 실제 체결 시점의 미세한 차이를 흡수하되 의미 있는 조작은 차단한다.

### 시세 실측이 불가능한 경우 (장 종료 후, 휴장일)

해당 시장의 **직전 정규장 종가**를 실측가로 사용한다. 이 경우 `price_verified: false` 로 표시하고
신고 단가는 무시한다 (실측가 강제).

---

## 6. 채점 규칙

### 6.1 NAV 정규화

모든 전략은 **NAV = 1.0** 에서 시작한다. 실계좌 규모를 받지 않는다.

- `weight: 0.1` = "그 시점 NAV의 10%를 투입"
- 미투자 현금은 수익률 0
- 슬랏 수와 무관하게 자동으로 비교 가능
- 자금 규모가 노출되지 않는다

### 6.2 포지션 관리

- 종목 단위 **평균단가** 방식. 매수 이벤트마다 평단 갱신.
- `sell` 의 `reduce` 는 **현 보유 수량 대비 비율**이다. lot 지정은 v0.1에서 지원하지 않는다.
- 동일 종목 추가 매수 시 투입 비중은 누적된다.

**`reduce` 가 수량 기준인 이유 — 스케일 불변성**

`reduce: 0.5` 는 "보유 수량의 50%"이며, 같은 종목이므로 "보유 평가액의 50%"와 동일하다.
"원래 투입 비중의 50%"라는 해석은 **명시적으로 배제한다** (그것은 다른 값이다).

절대 금액이나 절대 수량이 아니라 **비율**을 받는 이유는 스케일 불변성 때문이다.
클라이언트의 실계좌가 5천만원이든 5억이든, 서버 모델이 NAV=1.0이든,
"보유 수량의 50%"는 양쪽에서 정확히 같은 의미다. 절대값을 받으면 규모 차이로 어긋난다.

**워크드 예시 — 2배 상승 후 50% 부분매도**

| 단계 | 현금 | 포지션 평가 | 포지션 원가 | NAV | 포지션 비중 |
|---|---|---|---|---|---|
| 시작 | 1.0 | — | — | 1.0 | — |
| ① `buy weight 0.1` @10,000 | 0.9 | 0.1 | 0.1 | 1.0 | 10.00% |
| ② 주가 2배 → 20,000 | 0.9 | 0.2 | 0.1 | **1.1** | **18.18%** |
| ③ `sell reduce 0.5` @20,000 | 0.9998 | 0.1 | 0.05 | 1.0998 | 9.09% |

②에서 포지션 비중이 **0.2가 아니라 18.18%** 인 점에 주의한다. 분모인 NAV도 함께 커졌기 때문이다.

③ 계산: 수량 절반 매도 → 매도대금 0.1 → 거래비용 0.20% 차감 → 실수령 0.0998.
평단은 10,000원 그대로 유지되므로 잔여 원가는 0.05.
**실현손익 = 0.0998 − 0.05 = +0.0498** (실현수익률 +99.6%, 비용 반영).

**목표비중 리밸런싱은 어떻게 표현하나**

위 예시에서 18.18%를 다시 10%로 줄이려면 `reduce: 0.45` 를 보내면 된다.
이 비율을 계산하려면 클라이언트가 서버측 NAV를 알아야 하므로,
서버는 **포지션 조회 API (`GET /portfolio`)** 를 제공한다.

`target_weight` 필드를 추가해 서버가 대신 계산하게 하는 방법도 있으나 채택하지 않는다.
그 경우 서버가 계산한 매도 비율과 클라이언트가 실제로 매도한 비율이 어긋나
선언의 충실도가 깨진다. `reduce` 가 엄밀히 더 정확하다.

**클라이언트 실계좌와 서버 모델의 드리프트**

거부된 선언, 체결가 차이, 클라이언트측 수동 개입 등으로 서버 모델 NAV와 실계좌는 시간이 지나며 벌어질 수 있다.
**채점의 기준은 언제나 서버 모델이다.** 이는 판단 리더보드로서 의도된 동작이며,
실계좌 성과를 재현하려는 것이 아니다 (§3 Non-goals 참조).

### 6.3 거부 규칙

다음 선언은 `status: rejected` 로 기록되며 (삭제되지 않음) 채점에 반영되지 않는다.

| 조건 | 사유 |
|---|---|
| **현금 잔고 < `weight` × NAV** | 레버리지 금지 (아래 참조) |
| 보유하지 않은 종목에 `sell` | 공매도 금지 |
| `weight ≤ 0` 또는 `reduce ≤ 0` | 유효 범위 위반 |
| `seq` 가 기존 값보다 작거나 같음 | 재전송·역순 |
| 등록되지 않은 `market`/`symbol` | 검증 불가 |

**레버리지 제약의 정확한 정의**

`Σweight ≤ 1.0` 을 *보유 종목의 현재 평가비중 합*으로 해석하면 안 된다.
포지션이 전부 상승하면 평가비중 합이 자연히 1.0을 넘어가는데, 그때 신규 매수를 막는 것은 잘못이다.

정확한 제약은 **선언 시점의 현금 잔고 기준**이다:

> `cash ≥ weight × NAV` 이면 승인. 아니면 거부.

여기서 `cash` 는 이벤트 fold로 재구성된 미투자 현금이고, `NAV` 는 그 시점 총자산(현금 + 보유 평가액)이다.
이 정의는 자동으로 레버리지를 차단하면서 수익 중인 포트폴리오의 정상적인 재투자를 허용한다.

### 6.4 거래비용

**시장별 상수 하나.** 매도 시에만 적용한다.

| 시장 | 매도 시 차감 |
|---|---|
| `KRX` | 0.20% (거래세 0.18% + 수수료 근사) |
| `NASDAQ` / `NYSE` | 0.01% |

클라이언트가 이 값을 지정할 수 없다.

### 6.5 코퍼레이트 액션

- **분할·병합·배당** — 수정주가 기준으로 보유 수량·평단을 조정한다. **필수.**
- **거래정지** — 정지 직전 가격으로 평가를 고정한다.
- **상장폐지** — 정리매매 최종가로 강제 청산한다. 정리매매가 없으면 0원 처리.

### 6.6 미제출일 처리

특정 거래일에 선언이 없으면 **그날은 직전 포지션이 유지된 것**으로 본다 (무포지션이 아니라 홀딩).
다만 **제출 커버리지**를 별도 지표로 계산해 리더보드에 병기한다.

> 커버리지 = (선언이 1건 이상 있었던 거래일 수) / (전략 등록 이후 총 거래일 수)

이는 "지는 날엔 안 보내기" 조작을 드러내기 위한 것이다.

---

## 7. 무결성 모델

### 조작 경로와 대응

| 조작 시나리오 | 대응 | 잔여 리스크 |
|---|---|---|
| 수익률을 부풀려 신고 | **수익률을 받지 않는다.** 서버가 계산 | 없음 |
| 저점 매수가를 사후에 적어냄 | `received_at` 봉인 + 실측가 밴드 검증 | 없음 |
| 여러 전략 등록 후 잘된 것만 홍보 | 전략 **삭제 불가**, 전 이력 영구 공개 | 낮음 (홍보는 막을 수 없으나 검증 가능) |
| 지는 날 선언 생략 | 미제출일은 홀딩 처리 + **커버리지 병기** | 낮음 |
| 과거 선언을 수정·삭제 | DB 레벨 append-only (RLS) + 해시체인 | 없음 (클라이언트 기준) |
| 레버리지로 순위 조작 | `Σweight ≤ 1.0` | 없음 |
| **운영자가 직접 DB 수정** | 해시체인. 단 완전 방지는 외부 앵커링 필요 | ⚠️ 아래 참조 |

### 운영자 신뢰 문제

해시체인은 **클라이언트의 조작**은 막지만 **DB 운영자(=형님)의 조작**은 원리적으로 막지 못한다.
공개 리더보드로서의 신뢰가 필요해지면 **외부 앵커링**을 추가한다:

> 매일 장 마감 후 그날의 전체 선언에 대한 **머클 루트**를 공개 저장소(GitHub 커밋)에 기록.
> 이후 누구든 로컬에서 재계산해 대조할 수 있다.

v0.1에서는 생략하고, 참여자가 형님 외로 늘어나는 시점에 도입한다.

---

## 8. 아키텍처

### 8.1 구성

```
[클라이언트 — 아무 시스템, 아무 언어]
   POST /rest/v1/declarations   (HTTP 한 방. SDK 불필요)
                ↓
[Supabase Postgres]
   · received_at DEFAULT now()          ← 권위 시각, 클라이언트 접근 불가
   · RLS: INSERT만 허용, UPDATE/DELETE 전면 금지  ← append-only 강제
   · BEFORE INSERT 트리거로 prev_hash / hash 계산
                ↓
[워커 — PRISM 서버 또는 GitHub Actions]
   ① 시세 실측 → market_price_at_receipt, fill_price 확정
   ② 이벤트 fold → 포지션·NAV 재구성
   ③ 일별 채점 → 지표 갱신
                ↓
[리더보드]  Supabase view 를 그대로 읽음. 실시간 구독 가능
[조회 API]  GET /portfolio — 클라이언트가 리밸런싱 비율을 계산하기 위한 현재 포지션·NAV 스냅샷
```

### 8.2 Supabase를 선택한 이유

| 필요한 것 | Supabase가 제공 |
|---|---|
| 권위 있는 수신 시각 | `received_at timestamptz DEFAULT now()` — DB가 생성 |
| append-only 강제 | RLS로 UPDATE/DELETE 차단 → **DB 레벨에서 사후 조작 불가** |
| 전략별 인증 | API 키 + RLS로 "자기 전략에만 쓰기" |
| 읽기 API | PostgREST 자동 생성 |
| 실시간 대시보드 | 구독 기본 제공 |
| 인프라 | 서버·도메인·배포 없음. 무료 티어로 충분 |

**대안 검토 결과**

- **텔레그램 봇** — 이미 인프라가 있으나, 연동 개발자에게 **텔레그램 계정을 강제**한다. 시스템 무관 프로토콜의 취지에 어긋나 탈락.
- **메시지 큐** — 하루 수십~수백 건 규모에 과잉. 진입장벽만 올린다. 탈락.
- **자체 HTTP API** — 가장 정석이나 서버·도메인·인증·운영을 전부 새로 만들어야 한다. Supabase가 그 전부를 대체하므로 보류.

**정직한 단점**: Supabase 종속 (다만 순수 Postgres라 이전 용이), 무료 프로젝트는 1주 완전 무사용 시 일시정지 (매일 사용하므로 무관).

### 8.3 스키마 초안

```sql
create table strategies (
  strategy_id   text primary key,
  display_name  text not null,
  author        text not null,
  description   text,
  markets       text[] not null,
  started_at    date not null,
  api_key_hash  text not null,
  created_at    timestamptz not null default now()
);

create table declarations (
  id            bigserial primary key,
  strategy_id   text not null references strategies(strategy_id),
  seq           bigint not null,

  -- 클라이언트 제출
  ts            timestamptz,
  market        text not null,
  symbol        text not null,
  action        text not null check (action in ('buy','sell')),
  weight        numeric check (weight > 0 and weight <= 1),
  reduce        numeric check (reduce > 0 and reduce <= 1),
  price         numeric,
  reason        text,
  meta          jsonb default '{}'::jsonb,

  -- 서버 생성 (클라이언트 쓰기 불가)
  received_at   timestamptz not null default now(),
  market_price_at_receipt numeric,
  fill_price    numeric,
  price_verified boolean,
  status        text not null default 'pending',
  nav_before    numeric,
  nav_after     numeric,
  prev_hash     text,
  hash          text,

  unique (strategy_id, seq),
  check ((action = 'buy'  and weight is not null and reduce is null)
      or (action = 'sell' and reduce is not null and weight is null))
);

-- append-only 강제
alter table declarations enable row level security;

create policy declarations_insert on declarations
  for insert with check (strategy_id = current_setting('request.jwt.claims', true)::json->>'strategy_id');

create policy declarations_read on declarations
  for select using (true);   -- 전 이력 공개

-- UPDATE / DELETE 정책을 만들지 않음 → 전면 차단
```

`received_at`, `fill_price`, `hash` 등 서버 필드는 **컬럼 레벨 권한**으로 클라이언트 쓰기를 차단한다.

> **주의**: 위 RLS 정책은 예시다. 발급한 전략 API 키를 `strategy_id` 에 바인딩하는 방식
> (Supabase Auth 사용자로 매핑 vs 커스텀 JWT 발급 vs Edge Function 게이트웨이)은 구현 시 결정한다.
> 어느 방식이든 만족해야 할 요구사항은 하나다 — **클라이언트는 자기 전략에만 INSERT할 수 있고,
> UPDATE·DELETE는 누구도 할 수 없다.**

---

### 8.4 조회 API — `GET /portfolio`

클라이언트의 검산과 리밸런싱 비율 계산에 필요한 최소 정보를 제공한다.

```json
{ "strategy_id": "prism-kr-gpt5",
  "as_of":       "2026-08-09T06:30:00Z",
  "last_seq":    43,
  "nav":         1.0998,
  "cash":        0.9998,
  "positions": [
    { "market": "KRX", "symbol": "005930",
      "avg_cost": 10000, "market_value": 0.1, "weight": 0.0909 }
  ] }
```

`last_seq` 는 클라이언트 프로세스 재시작 시 `seq` 복구에 사용한다.

## 9. 리더보드 순위 설계

### 9.1 해결해야 할 문제

| # | 문제 | 방치하면 |
|---|---|---|
| 1 | **선착순 우위** — 등록 시점이 다름 | 먼저 시작한 전략이 복리 기간만으로 영구히 1등 |
| 2 | **위험 무시** | 레버리지·집중투자로 변동성을 키운 전략이 누적수익률 1등 |
| 3 | **운 vs 실력** | 2개월 돌린 고수익 전략이 3년 검증된 전략을 이김 |
| 4 | **표본 부족** | 거래 5건으로 승률 100% 달성 |
| 5 | **시장 베타** | 강세장에 시작한 전략이 자동으로 유리 |
| 6 | **생존편향** | 죽은 전략이 사라지면 전체 평균이 부풀려짐 |

### 9.2 참고한 업계 관행

- 실전 트레이딩 대회는 **4축 동등가중** 평가를 쓴다 — 수익 / 위험관리(MDD·Sharpe·Calmar·Sortino) / 트랙레코드 일관성 / **검증가능성** (audited > self-reported)
- 최소 트랙레코드 벤치마크는 대체로 **3개월 이상 + 50거래 이상**
- 학술 정석은 **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014) — 다중검정 선택편향·표본길이·비정규성을 보정해 "이 성과가 운일 확률"을 산출

### 9.3 설계 — 3단 구조

```
① 자격 게이트 (Qualification Gate)
② 메인 순위   = 롤링 12개월 Sortino Ratio
③ 보조 리그   (동등 노출, 단일 순위 강요하지 않음)
```

#### ① 자격 게이트

세 조건을 **모두** 만족해야 메인 랭킹에 오른다.

| 조건 | 임계값 | 근거 |
|---|---|---|
| 운영 기간 | 60 거래일 이상 | 업계 관행 "3개월" ≈ 60 거래일 |
| 청산 완료 거래 | 20건 이상 | 관행은 50건이나 스윙 전략에 가혹하여 완화. 대신 표본 수를 상시 병기 |
| 제출 커버리지 | 70% 이상 | "지는 날 생략" 조작 차단 |

미달 전략은 **Provisional(예선)** 으로 별도 표시한다. 숨기지 않는다 — 생존편향 방지.

#### ② 메인 순위 — 롤링 12개월 Sortino Ratio

$$\text{Sortino} = \frac{R_p - R_f}{\sigma_{down}}$$

일별 NAV 수익률 기준. 12개월 미만이면 가용 전 기간을 사용한다 (게이트가 최소 60거래일을 보장).
무위험수익률 `R_f = 0` 으로 단순화한다 — 모든 전략에 동일 적용되므로 순위에 영향이 없다.

**Sharpe가 아니라 Sortino인 이유**

Sharpe는 표준편차를 쓰므로 **상방 변동성도 벌점**이다.
큰 수익 몇 번으로 성과를 내는 추세추종 전략은 상방 변동이 클 수밖에 없어 부당하게 불리해진다.
Sortino는 **하방편차만** 벌점하므로 "손실은 짧게, 이익은 길게" 전략과 정합한다.

**롤링 윈도우인 이유**

누적 기준이면 문제 1(선착순 우위)이 영구히 해소되지 않는다.
롤링은 과거 영광에 안주할 수 없게 만들고, 늦게 합류한 전략에도 동등한 기회를 준다.

#### ③ 보조 리그 (동등 노출)

**단일 순위를 강요하지 않는 것이 핵심이다.** 하나의 숫자로 줄이면 반드시 그 숫자를 겨냥한 조작이 생긴다.

| 리그 | 기준 | 성격 |
|---|---|---|
| 명예의 전당 | 전 기간 누적수익률 | 절대 성과 |
| 낙폭 관리 | Calmar = 연율수익 / MDD | 직관적이고 조작 어려움 |
| 최근 폼 | 최근 3개월 수익률 | 현재 상태 |
| 지속성 | 최장 생존 거래일 수 | 오래 살아남는 것 자체가 실력 |
| 신인 리그 | 게이트 미달 전략끼리 | 신규 참여자 동기 |

### 9.4 시장별 보드 분리

`KRX` / `US` / `Crypto` 를 **섞지 않는다.** 벤치마크와 변동성 스케일이 달라 섞으면 비교가 무의미해진다.
다중 시장 전략은 시장별로 분해해 각 보드에 올린다.

### 9.5 전략 카드 상시 병기 지표

순위와 무관하게 모든 전략 카드에 항상 노출한다. **조작을 순위가 아니라 가시성으로 억제한다.**

| 지표 | 드러내는 것 |
|---|---|
| 제출 커버리지 | 선언 생략 조작 |
| 회전율 | 거래비용 왜곡, 과매매 |
| 최대 단일종목 비중 | 집중 리스크 |
| 청산 완료 거래 수 | 표본 충분성 |
| 운영 거래일 수 | 트랙레코드 길이 |
| MDD | 최악의 순간 |
| 벤치마크 대비 초과수익 | 시장 베타 제거 |
| 승률 · 손익비 · 평균 보유기간 | 전략 성격 |
| 현재 포지션 스냅샷 | 이벤트 fold 결과 |

### 9.6 신뢰도 뱃지

Deflated Sharpe Ratio를 **순위 산정에는 쓰지 않는다** — 참여자가 이해하기 어렵고 직관에 반한다.
대신 **뱃지**로 표시한다:

> ✅ **검증됨** — 표본이 충분하고 DSR이 통계적으로 유의함
> ⏳ **표본 부족** — 성과가 운일 가능성을 배제할 수 없음

학술적 엄밀성은 순위가 아니라 라벨로 전달하는 편이 실용적이다.

---

## 10. 미결정 사항

| # | 항목 | 비고 |
|---|---|---|
| 1 | 게이트 임계값 검증 | 60거래일 / 20건 / 커버리지 70% 는 관행 기반 초안. Phase 0 데이터로 재조정 필요 |
| 2 | 전략 등록 승인 절차 | 자동 승인 vs 수동. 스팸·다중등록 방지 |
| 3 | 지원 시장 범위 | v0.1은 KRX만? US·크립토는 언제 |
| 4 | 클라이언트 SDK 제공 여부 | HTTP 한 방이면 되므로 불필요할 수 있음. Python 얇은 헬퍼 정도 |
| 5 | PRISM 연동 시점 | `prism_core/order_intents.py` 의 `IntentStore` 에 발행 훅을 다는 형태가 자연스러움 |
| 6 | 공개 시점 | 초기 참여자 확보 전략 (§11) |
| 7 | 벤치마크 지수 선정 | KRX는 KOSPI? KOSDAQ? 혼합? 전략의 실제 유니버스와 맞춰야 함 |
| 8 | `GET /portfolio` 인증 범위 | 자기 전략만 조회 vs 전체 공개 (전 이력이 공개이므로 후자도 일관됨) |

### 참고 문헌

- Bailey, D. H. & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality.* [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Bailey, Borwein, López de Prado & Zhu (2013). *The Probability of Backtest Overfitting.* [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

---

## 11. 로드맵

### Phase 0 — 자가 대결 (외부 참여자 0명)

**빈 리더보드에는 아무도 오지 않는다.** 그리고 이슈 #328의 가설 검증에는 **외부 참여자가 필요 없다.**

같은 코드에서 판단 에이전트의 **모델만 바꿔** N개 인스턴스를 등록한다:

| 전략 ID | 구성 |
|---|---|
| `prism-kr-gpt5` | 기본 |
| `prism-kr-sonnet46` | 판단 에이전트만 Claude |
| `prism-kr-gemini` | 판단 에이전트만 Gemini |
| `prism-kr-t09` | 동일 모델, temperature 상향 |
| `prism-us`, `prism-btc` | 이미 존재 |

사람이 섞이면 변수가 오염되지만, **모델만 다른 동일 코드는 LLM 확률성의 순효과를 정확히 격리**한다.
이슈 #328의 가설에 오히려 더 충실한 실험이다.

### Phase 1 — 데이터 축적

Phase 0을 수 개월 돌려 리더보드에 **실제 이력을 채운다.**
순서가 중요하다 — **콘텐츠가 먼저, 사람이 나중이다.**

### Phase 2 — 외부 개방

6개월치 데이터가 쌓인 리더보드를 보여주며 참여를 유도한다.
이때 진입장벽을 PRISM에서 분리한다 — PRISM을 쓰지 않아도, 어떤 시스템이든 HTTP 한 방이면 참여할 수 있다.

### Phase 3 — 신뢰 강화 (참여자 증가 시)

- 머클 루트 외부 앵커링
- 전략 등록 승인 절차
- 필요 시 브로커 검증 뱃지 (선택 사항)

---

## 12. 법적 고려

이 설계는 다음 이유로 유사투자자문업 리스크를 낮게 유지한다.

- 각 참여자는 **자기 시스템을 자기 계좌로만** 운용한다. 운영자는 매매에 관여하지 않는다.
- 서버는 **실적을 신고받지 않고** 공개 시세로 계산할 뿐이다.
- API 키·계좌번호·잔고는 프로토콜에 존재하지 않으며 로컬을 떠나지 않는다.

**다만** 리더보드가 특정 전략의 매매를 따라 하도록 유도하는 형태(전략 구독·복사매매 등)로 확장되면
성격이 달라진다. **v0.1 범위에서는 명시적으로 제외한다.**

---

## 부록 A — 최소 클라이언트 예시

```bash
curl -X POST "https://<project>.supabase.co/rest/v1/declarations" \
  -H "apikey: $OTD_STRATEGY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "protocol": "otd/0.1",
        "strategy_id": "my-strategy",
        "seq": 42,
        "market": "KRX",
        "symbol": "005930",
        "action": "buy",
        "weight": 0.1,
        "price": 71200,
        "reason": "20일선 눌림목"
      }'
```

전량 매도:

```json
{ "protocol":"otd/0.1", "strategy_id":"my-strategy", "seq":43,
  "market":"KRX", "symbol":"005930", "action":"sell", "reduce":1.0 }
```

---

## 부록 B — 레퍼런스 클라이언트

§4.3의 계산 규칙을 코드로 강제한 최소 구현이다.
**파라미터 이름이 곧 정의**이므로, 시그니처를 따르면 §4.3의 오류를 범할 수 없다.

```python
import requests


class OTDClient:
    """OTD 선언 클라이언트. 계산은 두 개의 나눗셈이 전부다."""

    def __init__(self, endpoint, strategy_id, api_key, start_seq=0):
        self.endpoint = endpoint.rstrip("/")
        self.strategy_id = strategy_id
        self.api_key = api_key
        self.seq = start_seq          # 영속화는 호출자 책임. GET /portfolio 로 복구 가능

    def buy(self, market, symbol, invested_amount, nav, price=None, reason=None, meta=None):
        """
        invested_amount : 이번 매수에 실제로 투입한 금액
        nav             : 전략 자본 = 운용 현금 + 전략 보유 종목 평가액 총합
                          (예수금만 쓰지 말 것. 다른 목적의 자금은 제외할 것)
        """
        return self._send(market, symbol, "buy",
                          weight=invested_amount / nav,
                          price=price, reason=reason, meta=meta)

    def sell(self, market, symbol, sold_qty, qty_before_sell,
             price=None, reason=None, meta=None):
        """
        sold_qty        : 이번에 매도한 수량
        qty_before_sell : 매도 '직전' 보유 수량 (최초 매수 수량이 아님)
        """
        return self._send(market, symbol, "sell",
                          reduce=sold_qty / qty_before_sell,
                          price=price, reason=reason, meta=meta)

    def sell_all(self, market, symbol, price=None, reason=None, meta=None):
        return self._send(market, symbol, "sell", reduce=1.0,
                          price=price, reason=reason, meta=meta)

    def portfolio(self):
        """현재 포지션·NAV 스냅샷. 리밸런싱 비율 계산과 검산에 사용."""
        r = requests.get(f"{self.endpoint}/portfolio",
                         headers={"apikey": self.api_key},
                         params={"strategy_id": self.strategy_id}, timeout=10)
        r.raise_for_status()
        return r.json()

    def _send(self, market, symbol, action, weight=None, reduce=None,
              price=None, reason=None, meta=None):
        self.seq += 1
        body = {
            "protocol": "otd/0.1",
            "strategy_id": self.strategy_id,
            "seq": self.seq,
            "market": market,
            "symbol": symbol,
            "action": action,
        }
        for k, v in (("weight", weight), ("reduce", reduce),
                     ("price", price), ("reason", reason), ("meta", meta)):
            if v is not None:
                body[k] = v

        r = requests.post(f"{self.endpoint}/declarations",
                          headers={"apikey": self.api_key,
                                   "Content-Type": "application/json"},
                          json=body, timeout=10)
        r.raise_for_status()
        return r.json()
```

사용 예:

```python
otd = OTDClient("https://<project>.supabase.co/rest/v1", "my-strategy", KEY)

# 총자산 5,000만원 중 500만원어치 매수 → weight 0.1
otd.buy("KRX", "005930", invested_amount=5_000_000, nav=50_000_000,
        price=71_200, reason="20일선 눌림목")

# 100주 보유 중 50주 매도 → reduce 0.5
otd.sell("KRX", "005930", sold_qty=50, qty_before_sell=100, price=142_000)

# 잔량 전부 청산
otd.sell_all("KRX", "005930", price=150_000, reason="추세 이탈")

# 검산: 서버가 계산한 포지션·NAV를 자기 계좌와 대조
print(otd.portfolio())
```

**주의**: `seq` 는 전략별 단조증가여야 하며 누락·중복 감지에 쓰인다.
프로세스 재시작 시 `portfolio()` 응답의 `last_seq` 로 복구하거나 로컬에 영속화한다.
