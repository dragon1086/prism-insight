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

## 3일 데모 ACK probe

자연 주문만으로 ACK 표본을 모으는 데 오래 걸릴 때는 `live.ack_probe`를 별도로
활성화할 수 있습니다. 이 probe는 Bybit demo에서만 동작하며 전략 주문과 구분되는
`probe_submit`, `probe_cancel` operation으로 기록합니다.

```bash
cd /root/prism-insight/prism-btc
PYTHONPATH= /root/.pyenv/shims/python -m live.ack_probe start \
  --target-cycles 36 --duration-hours 72
PYTHONPATH= /root/.pyenv/shims/python -m live.ack_probe run
PYTHONPATH= /root/.pyenv/shims/python -m live.ack_probe status
```

권장 cron은 2시간 간격입니다. 한 번의 clean cycle은 주문 제출 ACK 1건과 취소 ACK
1건을 만드므로 목표 36회가 끝나면 총 72개 ACK 표본이 생깁니다.

안전 계약은 다음과 같습니다.

- 수량은 BTCUSDT 최소 단위인 0.001 BTC로 고정합니다.
- 매수는 최우선 매수호가/현재가 중 낮은 값보다 5% 아래, 매도는 최우선
  매도호가/현재가 중 높은 값보다 5% 위에 `Limit + PostOnly`로 냅니다.
- 주문 직후 취소하고 포지션과 미체결 주문이 모두 비었는지 재확인합니다.
- 기존 BTC 포지션이나 미체결 주문이 하나라도 있으면 그 회차를 건너뜁니다.
- 계좌 상태 조회가 실패하면 주문을 내지 않습니다.
- 취소 뒤 상태를 확인할 수 없거나 포지션/주문이 남으면 즉시 `halted`로 전환합니다.
- 36 clean cycle 또는 72시간에 도달하면 자동 종료됩니다. cron이 남아 있어도 종료
  상태에서는 주문하지 않습니다.

이 방식은 REST 주문 접수와 취소 왕복 지연만 빠르게 검증합니다. 실제 체결, 수수료,
슬리피지, ACK→fill 지연을 검증하지 않으며 그 표본을 대신할 수 없습니다.
