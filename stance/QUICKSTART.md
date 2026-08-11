# Stance Integration Quickstart

Never send account data, balances, orders, or broker credentials. A strategy declares only
its **target weight**. The server seals receipt time and price, then calculates fills, equity,
and performance.

## 1. Register once

```bash
export STANCE_URL="https://stance.example.com"

curl -sS "$STANCE_URL/strategies" \
  -H 'Content-Type: application/json' \
  -H "X-Stance-Registration-Token: $STANCE_REGISTRATION_TOKEN" \
  -d '{"strategy":"my-strategy","display_name":"My Strategy","handle":"@me","market":"KRX","cadence":"daily"}'
```

Store the one-time `api_key` response in your secret manager.
Omit `X-Stance-Registration-Token` when the server operator leaves registration open.

```bash
export STANCE_API_KEY="stk_..."
```

## 2. Declare a decision

```bash
# Set Samsung Electronics to 10% of total assets
curl -sS "$STANCE_URL/stances" \
  -H "Authorization: Bearer $STANCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"protocol":"stance/1","seq":1,"kind":"set","symbol":"005930","target_weight":0.10}'

# Exit: target weight zero
# Hold: {"protocol":"stance/1","seq":2,"kind":"hold","reason":"no signal"}
```

`set` covers buy, reduce, and exit. `hold`, `pause`, and `resume` have no symbol or weight.

## 3. Handle the synchronous verdict

- `accepted`: requested weight applied
- `clamped`: reduced to `effective_weight`
- `rejected`: no change; inspect `reason`
- `pending`: declaration sealed; price unavailable

A conservative order policy executes only `accepted` and `clamped`, and stops on
`rejected` or `pending`.

## 4. Retry safely

Keep the same `seq` and identical JSON until a response arrives.
Use one declaration writer per strategy.

- timeout + identical retry: no new ledger row; original verdict with `replayed: true`
- same `seq` + different body: `409 Conflict`
- process restart: use `GET /portfolio` → `last_seq + 1`

## Python

```python
from stance.client import StanceClient

stance = StanceClient(STANCE_URL, STANCE_API_KEY)  # no constructor I/O; 3s timeout
result = stance.set("005930", 0.10, reason="breakout")
if result["admit"] not in {"accepted", "clamped"}:
    raise RuntimeError(result["reason"] or result["admit"])
```

The first declaration recovers `last_seq`. Recovery failures are explicit, never reset to zero.

## JavaScript / TypeScript

```js
const headers = {
  Authorization: `Bearer ${process.env.STANCE_API_KEY}`,
  "Content-Type": "application/json",
};
const base = process.env.STANCE_URL;
const portfolio = await fetch(`${base}/portfolio`, { headers }).then(async r => {
  if (!r.ok) throw new Error(`portfolio ${r.status}: ${await r.text()}`);
  return r.json();
});
const declaration = {
  protocol: "stance/1", seq: portfolio.last_seq + 1,
  kind: "set", symbol: "005930", target_weight: 0.10,
};
const response = await fetch(`${base}/stances`, {
  method: "POST", headers, body: JSON.stringify(declaration),
});
if (!response.ok) throw new Error(`stance ${response.status}: ${await response.text()}`);
const result = await response.json();
```

After a timeout, resend the unchanged `declaration` object.

## Key rotation and API contract

```bash
curl -sS -X POST "$STANCE_URL/keys/rotate" \
  -H "Authorization: Bearer $STANCE_API_KEY"
```

The replacement key appears once and revokes the old key immediately.

- Swagger UI: `$STANCE_URL/docs`
- OpenAPI JSON: `$STANCE_URL/openapi.json`
- market rules: `$STANCE_URL/markets`
- health: `$STANCE_URL/health` — production must report `durable: true`

Fix `400/422`, check credentials on `401/403`, resolve seq conflicts on `409`, and retry
`429` with exponential backoff. Retry `5xx` and network timeouts with the identical body.
