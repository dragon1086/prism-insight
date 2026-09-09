# Astra SELL 전환

## 승인과 범위

2026-09-09 사용자가 기존 BUY 전환에 이어 KR/US SELL도 Astra로 전환하도록 승인했다.
대상은 정규 orchestrator의 Codex SELL primary이며, 매매 기준·순서·주문 로직은 변경하지 않는다.
매도 품질이나 수익률에서 Astra가 sol보다 우월하다는 결론으로 해석하지 않는다.

후속 설정은 `PRISM_SELL_CODEX_MODEL=gpt-6-astra`,
`PRISM_SELL_CODEX_EFFORT=high`, `PRISM_SELL_CODEX_TIMEOUT=120`이다.
Fast는 기존 공통 backend 설정을 유지한다. 기존 SELL timeout을 늘리지 않는다.

BUY의 Astra/high/240초·최대3개 병렬 설정은 그대로 두며, SELL은 기존 순차 실행을 유지한다.
기존 sol/high legacy fallback도 유지한다. 기존 `ASTRA_BUY_PREDEPLOYMENT_SAFETY_ko.md`의
SELL sol 유지 설명은 BUY-only 초기 배포 시점의 상태이며 이 후속 전환에서만 달라진다.

## 구현 계약

- `prism_core.codex_config.resolve_sell_codex_settings()`가 독립적인 SELL 설정을 읽는다.
- 미설정 모델은 기존 sol, effort는 모델 기본값, timeout은 기존 shared 값 또는90초다.
- BUY 설정이 SELL로 전파되거나 반대로 전파되지 않는다.
- 공통 immutable 설정·허용 목록·유한 timeout 검증을 재사용한다.
- `BuyCodexSettings` 기존 import는 호환 유지한다.
- KR/US SELL 모두 기존 cancellation-safe `generate_codex_fast_async()`를 사용한다.
- 설정 오류·CLI 실패·JSON 파싱 실패는 기존 fallback을 유지한다. 취소는 fallback 없이 전파한다.
- 로그는 requested model/effort/tier/timeout이며 실제 backend snapshot·청구 tier 확정값이 아니다.

## 전환 절차

1. PR head·CI·독립 리뷰와 서버 clean 상태를 확인한다.
2. 실행 중인 orchestrator가 없을 때 검증 commit을 ff-only 배포한다.
3. 기존 CLI0.153.4와 CODEX_HOME을 그대로 사용해 주문 없는 SELL 점검을 실시한다.
4. 서버 내부에 cron을 백업하고 네 정규 배치에 SELL 세 변수만 명시한다.
5. BUY 변수·CLI 경로·기존 shared timeout·다른 cron 명령과 시간을 바꾸지 않는다.
6. 환경을 다시 파싱해 BUY/Sell resolver와 연결을 확인한다.
7. 다음 정상 배치에서 SELL 요청모델·effort·시간·fallback을 확인한다. 배치를 중복 실행하지 않는다.

모델만 바꿨다고 기존 timeout 안에 모든 운영 요청이 완료된다는 보장은 없다.
timeout은 실제 로그에 따라 별도로 검토하며 자동 확대하지 않는다.

## 롤백

네 배치의 SELL override를 제거하면 기존 sol/implicit effort/shared timeout 경로로 돌아간다.
BUY 설정과 배포된 안전 보강을 불필요하게 되돌리지 않는다. 원장·주문·체결 기록을 복원하거나
삭제하지 않는다. 설정 백업에는 비밀값이 포함될 수 있으므로 서버 안에만 보관한다.

## 검증 한계

지금까지의 합성 SELL 사례에서는 sol과 Astra의 최종 판단이 같았다. 애매한 실제 과거 매도
사례와 장기 운영 성과를 충분히 비교하지 않았으므로 우위 판정은 미확정이다.
기술적인 전환 가능성·기존 규칙 보존·fallback·취소 검증과 투자 성과 검증을 구분한다.
