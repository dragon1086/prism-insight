# PRISM-BTC 데모 실주문 어댑터 + 텔레그램 현황 — 구현 스펙 (2026-06-13)

> Rocky 지시: 섀도우와 **병행** 가동 (섀도우=이론값, 데모=거래소 실체결값 → 괴리 측정).
> 운영서버(이 맥)에서 바로 데모로 돌게. 텔레그램 채널에 정기 현황. 대시보드는 나중.

## 0. 불변 원칙 (절대 위반 금지)
- **shadow.py 를 수정하지 않는다.** 데모는 별도 클래스(live/demo.py).
- core/engine 결정 로직은 그대로 재사용 (진입/청산 판단 동일). 다른 건 "집행"뿐.
- 데모 키는 출금 불가 (이미 확인). 그래도 코드에 출금/이체 호출 절대 금지.
- 모든 네트워크/주문 실패는 흡수 → btc_events(mode='demo') 기록, 데몬 비중단.
- 데이터는 mode='demo' 로 기존 btc_* 테이블에 (섀도우와 완전 독립 추적).

## 1. pybit 데모 접속 (검증 완료)
```python
from pybit.unified_trading import HTTP
sess = HTTP(demo=True, api_key=KEY, api_secret=SECRET)   # ★ demo=True (testnet 아님!)
```
- 잔고: `sess.get_wallet_balance(accountType="UNIFIED")` → list[0].totalEquity / coin[USDT].walletBalance
- 키: .env `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET` (존재 확인됨, $10k USDT 충전됨)
- category="linear", symbol="BTCUSDT", positionIdx=0 (단방향 모드 가정 — 첫 진입 전 set 확인)

## 2. DemoAdapter 설계 (live/demo.py)

ShadowAdapter 와 **동일 시그니처**: `__init__(root_conn, tf_data, funding_times, funding_rates, mode="demo")`,
`process_bar(bar_time, bar, new_4h_confirmed, cur_4h_ns)`.

**차이점은 "체결의 출처"뿐:**
| 항목 | shadow | demo |
|---|---|---|
| 진입 체결 | 다음 봉 [low,high] 안에 limit 들면 가상 체결 | 거래소 post-only 지정가 주문 → 다음 tick 에 체결조회 |
| SL | 봉 high/low 가 sl 터치 시 가상 청산 | 거래소 네이티브 stop-market 주문이 처리 |
| TP1 부분익절 | _book_leg 가상 | 거래소 reduce-only 지정가(maker) |
| 트레일 | sl_price 갱신 | 거래소 stop 주문 amend |
| equity/포지션 | 로컬 누산기 | **거래소가 진실 (get_wallet_balance/get_positions 복원)** |

**핵심 흐름 (process_bar):**
1. 거래소에서 현재 포지션·잔고·미체결 주문 동기화 (reconcile) → btc_positions(demo)/btc_equity_curve(demo) 갱신
2. 직전 tick 의 pending 진입주문 체결 여부 확인 (get_open_orders/get_positions diff)
   - 체결됨 → 진입 시 SL stop-market(reduce-only) + TP1 limit(reduce-only) 동반 주문, btc_positions 기록, log_event "fill"
   - 미체결 + 만료(ENTRY_ORDER_EXPIRY_BARS) → cancel_order, pending 해제
3. 보유 포지션에 대해 core 의 evaluate_exits/check_exit_signal 평가 → Action:
   - ClosePosition/signal_exit → reduce-only 시장가 청산
   - BookPartial(TP1) → 이미 step2 에서 TP 지정가 걸어둠 (체결되면 reconcile 가 잡음). 신호기반 reduce 는 시장가
   - UpdateStop/ActivateBETrail(트레일) → 기존 stop 주문 amend_order(triggerPrice 갱신)
   - ForceReduce → reduce-only 시장가 (청산 임박 방어)
4. 신규 진입 신호 (4h 확정 + 하드캡 + 쿨다운, shadow 와 동일 게이트) → core evaluate_entry 로 OpenIntent →
   set_leverage → post-only 지정가 진입주문 → pending 을 btc_meta(demo) 에 기록
