# 01. 현재 아키텍처 분석 (2026-07-06 main 기준)

## 1. 실제 주문 경로 (검증 완료)

이슈 #412가 서술한 경로는 정확하다:

```
Buy/Sell Agent (LLM 판단만)
  → StockTrackingAgent / EnhancedStockTrackingAgent  (원장 + 실주문 + 알림 + publish)
  → AsyncTradingContext → DomesticStockTrading       (KIS adapter)
  → kis_auth._url_fetch() → KIS REST API
  → Redis Streams / GCP Pub/Sub publish (optional, non-critical)
```

## 2. 핵심 결함: 원장 선커밋 + 주문 fire-and-forget

### 매수 (stock_tracking_enhanced_agent.py:531-544)

```python
if decision == "Enter" and buy_score >= min_score and sector_diverse:
    buy_success = await self.buy_stock(...)          # ① 로컬 원장 먼저 커밋
    if buy_success:
        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_buy_stock(...)  # ② 실주문
        if trade_result['success']:
            logger.info(...)
        else:
            logger.error(...)                        # ③ 실패해도 로그만. 롤백 없음
```

### 매도 (stock_tracking_agent.py:1461-1500)

```python
sell_success = await self.sell_stock(stock, sell_reason)   # ① 원장에서 행 삭제
if sell_success:
    async with AsyncTradingContext(...) as trading:
        trade_result = await trading.async_sell_stock(...)  # ② 실주문
    # 실패 시 역시 로그만
```

**결과**: KIS 주문이 실패하면 로컬 원장과 실계좌가 조용히 어긋난다.
- 매수: 원장에는 보유 중, 실계좌에는 없음 → 이후 매도 판단이 유령 포지션에 대해 돈다.
- 매도: 원장에서는 삭제됨, 실계좌에는 남음 → 시스템 관리 밖의 실물 포지션 발생.
- 보상(compensation) 로직, ERROR 상태, 재시도, 알림 어느 것도 없다.

추가 문제: **매도는 원장 행을 delete한다.** 상태 이력이 없어 사후 reconciliation이
구조적으로 불가능하다 (무엇이 있었는지 원장이 기억하지 못함).

## 3. God class: StockTrackingAgent 상속 체인

- `stock_tracking_agent.py` — `StockTrackingAgent` 2,297줄
- `stock_tracking_enhanced_agent.py` — `EnhancedStockTrackingAgent(StockTrackingAgent)` 1,497줄
- `prism-us/us_stock_tracking_agent.py` — **KR 클래스의 통째 포크** 약 2,900줄+

한 상속 체인이 담고 있는 책임 12가지:

1. DB 스키마 관리 (`_create_tables`, 마이그레이션)
2. 계좌 설정 (`_get_trading_accounts`, `_account_scope`)
3. 시세/거래량 조회 (`_get_current_stock_price`, `_get_trading_value_rank_change`)
4. LLM 매수 판단 (`_extract_trading_scenario`, `analyze_report`)
5. LLM 매도 판단 (`_analyze_sell_decision`, `_fallback_sell_decision`)
6. 포트폴리오 정책 — `MAX_SLOTS=10`, `MAX_SAME_SECTOR=3`, 점수 임계값이 **클래스 상수로 하드코딩**
7. 원장 CRUD (`buy_stock`, `sell_stock`, `update_holdings`, watchlist)
8. 매매일지/교훈 (`_create_journal_entry`, `compress_old_journal_entries`)
9. KIS 실주문 (루프 안 inline import로 `AsyncTradingContext` 호출)
10. 시그널 publish (Redis/GCP, try/except non-critical)
11. Telegram/Firebase 알림 (`send_telegram_message`, `_notify_firebase`, 번역 채널)
12. 실행 루프 (`run`, `process_reports`)

LLM 출력 계약이 없어 방어적 파싱이 흩어져 있다:
`_normalize_decision`, `_parse_price_value`, `_safe_number_conversion`.

**이식성이 깨졌다는 실증**: 미국 시장 진출 시 이 클래스를 복사해 prism-us 포크를
만들었다. 다음 프로젝트도 같은 방식이 될 것.

## 4. 이미 잘 되어 있는 것 (건드리지 않거나 승격할 것)

### 4.1 시그널 publish는 이미 약결합
`stock_tracking_agent.py:1505` 부근, enhanced:548 부근 — Redis/GCP publish는
optional·auto-skip·non-critical. 이 패턴을 시스템 전체 규칙(이벤트 버스)으로 승격한다.

### 4.2 KIS adapter 내부 안전장치 (trading/domestic_stock_trading.py, 2,179줄)
- `:225` 전역 `asyncio.Lock`, `:1204` 종목별 lock — 단, **프로세스 내부용**.
  cron으로 도는 별도 프로세스 간에는 무력하다.
- 실/모의 TR ID 분기: 매수 `TTTC0012U`/`VTTC0012U`(:383), 매도 `TTTC0011U`/`VTTC0011U`(:890),
  예약주문 `CTSC0008U`(:771, :1134)
- `:1431` — get_portfolio()의 일시적 빈 응답을 보유 없음으로 확정하지 않는 재확인 가드

### 4.3 kis_auth.py (1,741줄)
- `:163-166` real/prod/live vs demo/paper/vps 모드 정규화, 불일치 시 예외
- `:220` `account_key = svr:account:product` 단위 credential 바인딩
- 단, 인증 컨텍스트가 전역 가변 상태라 다중 계좌에서 lock으로 보호하는 구조 —
  근본 해결은 lock 추가가 아니라 인증 컨텍스트의 객체 스코프화.

### 4.4 사고에서 나온 회귀 방어 (반드시 보존·이식할 것)
- **중복 SELL 가드** (`prism-us/us_stock_tracking_agent.py:2050-2075`):
  2026-07-01 MU 사고(loop_a가 23:50 손절 매도+publish 후, batch가 stale snapshot으로
  23:55 두 번째 SELL publish) 이후, 모든 매도 경로가 `sell_stock` 단일 chokepoint를
  지나고, fresh WAL snapshot(`conn.commit()` 후 재조회)으로 행 부재 시 abort.
  **KR 쪽에는 아직 이 가드가 없다.**
- **피라미딩 분할매도 over-sell 방지** (#288 FIX 2, `stock_tracking_agent.py:1465-1497`):
  pass당 보유수량 스냅샷에서 이미 주문한 수량을 차감해 분배. 미체결 지정가가
  있을 때 마지막 행이 broker 재조회로 전량 매도하는 버그를 막는다.

## 5. 현재 없는 것

- 주문 접수 이후의 체결/미체결 추적 (예약주문은 `us_pending_order_batch.py`가 부분적으로 담당)
- 로컬 원장 ↔ 실계좌 reconciliation job
- 주문 intent의 영속화 / idempotency 키
- 크로스 프로세스 lock
- shadow 실행 모드 (demo 계좌 모드는 있음)
