# BTC 주문 지연 CAPTURE 계약

> 상태: CAPTURE v1
> 적용 대상: Bybit demo/live 주문
> 거래 영향: 없음
> 원장: `btc_execution_samples`

## 수집 단계

### SUBMIT_TO_ACK

클라이언트가 Bybit REST 호출을 시작한 monotonic 시각부터 성공·실패 응답을 받을 때까지의
왕복 시간입니다.

- entry post-only
- native stop-market
- take-profit reduce-only
- market reduce
- stop amend
- order cancel

성공 여부, retry 횟수, retCode, 안전한 주문 속성만 기록합니다.

### ACK_TO_RECONCILE

진입 주문 ACK 이후 다음 reconcile에서 미체결 주문 소멸과 포지션 출현·증가를 함께
확인할 때까지의 시간입니다. 현재 runner가 10분/30분 cadence이므로 **실제 거래소 체결
시간이 아니라 체결 확인 상한**입니다. 실제 fill latency라고 부르지 않습니다.

## 비밀정보

- 원문 order ID는 SHA-256 24자리 ref로만 저장
- API key, secret, token, 응답 payload, retMsg 저장 금지
- side, order type, qty, price, trigger, reduce-only만 허용

## Evidence Packet

```bash
cd /root/prism-insight/prism-btc
PYTHONPATH= python -m analysis.execution_latency_packet \
  --root-db ../stock_tracking_db.sqlite \
  --mode demo \
  --output ../.omx/evidence/btc-execution-latency.json
```

Packet은 mode·operation·phase별 다음 값을 계산합니다.

- n, p50, p90, p95, p99, max
- 성공률, retry율, retCode 분포
- 자동 SHADOW/LIVE 금지선

## 변경 검토 기준

ACK와 fill-confirm 표본이 각각 최소 30건 이상이고 여러 주간 p95가 안정되기 전에는
backtest latency/slippage 모델을 바꾸지 않습니다. 그 이후에도 기존 비용 모델과 새 모델을
같은 기간에 replay하고, 실제 demo position path와 backtest position path의 차이가 줄어드는지
확인해야 합니다. 사용자 승인 없이 LIVE 체결 모델을 변경하지 않습니다.
