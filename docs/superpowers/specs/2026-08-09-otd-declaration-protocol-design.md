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

### 4.3 서버가 덧붙이는 필드 (클라이언트가 쓸 수 없음)

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
- 동일 종목 추가 매수 시 weight 는 누적된다.

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

## 9. 리더보드 지표

**⚠️ 순위 산정 기준은 미확정이다 (§10 참조).** 아래는 계산해서 보여줄 후보 지표다.

| 지표 | 설명 |
|---|---|
| 누적 수익률 | NAV - 1.0 |
| 기간 수익률 | 1개월 / 3개월 / YTD |
| MDD | 최대 낙폭 |
| 승률 | 청산 완료 포지션 기준 |
| 손익비 | 평균 수익 / 평균 손실 |
| 평균 보유기간 | 진입~전량청산 |
| 회전율 | 거래비용 왜곡을 드러냄 |
| **제출 커버리지** | 조작 탐지용. 반드시 병기 |
| 현재 포지션 스냅샷 | 이벤트 fold 결과 |

---

## 10. 미결정 사항

| # | 항목 | 비고 |
|---|---|---|
| 1 | **리더보드 순위 기준** | 누적수익률 단순 정렬? Sharpe? 기간 보정? 등록 시점이 다른 전략을 어떻게 공정하게 비교할지가 핵심 난제. **동기부여를 좌우하므로 별도 논의 필요** |
| 2 | 전략 등록 승인 절차 | 자동 승인 vs 수동. 스팸·다중등록 방지 |
| 3 | 지원 시장 범위 | v0.1은 KRX만? US·크립토는 언제 |
| 4 | 클라이언트 SDK 제공 여부 | HTTP 한 방이면 되므로 불필요할 수 있음. Python 얇은 헬퍼 정도 |
| 5 | PRISM 연동 시점 | `prism_core/order_intents.py` 의 `IntentStore` 에 발행 훅을 다는 형태가 자연스러움 |
| 6 | 공개 시점 | 초기 참여자 확보 전략 (§11) |

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
