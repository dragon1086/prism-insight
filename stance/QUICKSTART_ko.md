# Stance 연동 빠른 시작

계좌·잔고·주문 내역·증권사 키를 보내지 않는다. 전략은 **목표 비중**만 선언한다.
서버가 접수 시각과 가격을 봉인하고, 체결·자산·성과를 계산한다.

## 1. 전략 등록 — 대시보드에서 최초 1회

**[PRISM 대시보드의 Stance 탭](https://analysis.stocksimulation.kr/?tab=stance)**에서
전략 이름·시장·판단 주기를 입력한다. 계좌 연결은 없다.

등록 직후 `api_key`가 한 번만 표시된다. 바로 비밀 저장소에 보관한다.
운영등록 토큰이나 별도 서버 주소는 필요 없다.

```bash
export STANCE_URL="https://analysis.stocksimulation.kr/api/stance/v1"
export STANCE_API_KEY="stk_..."
```

## 2. 판단 선언

`target_weight`는 총자산 중 해당 종목의 목표 평가액 비율이다.

```bash
# 삼성전자 10%
curl -sS "$STANCE_URL/stances" \
  -H "Authorization: Bearer $STANCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"protocol":"stance/1","seq":1,"kind":"set","symbol":"005930","target_weight":0.10}'

# 전량 청산
curl -sS "$STANCE_URL/stances" \
  -H "Authorization: Bearer $STANCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"protocol":"stance/1","seq":2,"kind":"set","symbol":"005930","target_weight":0}'

# 관망
curl -sS "$STANCE_URL/stances" \
  -H "Authorization: Bearer $STANCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"protocol":"stance/1","seq":3,"kind":"hold","reason":"no signal"}'
```

`set` 하나로 매수·축소·청산을 표현한다. `hold`, `pause`, `resume`은 종목과 비중이 없다.

## 3. 판정 처리

```json
{
  "seq": 1,
  "next_seq": 2,
  "received_at": "2026-08-11T09:00:00+00:00",
  "admit": "accepted",
  "reason": null,
  "symbol": "005930",
  "fill_price": 70100.0,
  "requested_weight": 0.1,
  "effective_weight": 0.1,
  "realized_pnl": 0.0,
  "total_assets_after": 1.0,
  "pending": false,
  "replayed": false
}
```

- `accepted`: 요청 비중 반영
- `clamped`: 가능한 비중까지 축소 반영 — `effective_weight` 확인
- `rejected`: 반영 없음 — `reason` 확인
- `pending`: 선언은 봉인, 시세 복구 뒤 판정

주문 실행 정책은 연동 시스템이 정한다. 보수적 기본값은 `accepted`와 `clamped`만 주문,
`rejected`와 `pending`은 주문 중단이다.

## 4. 재시도 규칙

`seq`는 전략별 증가 번호다. 응답을 받기 전까지 같은 `seq`와 같은 본문을 유지한다.
한 전략에는 선언 writer를 하나만 둔다.

- 타임아웃: **같은 요청 그대로 재전송**
- 같은 `seq` + 같은 본문: 원장 추가 없음, 원 판정 반환, `replayed: true`
- 같은 `seq` + 다른 본문: `409 Conflict`
- 재시작: `GET /portfolio`의 `last_seq + 1`

```bash
curl -sS "$STANCE_URL/portfolio" \
  -H "Authorization: Bearer $STANCE_API_KEY"
```

## Python

```python
from stance.client import StanceClient

stance = StanceClient(STANCE_URL, STANCE_API_KEY)  # 생성자 네트워크 호출 없음
result = stance.set("005930", 0.10, reason="breakout")

if result["admit"] not in {"accepted", "clamped"}:
    raise RuntimeError(result["reason"] or result["admit"])
```

기본 타임아웃은 3초다. 첫 선언에서만 `last_seq`를 복구한다.
복구 실패는 숨기지 않고 예외로 반환한다.

## JavaScript / TypeScript

```js
const headers = {
  Authorization: `Bearer ${process.env.STANCE_API_KEY}`,
  "Content-Type": "application/json",
};
const base = process.env.STANCE_URL;
const portfolio = await fetch(`${base}/portfolio`, { headers }).then(r => {
  if (!r.ok) throw new Error(`portfolio ${r.status}`);
  return r.json();
});
const declaration = {
  protocol: "stance/1",
  seq: portfolio.last_seq + 1,
  kind: "set",
  symbol: "005930",
  target_weight: 0.10,
};
const response = await fetch(`${base}/stances`, {
  method: "POST",
  headers,
  body: JSON.stringify(declaration),
});
if (!response.ok) throw new Error(`stance ${response.status}: ${await response.text()}`);
const result = await response.json();
```

타임아웃 뒤에는 `declaration`을 바꾸지 않고 재전송한다.

## 키 교체·API 명세

```bash
curl -sS -X POST "$STANCE_URL/keys/rotate" \
  -H "Authorization: Bearer $STANCE_API_KEY"
```

새 키는 한 번만 표시되며 기존 키는 즉시 폐기된다.

- 공개 표준: [`stance/spec/core-spec.md`](spec/core-spec.md)
- 시장 규칙: `$STANCE_URL/markets`
- 상태 점검: `$STANCE_URL/health` — 운영 원장은 `durable: true`

HTTP `400/422`는 요청 수정, `401/403`은 키·권한 확인, `409`는 seq 충돌,
`429`는 지수 백오프 후 재시도한다. `5xx`와 네트워크 타임아웃은 같은 본문으로 재시도한다.
