# BTC 오픈소스 트레이딩 프레임워크 검토

> 조사일: 2026-08-31
> 원칙: 아이디어만 clean-room으로 재구현하며 라이선스 미검토 코드를 복사하지 않음

## 우선순위

### 1. Freqtrade

- GitHub 약 53.8k stars, Python crypto bot
- 강점: lookahead-analysis, recursive-analysis, lower-timeframe detail backtest,
  rejected-signal analysis
- 라이선스: GPL-3.0
- 결정: 패키지·코드 도입 금지. 검증 개념만 독자 구현
- 반영: `analysis.bias_audit`로 full-precompute와 prefix-only snapshot 비교,
  startup 40·80·150·300·1000 drift 검사

### 2. NautilusTrader

- GitHub 약 28.2k stars, Rust/Python deterministic event-driven engine
- 강점: backtest/live 동일 strategy component, event sourcing, data catalog,
  streaming/chunked backtest
- 라이선스: LGPL-3.0
- 결정: 새 의존성으로 넣지 않음. 기존 PRISM hexagonal core parity와 action-trace
  검증을 강화하는 참고 대상으로 사용

### 3. Jesse

- GitHub 약 8.4k stars, Python crypto research/backtest/live framework
- 강점: strategy abstraction, route/session configuration, backtest session history
- 라이선스: MIT
- 결정: 직접 의존성은 불필요. `strategy_id`, config/input hash, Evidence Packet과
  실험 manifest에 개념 반영

### 4. hftbacktest

- GitHub 약 4.6k stars, Rust/Python
- 강점: order latency, queue position, partial/no-partial fill, L2/L3 replay,
  backtest/live position-path 비교
- 라이선스: MIT
- 결정: 현재 OHLCV 전략에 엔진 도입은 과함. 실측 send/ack/fill latency와 L2 데이터가
  축적된 뒤 execution-model 보정 단계에서 검토

### 5. Hummingbot

- GitHub 약 19.7k stars, Apache-2.0
- 강점: 다거래소 connector, market-making, arbitrage, order-book 전략
- 결정: Bybit 단일 추세 시스템인 현재는 중복이 큼. 멀티거래소·market-making lane을
  열 때 재검토

### 6. CCXT

- GitHub 약 43.8k stars, MIT
- 강점: 100개 이상 거래소 통합 API
- 결정: 현재 pybit Bybit adapter가 live/demo 의미론에 맞게 검증돼 있으므로 교체하지 않음.
  두 번째 거래소가 필요할 때 connector 경계에서 검토

### 7. vectorbt

- GitHub 약 8.9k stars
- 강점: 대규모 vectorized 아이디어 탐색
- 라이선스: Apache-2.0 + Commons Clause
- 결정: 의존성 도입 금지. 빠른 스윕은 과최적화 위험도 커서 현재 deterministic
  trial registry와 맞지 않음

### 8. QuantConnect Lean

- GitHub 약 21.4k stars, Apache-2.0
- 강점: fee/fill/slippage model과 다자산 event-driven backtest
- 결정: 범용 엔진 교체는 현재 PRISM-BTC의 backtest/live core parity를 깨뜨릴 수 있음.
  독립 교차검증기로만 검토

## 이번 반영

`prism-btc/analysis/bias_audit.py`를 추가했습니다.

- 전체 데이터 indicator precompute와 판단 시점 prefix-only 재계산 비교
- startup candle 40·80·150·300·1000 비교
- 추세·캔들 위치·MA·ATR·alignment score 차이 검출
- lookahead 차이는 즉시 실패
- 운영 필수 startup 300봉에서 상대 오차 0.01%를 넘으면 실패
- 매매·DB·설정 변경 없음

2020~2026 BTC 데이터 12개 시점 결과:

- lookahead bias 0
- startup 40봉 drift 72건
- startup 80봉 drift 61건
- startup 150·300·1000봉 drift 0건
- 최소 안정 startup 150봉

## 다음 후보

1. Nautilus식 backtest/live action trace parity audit
2. 실측 주문 send/ack/fill-confirm latency CAPTURE — v1 구현 완료
3. 5분봉이 충분히 쌓인 구간의 detail-timeframe fill sensitivity
4. strategy experiment manifest와 trial registry

어느 항목도 자동으로 SHADOW·DEMO·LIVE로 승격하지 않습니다.