5. log_event heartbeat(demo)

**reconcile 우선 원칙**: 종결 트레이드(btc_trading_history,demo)는 거래소 체결내역(get_executions 또는 closed_pnl)
기준으로 기록. 로컬 추정과 충돌 시 거래소가 진실. r_multiple 은 net_pnl/initial_risk 로 역산
(initial_risk 는 진입 시 btc_meta 에 저장).

**주문 헬퍼** (실패 흡수 + 재시도 1회): _place_limit_postonly / _place_stop_market /
_place_reduce_limit / _market_reduce / _amend_stop / _cancel / _sync_state.

## 3. runner.py 수정
- tick(mode) 에서 mode=="demo" 면 DemoAdapter, 아니면 ShadowAdapter (한 줄 분기).
- 데모는 키 없으면 즉시 에러 이벤트 + 스킵 (섀도우 영향 0).
- 오버라이드 적용/저널/버전추적 로직은 mode 무관하게 동일.

## 4. 텔레그램 현황 리포터 (live/telegram_reporter.py)
- 인프라: 루트 `tracking/telegram.py` 의 TelegramSender (python-telegram-bot>=20). 없으면 직접 Bot.
- .env: TELEGRAM_BOT_TOKEN + **BTC 전용 채널 BTC_TELEGRAM_CHANNEL_ID** (없으면 TELEGRAM_CHANNEL_ID 폴백).
  Rocky 가 채널ID 줄 때까지는 .env 미설정 → 전송 스킵 + 로그만 (크래시 금지).
- 내용 (Markdown, 한국어, 트레이더 친화):
  - 헤더: 모드(DEMO), 현재시각, 가동일수
  - 자산: 현재 equity, 시작 대비 수익률%, 최고점 대비 DD%
  - 보유 포지션: 방향/진입가/현재가/미실현 R/레버 (없으면 "관망 중")
  - 최근 종결 트레이드 3건: 방향/R/사유
  - 누적: 트레이드수/승률/PF/평균R (mode='demo')
  - 신호: 마지막 4h 평가 (btc_signal_log) score/추세강도
  - 푸터: 섀도우 대비 한 줄 (있으면)
- CLI: `python -m live.telegram_reporter --mode demo [--channel ID]`

## 5. 운영 배치 (LaunchAgent)
- 신규 `com.prism.btc-demo` : 매시 02/32분 (섀도우 01/31 과 1분 시차 — DB 경합 회피),
  `python -m live.runner --once --mode demo`. 섀도우 LaunchAgent 는 그대로 둠 (병행).
- 신규 `com.prism.btc-telegram` : 정기 현황 — 우선 4시간마다 (0 */4 * * * 대신 StartCalendarInterval
  6회), `python -m live.telegram_reporter --mode demo`. 빈도는 나중 조정.
- 모든 plist 는 crontab 금지 규칙 준수 (LaunchAgent --once). 로그 /tmp/btc_demo.log, /tmp/btc_telegram.log.

## 6. 테스트 (tests/test_demo.py)
- pybit HTTP 를 가짜 거래소(FakeExchange)로 모킹 — 네트워크 0.
- 검증: OpenIntent→post-only 주문 발행 / 체결 감지→SL+TP 동반주문 / reconcile 가 거래소 포지션을
  btc_positions(demo) 에 반영 / 신호청산→시장가 reduce / 출금·이체 호출 절대 없음(assert) /
  키 없을 때 graceful 스킵 / 텔레그램 미설정 시 스킵.
- 기존 221 + 신규. shadow.py 바이트 불변 확인.

## 7. 안전 점검 리스트 (구현 후 실거래소 1회)
- demo=True 로 $10k 인식 / set_leverage 동작 / 아주 작은 테스트 주문 1건 발행·취소 →
  btc_events(demo) 에 기록 확인 → 즉시 취소. (실제 포지션 잡지 말 것, reconcile 경로만 확인)
