# BTC 전략 근거 수집과 실험 계약

> 상태: decision CAPTURE v1
> 거래 영향: 없음
> 원장: `stock_tracking_db.sqlite.btc_decision_log`
> 시장 데이터: `prism-btc/state/btc_market.db`

## 목적

PRISM-BTC가 왜 진입·관망·기각했는지 판단 당시 입력으로 재현하고, 이후 여러 전략을
같은 시장 데이터와 비용 조건으로 비교할 수 있게 합니다. CAPTURE는 기존 신호·점수·주문·
청산을 변경하지 않습니다.

## Decision 원장

4시간 확정 판단마다 stable `decision_id` 한 건을 기록합니다.

- 계보: schema, strategy ID, code version, config hash, input hash
- 시장: alignment score와 30m·1h·4h·12h·1d·1w trend/candle position/trend strength
- 상태: equity, peak, drawdown, pending order, 실제 포지션 수, dust 포지션 수
- 신호: side, strength, 구조화된 reason code
- 최종 진입 평가: accepted/rejected와 hardcap·cooldown·pyramid·sizing reason code
- 주문 의도: qty, leverage, stop, liquidation, initial risk의 안전한 수치만

API key, secret, token, 주문 응답 원문은 기록하지 않습니다. 기존 `btc_signal_log`는
텔레그램 near-miss와 하위 호환을 위해 유지합니다.

## Evidence Packet

배포 이후 decision 원장과 confirmed 30분봉을 연결해 다음 label을 만듭니다.

- 30분, 3시간, 6시간, 24시간, 7일 forward return
- 같은 기간 MFE·MAE
- mode·strategy·horizon coverage
- 비용 계약과 자동 SHADOW/LIVE 금지선

```bash
cd /root/prism-insight/prism-btc
PYTHONPATH= python -m analysis.strategy_evidence_packet \
  --root-db ../stock_tracking_db.sqlite \
  --market-db state/btc_market.db \
  --mode demo \
  --output ../.omx/evidence/btc-strategy-evidence.json
```

Packet은 원장과 시장 DB를 수정하지 않습니다. 같은 입력에서는 같은 `packet_id`가 나와야
합니다. 결과가 아직 성숙하지 않은 horizon은 `MISSING`이며 실패로 바꾸지 않습니다.

## 여러 전략 비교 절차

1. 한 문장 가설과 `strategy_id`를 사전등록합니다.
2. entry/exit, timeframe, 비용, stop, sizing, 최대 동시 포지션을 결과를 보기 전에 고정합니다.
3. 기존 `btc_market.db`에서 동일 기간·동일 비용으로 replay합니다.
4. 2020~현재 전체 결과뿐 아니라 시간순 train/OOS와 연도별 부호를 확인합니다.
5. 수익률, PF, MDD, 거래 수, 비용, 최고 승자 제거 결과를 함께 봅니다.
6. 통과한 전략 하나만 주문과 연결되지 않은 SHADOW candidate로 계산합니다.
7. prospective SHADOW와 데모 검토 후에도 사용자의 명시적 승인 없이는 LIVE로 승격하지
   않습니다.

모든 신규 전략은 replay 전에 lookahead·recursive audit을 통과해야 합니다.

```bash
cd /root/prism-insight/prism-btc
PYTHONPATH= python -m analysis.bias_audit \
  --market-db state/btc_market.db \
  --samples 12 \
  --startup-sizes 40,80,150,300,1000 \
  --required-startup-size 300
```

오픈소스 참고 대상과 라이선스 결정은
[`BTC_OPEN_SOURCE_RESEARCH_ko.md`](BTC_OPEN_SOURCE_RESEARCH_ko.md)를 따릅니다.

실측 주문 지연 원장과 execution Evidence Packet은
[`BTC_EXECUTION_LATENCY_ko.md`](BTC_EXECUTION_LATENCY_ko.md)를 따릅니다.

파라미터나 규칙을 바꾸면 새로운 strategy version과 새로운 trial입니다. 실패한 실험도
시도 횟수에서 삭제하지 않습니다.

## 해석 제한

- 현재 decision CAPTURE는 `main_trend_v1`의 shadow/demo 판단부터 시작합니다.
- 배포 이전 `btc_signal_log`는 snapshot·config hash가 없으므로 새 원장으로 추정 backfill하지
  않습니다.
- 관찰된 forward return은 후보 label이며 전략 전체의 포트폴리오 성과가 아닙니다.
- 대안 전략의 우월성은 동일 비용 event-driven replay를 통과해야 합니다.
