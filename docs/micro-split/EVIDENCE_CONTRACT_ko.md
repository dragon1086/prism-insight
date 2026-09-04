# 초분할 Evidence 계약

## 비교 대상

동일한 candidate/decision/가격 경로에서 다음을 분리합니다.

- **Baseline**: 현행 `100 → 200 → 300%` 독립 행 피라미딩
- **Micro Split**: versioned `10 → 30 → 60 → 100%`, 강세장 최대 300%
- **Broker Projection**: 계정별 정수 주식으로 실행 가능했을 경로

Baseline과 Micro Split을 서로 다른 종목·기간·레짐에서 비교하지 않습니다.

## 최소 저장 필드

- `decision_id`, `campaign_id`, `market`, `symbol_ref`
- `policy_version`, code/config/input hash
- 판단 시각과 as-of
- 이전/새 목표비중, stage reason, 레짐과 레짐 출처
- unit amount snapshot ref, 가격, stop, 1R
- virtual target notional/qty와 정수 주식 projected qty
- execution provenance: `NOT_REQUESTED|QUEUED|SUBMITTED|CONFIRMED|CANCELLED|REJECTED|UNKNOWN`
- forward return, MFE, MAE, 손절·trend-exit·target 결과

## 결정론적 Packet

```bash
python tools/build_micro_split_evidence_packet.py \
  --input logs/prism_events.jsonl \
  --replay-config trading/config/kis_devlp.yaml \
  --market US \
  --output /tmp/micro-split-evidence.json
```

- 원시 JSONL과 KIS 설정은 서버 밖으로 복사하지 않습니다.
- config에서는 USD 단위금액만 읽고 Packet에는 원값·계좌·API 정보를 출력하지 않습니다.
- 같은 입력의 순서가 달라도 `packet_id`가 같아야 합니다.
- 같은 `event_id`는 최신 한 건만 남기고 중복 수를 별도 보고합니다.
- `observed_shadow`와 `candidate_replay`를 절대 합산하지 않습니다.
- replay는 정수주 실행 가능성 projection이며 실제 진입·체결·성과가 아닙니다.
- schema v1과 v2를 섞어 coverage 100%로 간주하지 않습니다.

## 주요 지표

Primary:

- 포트폴리오 MDD
- 계좌 open-risk와 최대 동시 목표 슬롯
- 확정 실현 순손익 또는 동일 비용 가상 순손익

Secondary:

- Profit Factor, 중앙 R, 승률
- 연속 손실의 총 계좌 손실
- 최고 winner 제거 전후 방향
- 큰 승자 포착률과 baseline 대비 수익 절단률
- 목표비중까지 걸린 거래일 수
- 정수 주식 때문에 실행되지 않은 캠페인 비율
- 주문 수·미체결·취소·retry·비용

## 필수 반례

- 10% 정찰 뒤 바로 급등해 baseline보다 크게 덜 번 winner
- 60% 첫 정수 주식 직후 손절한 고가주
- 내부는 300%까지 갔지만 KIS는 1주도 못 산 종목
- 강세장에서 추가한 뒤 레짐이 급변한 종목
- 가격 하락만으로 수량이 늘어날 뻔한 사례
- 동일 종목 재평가·재시작으로 중복 leg가 생길 뻔한 사례

## 승격 금지선

- fill status가 `CONFIRMED`가 아닌데 실현 성과로 사용
- baseline과 policy version이 섞임
- 미래 봉으로 stage threshold 선택
- 극단 winner 한 건을 빼면 개선 방향이 반전
- MDD가 개선돼도 winner removal이 사전 허용치를 초과
- 정수 주식·수수료·슬리피지 제외 시에만 개선
- 내부 원장과 KIS 실행을 하나의 상태로 합침

## 초기 판정 enum

- `CONTINUE_CAPTURE`
- `PREREGISTER_REPLAY`
- `START_INTERNAL_SHADOW`
- `START_EXECUTION_SHADOW_REVIEW`
- `LIMITED_DEMO_REVIEW`
- `LIMITED_LIVE_REVIEW`
- `RETIRE`
