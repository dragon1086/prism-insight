# BTC 매매 시스템 이미지 설계도

기준일: 2026-10-05 · 로컬 소스: `089e1ff93b4b1d0fba36459e8f8d26dedf457cb6`

최근 개편된 **Bybit DEMO MAIN LLM 시나리오**를 설명합니다. 과거 MA 기반 MAIN/SWING 전략과 구분합니다. 문서화 범위는 로드맵 M3이며, 주문·위험·배치·전략 변경이나 배포를 수행하지 않습니다. 서버 현재 상태를 직접 조회한 운영 감사는 아닙니다.

## 이미지 설계도

### 1. 전체 구조·배치·시장 입력
![전체 구조·배치·시장 입력](btc-blueprint-20261005/01-architecture.png)

### 2. 판단·상태전이·위험예산
![판단·상태전이·위험예산](btc-blueprint-20261005/02-decisions-risk.png)

### 3. 주문·부분익절·트레일링스탑
![주문·부분익절·트레일링스탑](btc-blueprint-20261005/03-orders-stops.png)

### 4. 정산·복구·공지·검증
![정산·복구·공지·검증](btc-blueprint-20261005/04-evidence-operations.png)

## 해석 시 주의사항

- 2%는 최초 시나리오 순자산 기준의 비용 포함 계획 손실 예산이며, 실제 손실의 절대 상한이 아닙니다.
- 새 트레일링은 LLM의 ADJUST와 결정론 검증을 통한 단조 손절 강화입니다. 과거 고정 MA/ATR 트레일 공식이나 고정 부분익절 비중을 새 전략 규칙으로 읽지 않습니다.
- 5분 판단과 독립 1분 보호, 거래소 native SL은 서로 다른 계층입니다. 1분 보호는 추가 LLM 판단이 아닙니다.
- 로컬 구현, 배포 기록, 실제 전진 체결, 수익성 입증을 구분합니다. 이 이미지 제작으로 로드맵 완료 상태를 변경하지 않습니다.
- 이미지의 작은 글자는 원본 파일에서 확대해 읽으십시오. 코드·검증 계약이 이미지보다 우선합니다.

## 확인한 근거

- `prism-btc/live/scenario_llm.py`: 판단 프롬프트·모델·75초 호출 제한·strict 출력.
- `prism-btc/engine/scenario_snapshot.py`: 진행봉·MA·다중 시간봉 관측.
- `prism-btc/core/llm_scenario.py`: 위험 산식·중단·상태별 행동·추격 한도.
- `prism-btc/live/scenario_runtime.py`: 5분 슬롯·잠금·재조정·영속 의도.
- `prism-btc/live/scenario_runner.py`: 독립 보호 진입점·활성화 경계.
- `prism-btc/live/scenario_execution.py`: 주문·native SL·부분 출구 quota·추격·담보 검증.
- `prism-btc/live/scenario_broker.py`: 실제 계좌·체결·보호·회계 통합.
- `docs/BTC_LLM_SCENARIO_20261003_ko.md`: 전환 승인·배포 당시 스케줄 근거.
- `docs/BTC_DIRECTIONAL_REASSESSMENT_20261005_ko.md`: 양방향·미체결 재평가.
- `docs/BTC_ROUND_PRICE_BUFFER_20261005_ko.md`: 신규 지정가 마디 보정.
- `docs/BTC_POSITION_MESSAGE_CONTRACT_20260915_ko.md`: 공지 계약.
- `docs/BTC_ROADMAP_ko.md`: 단계·증거·승격 제한.

## 제작 방식

내장 image_gen 도구로 생성합니다. 도구에 모델 선택 인자가 없어 GPT Image 2.5 사용을 확약하지 않습니다. 공식 문서는 GPT Image 2.5 Sunburst/Flare를 설명합니다: https://developers.openai.com/api/docs/guides/image-generation

전체 생성 프롬프트는 이미지 폴더의 `generation-prompts.json`, 2장 보완 프롬프트는 `revision-prompt.txt`에 보존합니다. 원본은 각 1672×941 PNG입니다. 4장 모두 직접 시각 검수했으며 2장의 확신도 위험 제한과 순수 보호 강화 예외를 보완했습니다. 작은 글자·개념 요약은 코드의 모든 분기나 장애 사례를 대체하지 않습니다. 매매 코드와 운영 데이터는 변경하지 않습니다.
