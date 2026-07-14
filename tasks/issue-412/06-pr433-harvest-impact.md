# 06 — PR #433 하베스트가 #412에 주는 영향 (2026-07-15)

> 외부 기여 PR #433(@tkgo11, 91파일/+12,876·-1,724, fork main→main, "원하는 만큼만 반영하세요")
> 를 다각도 리뷰 후 **안전한 격리 조각만 4개 PR로 분리 반영**하고, 주문 생명주기 아이디어는
> 여기(#412)로 흡수하기로 함. 이 노트는 그 결정과 #412 문서에 필요한 후속 갱신을 기록한다.

## 반영한 것 (열린 PR — main 미머지, Rocky 리뷰 대기)
- **#442** `fix(auth)` — OAuth 토큰 원자적 저장(mkstemp 0600+fsync+os.replace). #412 무관.
- **#443** `fix(messaging)` — redis/pubsub/health `asyncio.to_thread` 오프로드. 원본 hunk의
  `__init__` asyncio.Lock 크로스루프 취약점을 **지연 생성(getattr)으로 하드닝**. #412 무관.
- **#444** `fix(trading)` — `_resolve_sell_quantity`가 잘못된/0/음수 명시 수량에 **전량(holding)
  fallback → 0 반환 + 호출부 7곳(US 3·KR 4) 가드**. `None→전량`은 보존. ★ **#412 Phase 1 대상 파일**
  (`prism-us/trading/us_stock_trading.py`, `trading/domestic_stock_trading.py`)을 건드림.
- **#445** `fix(btc)` — `jeoningu_price_fetcher.get_current_price` mock→None + 호출부 가드
  (평가 스킵 / 매도·매수 defer / 대시보드 buy_price fallback). 포지션 스위치 반쪽매도 BLOCKER 수정.

## DROP 한 것 → #412로 흡수 (fork 직접 병합 안 함)
- `us_pending_order_batch.py` claim/lease 상태기계 + 운영 DB 라이브 `ALTER TABLE` DDL → Phase 3(OrderIntent 영속화)에서 우리가 라이브 대조 테스트와 함께.
- `has_open_inflight` inflight-TTL(OPEN+SHADOW→OPEN+TTL) → **기존 브랜치 `feature/loop-a-inflight-ttl`와 중복/충돌**. 그 브랜치에서 처리.
- dedup 변경 → `feat/portfolio-dedup`와 중복.
- 신규 `prism-us/trading/order_submission.py` 모듈 → **#412 Phase 2(ExecutionService chokepoint) 결정을 외부인이 선점**. 우리 설계로 대체.
- `us_stock_trading.py`의 `OrderOutcomeUnknown`/`unknown_outcome`/`retry_safe` → 리뷰 결과 `asyncio.wait_for` 타임아웃에 삼켜져 **라이브 US 경로에서 무력**하고 소비자 0개. Phase 2/4에서 제대로 설계.
- 프론트엔드 lockfile/ .tsx / CI / youtube 테스트 통삭제 등 노이즈 전량.

## #412 문서에 필요한 후속 갱신 (★ A~D 머지 후 실행)
1. **01-current-state.md** — 실주문/매도 수량 처리 현행 서술 갱신:
   - `_resolve_sell_quantity`의 "잘못된 수량 → 전량청산" 위험이 #444로 **제거됨**(이제 거부+WARNING).
     현행 상태 스냅샷을 #444 반영 후로 재베이스라인.
   - BTC(jeoningu) 시세는 더 이상 mock fabricate 안 함(#445) — 시뮬 성과 서술 갱신.
2. **04-migration-plan.md — Phase 1(순수함수 추출)**:
   - 분할매도계산 순수함수화 시, #444가 이미 넣은 `_resolve_sell_quantity` + 7개 호출부 `<=0` 가드를
     **prism_core로 그대로 이관**(재구현 금지, 회귀 방지). 가드의 실패-dict 계약(US 'ticker' / KR 'stock_code')도 보존.
   - 시간대판정/파싱 추출은 영향 없음.
3. **Phase 6(포크 흡수)** — #433 자체가 KR/US 포크 드리프트의 실피해(오늘 #438 텔레그램 버그와 동류)를
   외부에서 또 드러냄. inert US OrderOutcomeUnknown이 그 증거. Phase 6 우선순위 근거로 추가.

## 진입점 재확인 (변화 없음)
실주문 진입점 9곳(#412 02§) 불변. #444는 그중 매도 계산 경로에 방어 가드만 추가(구조 불변)이라
Phase 1 리베이스는 trivial. `order_submission.py`를 DROP했으므로 Phase 2 chokepoint 설계 공간은 그대로 비어 있음.

## 다음
A~D 머지되면 위 1·2 갱신 → Phase 1 착수(문서앵커 최신화 완료 상태).
