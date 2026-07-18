# 01. 현재 아키텍처 분석

> 2026-07-06 작성 (당시 main #411 기준) → **2026-07-13 main(#432, `7e9c94eb`) 기준 전면 재검증.**
> 재검증에서 확인된 주요 변화: KR 중복 SELL 가드 이식 완료, 루프 3종(구 loop_a/b/c)의
> 리네임과 크로스 프로세스 owner_lock 도입, US 코드의 `prism-us/` 패키지 이동,
> market-pulse 레짐 정책 도입. 상세는 각 절의 앵커와 §6 참고.

## 1. 실제 주문 경로 (검증 완료)

이슈 #412가 서술한 경로는 정확하다:

```
Buy/Sell Agent (LLM 판단만)
  → StockTrackingAgent / EnhancedStockTrackingAgent  (원장 + 실주문 + 알림 + publish)
  → AsyncTradingContext → DomesticStockTrading       (KIS adapter)
  → trading/kis_auth._url_fetch() → KIS REST API
  → Redis Streams / GCP Pub/Sub publish (optional, non-critical)
```

단, **2026-07 현재 주문 진입점은 batch 에이전트만이 아니다.** 실주문 진입점은
총 9곳 (설계 최초 작성 시점의 "4곳" 전제는 폐기). 컨텍스트 클래스는 KR
`AsyncTradingContext` / US `AsyncUSTradingContext`로 **문자열이 다르며**, #9는
컨텍스트 없이 `USStockTrading`을 직접 쓴다 — grep 기반 검사 시 세 패턴 모두 필요:

| # | 경로 | 위치 | 주문 종류 |
|---|------|------|----------|
| 1 | KR batch 매도 | `stock_tracking_agent.py:1640-1668` | 지정가/분할 매도 |
| 2 | KR batch 매수 | `stock_tracking_agent.py:2008-2011` | 지정가 매수 |
| 3 | KR enhanced 매수 | `stock_tracking_enhanced_agent.py:578-581` | 지정가 매수 |
| 4 | 하드스탑 루프 (구 loop_a) | `tools/hardstop_seller.py:260-263,417` | 시장가 손절 매도 (KR/US 양시장) |
| 5 | 추세이탈 루프 (구 loop_b) | `tools/trend_exit_seller.py:426-429,638` | 시장가 매도 (KR/US 양시장) |
| 6 | 미체결 추격 루프 (구 loop_c) | `tools/fill_chaser.py:300-303` | **정정/취소** (KR/US, SHADOW 기본) |
| 7 | US batch 매도 | `prism-us/us_stock_tracking_agent.py:2557` | 지정가/예약 매도 |
| 8 | US batch 매수 | `prism-us/us_stock_tracking_agent.py:2974` | 지정가/예약 매수 |
| 9 | US 예약주문 지연 제출 배치 | `prism-us/us_pending_order_batch.py:88,157,164` (204줄) | **큐잉된 예약주문의 실제 제출** |

4~6은 batch와 **별도 cron 프로세스**로 도는 LLM-free 루프다. #9는 KIS 예약주문
API 시간창(10:00~23:20 KST) 밖에서 큐잉된 주문을 10:05 KST cron이 꺼내
`buy_reserved_order()`/`sell_reserved_order()`로 제출하는 **지연 제출(deferred
submission) 경로**다 — "익일 체결 확인"이 아니다. 목표 설계의 ExecutionService
chokepoint는 이 9곳 전부를 감싸야 한다.

각주: `cores/corporate_status.py:87-95`도 `AsyncTradingContext`를 열지만 시세/종목
상태코드 조회 전용(실주문 없음)이다. grep 검사의 제외 목록에 명시하고, 장기적으로는
MarketDataPort로 이관할 사용처다.

## 2. 핵심 결함: 원장 선커밋 + 주문 fire-and-forget (여전히 유효)

### 매수 (stock_tracking_enhanced_agent.py:574-581)

```python
if decision == "Enter" and buy_score >= min_score and sector_diverse and not _cd_block:
    buy_success = await self.buy_stock(...)          # ① 로컬 원장 먼저 커밋
    if buy_success:
        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_buy_stock(...)  # ② 실주문
        if trade_result['success']:
            logger.info(...)
        else:
            logger.error(...)                        # ③ 실패해도 로그만. 롤백 없음
```

### 매도 (stock_tracking_agent.py:1627-1668)

```python
sell_success = await self.sell_stock(stock, sell_reason)   # ① 원장에서 행 삭제
if sell_success:
    async with AsyncTradingContext(...) as trading:
        trade_result = await trading.async_sell_stock(...)  # ② 실주문
    # 실패 시 역시 로그만
```

### 루프도 같은 패턴 (tools/hardstop_seller.py:405-425)

sim/원장 close를 먼저 확정한 뒤 KIS 주문을 넣는다. 에러 메시지가 구조를 자백한다:
`"KIS sell failed after sim close"` — 주문 실패 시 원장은 이미 닫혀 있고 보상이 없다.

**결과**: KIS 주문이 실패하면 로컬 원장과 실계좌가 조용히 어긋난다.
- 매수: 원장에는 보유 중, 실계좌에는 없음 → 이후 매도 판단이 유령 포지션에 대해 돈다.
- 매도: 원장에서는 삭제됨, 실계좌에는 남음 → 시스템 관리 밖의 실물 포지션 발생.
- 보상(compensation) 로직, ERROR 상태, 재시도, 알림 어느 것도 없다.

추가 문제: **매도는 원장 행을 delete한다.** 상태 이력이 없어 사후 reconciliation이
구조적으로 불가능하다 (무엇이 있었는지 원장이 기억하지 못함).
US도 동일 (`prism-us/us_stock_tracking_agent.py:2303-2315`의 DELETE 3연타).

## 3. God class: StockTrackingAgent 상속 체인 (계속 성장 중)

- `stock_tracking_agent.py` — `StockTrackingAgent` **2,510줄** (07-06 시점 2,297줄)
- `stock_tracking_enhanced_agent.py` — `EnhancedStockTrackingAgent(StockTrackingAgent)` **1,539줄**
- `prism-us/us_stock_tracking_agent.py` — **KR 클래스의 통째 포크 3,600줄** (07-06 시점 ~2,900줄)

일주일 사이 +350줄/+700줄. 포크 비대화는 가속 중이며, 이식성 문제의 실증이다.

한 상속 체인이 담고 있는 책임 12가지 (07-13 기준 계속 유효, 일부는 심화):

1. DB 스키마 관리 (`_create_tables`, 마이그레이션)
2. 계좌 설정 (`_get_trading_accounts`, `_account_scope`)
3. 시세/거래량 조회 (`_get_current_stock_price`, `_get_trading_value_rank_change`)
4. LLM 매수 판단 (`_extract_trading_scenario`, `analyze_report`)
5. LLM 매도 판단 (`_analyze_sell_decision`, `_fallback_sell_decision`)
6. 포트폴리오 정책 — `MAX_SLOTS=10`, `MAX_SAME_SECTOR=3`, 점수 임계값이 **클래스 상수로 하드코딩**
   (+ 07-13: market-pulse 레짐 게이트 `pilot_reexposure_active`, 재진입 cooldown이 추가로 얽힘)
7. 원장 CRUD (`buy_stock`, `sell_stock`, `update_holdings`, watchlist)
8. 매매일지/교훈 (`_create_journal_entry`, `compress_old_journal_entries`)
9. KIS 실주문 (루프 안 inline import로 `AsyncTradingContext` 호출)
10. 시그널 publish (Redis/GCP, try/except non-critical)
11. Telegram/Firebase 알림 (`send_telegram_message`, `_notify_firebase`, 번역 채널)
12. 실행 루프 (`run`, `process_reports`)

`sell_stock`은 07-13 현재 중복 SELL 가드 + exit_kind 분류(churn 가드) + 재진입
cooldown 입력 기록 + 다국어 브로드캐스트 큐잉까지 흡수해 더 무거워졌다 —
Phase 1 순수 함수 추출의 난도가 그만큼 올라갔다.

LLM 출력 계약이 없어 방어적 파싱이 흩어져 있다:
`_normalize_decision`, `_parse_price_value`, `_safe_number_conversion`.

## 4. 이미 잘 되어 있는 것 (건드리지 않거나 승격할 것)

### 4.1 시그널 publish는 이미 약결합
`stock_tracking_agent.py:1680-1704`, enhanced `:591-620` — Redis/GCP publish는
optional·auto-skip·non-critical. 이 패턴을 시스템 전체 규칙(이벤트 버스)으로 승격한다.

### 4.2 KIS adapter 내부 안전장치 (trading/domestic_stock_trading.py, 2,179줄)
- `:225` 전역 `asyncio.Lock`, `:1204` 종목별 lock — 단, **프로세스 내부용**.
  cron으로 도는 별도 프로세스 간에는 무력하다 (루프 쪽 owner_lock은 §4.5).
- 실/모의 TR ID 분기: 매수 `TTTC0012U`/`VTTC0012U`(:383, :517, :667),
  매도 `TTTC0011U`/`VTTC0011U`(:890, :1035), 예약주문 `CTSC0008U`(:771, :1134)
- `:1431` — get_portfolio()의 일시적 빈 응답을 보유 없음으로 확정하지 않는 재확인 가드
  (빈 응답 3회 재시도 + 최초 확인 수량 fallback)

### 4.3 trading/kis_auth.py (1,741줄 — 루트에서 trading/으로 이동)
- `:163-166` real/prod/live vs demo/paper/vps 모드 정규화, 불일치 시 예외
- `account_key = svr:account:product` 단위 credential 바인딩
- 단, 인증 컨텍스트가 전역 가변 상태라 다중 계좌에서 lock으로 보호하는 구조 —
  근본 해결은 lock 추가가 아니라 인증 컨텍스트의 객체 스코프화.

### 4.4 사고에서 나온 회귀 방어 (반드시 보존할 것 — KR 이식은 완료됨)
- **중복 SELL 가드 — 이제 KR/US 양쪽에 존재**:
  - US: `prism-us/us_stock_tracking_agent.py:2242-2261` (`[SELL-GUARD][US]`) +
    **Layer 2 fresh holding re-check** `:2432-2445` (update_holdings의 stale snapshot 선차단)
  - KR: `stock_tracking_agent.py:1300-1327` (`[SELL-GUARD][KR]`) — **07-06 문서의
    "KR에는 아직 없다"는 서술은 무효.** 이식이 main에 반영됐다.
  - 시맨틱: 2026-07-01 MU 사고(하드스탑 루프가 23:50 손절 매도+publish 후, batch가
    stale snapshot으로 23:55 두 번째 SELL publish) 이후, 모든 매도 경로(batch
    update_holdings, hardstop_seller, trend_exit_seller)가 `sell_stock` 단일
    chokepoint를 지나고, fresh WAL snapshot(`conn.commit()` 후 재조회)으로 행 부재 시 abort.
  - 남은 일: 이 가드를 ExecutionService로 이관할 때 시맨틱을 1:1 보존하고,
    동시 2-프로세스 회귀 테스트를 CI에 고정하는 것 (05 문서 L2-1).
- **피라미딩 분할매도 over-sell 방지** (#288 FIX 2, `stock_tracking_agent.py:1575-1668`):
  pass당 보유수량 스냅샷(`pass_total_qty`)에서 이미 주문한 수량을 차감해 분배.
  미체결 지정가가 있을 때 마지막 행이 broker 재조회로 전량 매도하는 버그를 막는다.
  (07-06 문서의 :1465-1497 앵커는 라인 이동)

### 4.5 루프 계층의 안전장치 (07-13 신규 — 설계가 흡수·승격할 선례)

설계 최초 작성 이후 파악된, 루프 3종이 이미 갖춘 것들:

- **크로스 프로세스 owner_lock — 단, 부분적**: `loop_a_position_state` 테이블에
  SQLite `BEGIN IMMEDIATE`로 티커 단위 lock + 만료 시각을 공유하는 것은
  **hardstop(`tools/hardstop_seller.py:200-235`)과 fill_chaser(`tools/fill_chaser.py:165-240`)
  둘뿐이다.** trend_exit는 자체 `loop_b_position_state`/`loop_b_inflight_orders`
  테이블을 쓴다 (`tools/trend_exit_seller.py:200-289`) — 즉 **hardstop↔trend_exit는
  상호 직렬화되지 않으며** (둘 다 SELL 주체인데도), 이 경합은 sell_stock
  fresh-snapshot 가드에만 의존한다. fill_chaser docstring의 "all loops serialise
  on the SAME lock"(:38-39)은 코드 현실과 어긋난 stale 주석이니 믿지 말 것.
  batch 에이전트도 lock 밖이다. 목표 설계의 크로스 프로세스 lock은 loop_a_*/loop_b_*
  테이블을 통합·일반화해 루프 전체 + batch를 포괄해야 한다.
- **SHADOW/LIVE env 게이트**: `HARDSTOP_LIVE`, `TREND_EXIT_LIVE`, `FILL_CHASER_LIVE`
  3종 (기본 SHADOW — 실주문 없이 "WOULD SELL/AMEND" 로그만). 목표 설계의 shadow
  실행 모드가 요구하는 시맨틱의 축소판이 이미 운영 중이다.
- **미체결 주문 관리의 단일 소유자**: `fill_chaser`가 KIS 실시간 미체결 조회
  (KR `get_revisable_orders`(:322) / US `get_unfilled_orders`(:336))를 source of truth로
  정정(추격)/취소를 수행하고 partial fill을 `loop_c_chase_log`로 대사한다.
  BrokerAdapter의 `list_open_orders`/`amend_order`가 흡수할 대상이자 참조 구현.
  ⚠️ 정정/취소 TR wrapper는 라이브 검증 전 (`tasks/loop_c_design.md` 체크리스트).
- **루프 매도 publish 경로**: `sell_broadcast.py`(구 loop_publish)가 루프 매도를
  batch와 같은 Redis/GCP 스트림으로 발행한다. 시맨틱 주의: **publish는 sim 커밋
  기준**(sim = 방송 source of truth)이며 자사 KIS 체결 여부와 무관 — 이벤트 버스
  통합 시 발행 시점 계약을 명시적으로 결정해야 한다 (03 §6).

관련 문서: `tasks/loop_architecture_design.md`, `tasks/mu_double_sell_safety_design.md`.

## 5. 현재 없는 것 (07-13 재평가)

- 주문 접수 이후의 체결/미체결 추적 — **부분 존재로 격상**: fill_chaser가 미체결
  조회·정정·취소를 담당하고, `us_pending_order_batch.py`는 시간창 밖 큐잉 예약주문의
  지연 제출을 담당한다 (`us_pending_orders` 큐 — **order_intents 영속화의 기존 실물
  선례**). 단 둘 다 국지적이며, 주문 수명주기의 영속 기록(order state store)은 없다.
- 로컬 원장 ↔ 실계좌 reconciliation job — 없음 (변동 없음)
- 주문 intent의 영속화 / idempotency 키 — 없음 (변동 없음)
- 크로스 프로세스 lock — **루프 간에는 존재** (§4.5), batch는 미포괄
- shadow 실행 모드 — **루프에는 env 게이트로 존재** (§4.5), 코어 batch의
  `execution_mode` 격리는 없음 (demo 계좌 모드는 있음)

## 6. 07-06 → 07-13 사이 설계에 영향을 준 main 변경 요약

- #422~#423: 루프 grace/portfolio 수정, 루프 매도 다국어 브로드캐스트
- #424: buy-gate (개별 추세 게이트 + 반복 손절 게이트가 매수 프롬프트에)
- #425~#427, #429~#430: market-pulse — O'Neil M 상태기계, batch-rest 정책,
  CORRECTION 후 파일럿 재진입 축소매수/스로틀 (`cores/regime_policy.py` 신설,
  buy 경로에 `pilot_reexposure_active` 게이트 추가)
- #428: loop_a/b/c → hardstop_seller / trend_exit_seller / fill_chaser 리네임
  (구 경로는 deprecation shim), loop_publish.py → sell_broadcast.py
- #431: US 분석 배치 아침/오후 2회로 축소 (cron 스케줄 변경)
- KR 중복 SELL 가드 이식 (`[SELL-GUARD][KR]`)
- US 코드 전체가 `prism-us/` 패키지로 이동 (us_stock_trading.py 어댑터 포크 포함)
