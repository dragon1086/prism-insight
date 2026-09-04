# BTC QuantMind 선별 도입 기록

> 도입일: 2026-09-04
> 범위: PRISM-BTC 연구·관측 계층
> 주문 영향: 없음

## 결론

QuantMind 전체 플랫폼이나 자동 팩터 생성기는 도입하지 않는다. PRISM-BTC에 실제로
부족했던 아래 두 가지 개념만 독립 구현했다.

1. 작은 고정 OHLCV 팩터 묶음을 모든 4시간 진입 판단과 함께 저장한다.
2. 팩터 검증은 시간순 train/validation/test와 label horizon·execution lag만큼의
   embargo를 강제한다.

QuantMind는 AGPL-3.0 프로젝트이므로 소스 코드는 복사하지 않았다. 공개 문서에서 확인한
연구 개념만 PRISM의 기존 pandas·SQLite 구조에 맞춰 clean-room 방식으로 구현했다.

- 원 프로젝트: https://github.com/qusong0627/QuantMind
- 라이선스: https://github.com/qusong0627/QuantMind/blob/main/LICENSE

## 도입한 관측값

각 1h·4h·1d 확정봉에서 10·21개 창을 사용한다.

- 로그가격 선형추세의 `R²`
- 로그수익률 변동성
- 최근 범위 안 종가 위치(RSV)
- 가격·거래량 상관계수

`research.market_factors.build_factor_snapshot()`은 기존
`backtest.engine._get_tf_slice()`를 재사용하므로 평가 시점에 아직 닫히지 않은 상위
시간대 봉은 포함하지 않는다. 결과는 `btc_decision_log.market_snapshot`의
`ohlcv_factors`에 schema v2로 저장한다.

## 검증 계약

`research.causal_validation.purged_chronological_splits()`은 다음 순서를 고정한다.

1. train
2. `label_horizon + execution_lag`만큼 embargo
3. validation
4. 같은 embargo
5. test

`analysis.factor_evidence`는 저장된 판단과 이후 4시간봉 수익률을 연결해 각 구간의
상관 방향을 출력한다. 표본이 한 세트를 채우지 못하면 `insufficient`이며,
`promotion_allowed`는 항상 `false`다.

```bash
cd /root/prism-insight/prism-btc
python3 -m analysis.factor_evidence --mode demo
```

## 의도적으로 제외한 것

- Qlib·RD-Agent 및 LLM 기반 자동 팩터 생성
- 현재 상위 코인만 과거에도 존재했다고 가정하는 universe 백테스트
- 이 팩터를 이용한 즉시 진입 차단·가중치·사이징 변경
- Binance 전용 구현과 PRISM의 Bybit 집행 경로 결합

실제 전략 변경은 충분한 전진 표본에서 validation과 test의 방향이 반복되고, 비용 포함
성과와 MDD까지 별도 백테스트한 뒤 작은 SHADOW 실험으로만 제안한다.
