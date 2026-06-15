# PRISM-INSIGHT v2.14.0 — PRISM-BTC 신규 모듈 · 오닐식 매매엔진 심화 (멀티 MA 국면 · 분산일 · 매도 안전판 · 이벤트 강제청산)

> **Release Date**: 2026-06-15
> **Range**: `v2.13.0`(27140ca) → `main`(0f74427) · 32 PRs (#298–#335)

## 개요

v2.14.0은 세 갈래의 작업을 묶습니다.

1. **PRISM-BTC 신규 모듈 (#324·#325·#326·#327)** ⭐ — 비트코인 무기한 선물(Bybit)을 위한 **완전히 새로운 자동매매 서브시스템**(`prism-btc/`). 전략엔진·6년 백테스터·헥사고날 코어·섀도/데모 실행기·자기개선 오토루프·매매일지/포스트모템 학습·텔레그램 실시간 알림/일일 리포트/이상감지 워치독까지 약 14,000줄 규모. **현재 데모(모의투자) 단계**로, 이번 릴리즈 변경량의 압도적 다수를 차지합니다.

2. **오닐식 매매엔진 심화 (KR/US 주식)** — 단일 20MA 국면판단을 **멀티 이동평균(KR 5/20/60/120, US 10/20/50/200)** 으로 재설계(#309), **분산일(distribution day) 결정론적 카운팅 + 국면 킬스위치**(#320), OpenAI 장애 시 승자를 청산하던 **매도 폴백 전면 교체**(#305), 그리고 상장폐지·공개매수·거래정지 등 **법인이벤트 강제청산(TIER0)** 을 뉴스 기반 자율 판단으로 진화(#330~#335).

3. **학습 파이프라인 부활 · 관측성 · 운영 안정화** — US 매매일지 활성화(#312·#313·#314), 2월부터 멈췄던 직관 생성 복구(#316·#317), US 주간 압축 연결(#321), 국면 분포 로깅+백테스트 도구(#310), Firecrawl Spark 에이전트 전환(#300·#301), 도메스틱 주문창 KST 정합(#304), US 매수신호 누락 버그(#322) 등.

모든 주식 매매 로직 변경은 5인 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 관점으로 검토해 합의 영역만 채택했습니다. BTC 전략 역시 동일 추세추종 철학(타이트 스탑·잦은 리샘플링·승자 보유) 위에 6년 백테스트로 검증했습니다.

---

## 1. PRISM-BTC — 비트코인 선물 자동매매 신규 모듈 (#324·#325·#326·#327) ⭐

이번 릴리즈에서 **단연 가장 큰 작업**입니다. 기존 KR/US 주식 시스템과 분리된 별도 모듈 `prism-btc/`(약 70개 신규 파일, 14,000여 줄, 269+ 테스트)로, 비트코인 무기한 선물(BTCUSDT)을 **모의투자(데모)** 로 자동매매합니다. 주식 시스템에는 일절 영향을 주지 않습니다.

### 1-1. 전략엔진 · 6년 백테스터 (feature/prism-btc-v3 R&D)
- **수집/저장**: `collector/`(Bybit 공개 API, 백필/업데이트, SQLite 스토어 `state/btc_market.db`).
- **엔진**: `engine/regime.py`·`signal.py`·`sizing.py`·`config.py` — 추세추종 스윙 전략. 진입 게이트 score≥70 + 4h 추세강도(ts)≥2.0, 변동성 연동 레버리지 상한(8~12x, `lev ≤ 1/(12·ATR)`), 실(實) 펀딩비 모델(부호 인지, 6,800여 건 백필).
- **이벤트드리븐 백테스터**: 룩어헤드 제거(`end≤t` 슬라이스), 수수료/펀딩 순손익 회계, 약 30배 성능 최적화. 6년(2020.7~2026.6) 전구간 검증 — PF 1.81, 청산 0건. TP2/TP3 래더를 제거해 우측 꼬리를 열고(RR 1.35→2.29) 12h MA10 트레일링으로 승자를 끝까지 보유.
- **다자산 교차검증**: ETH·SOL로 엣지 일반화 확인(전략 변경 없이 검증), 운용 대상은 **BTCUSDT 단일**로 확정.

### 1-2. 헥사고날 코어 — 백테스트/라이브 동치 (parity)
전략 의사결정을 순수 함수 패키지 `prism-btc/core/`(`entries.py`·`exits.py`·`risk.py`·`actions.py`)로 추출하고, `backtest/engine.py`는 어댑터로 축소. **로직 중복 없이** 동일 코어를 백테스트와 라이브가 함께 구동합니다(바이트 동일성 검증).

### 1-3. 라이브 실행 — 섀도 → 데모(실주문)
- **섀도 페이퍼**: `live/shadow.py`+`runner.py` — 백테스트 의미론을 그대로 모사한 가상체결(포스트온리·수수료/슬리피지·펀딩 부호 인지), DB 영속·재시작 안전.
- **데모 실주문**: `live/demo.py`(`DemoAdapter`) — Bybit **데모 거래소에 실제 주문**(출금 호출 0건, 거래소 상태를 진실로 재조정). 단일 포지션→**3-트랜치(40/30/30) 피라미딩**으로 섀도/백테스트와 정합. 모든 예외를 흡수해 데몬이 멈추지 않음.
- E4 드로다운 리스크 오버레이는 전구간 재시뮬에서 고정 리스크보다 열위로 확인되어 **비활성화**, 고정 2% 리스크로 운용.

### 1-4. 자기개선 오토루프 (학습 기어)
- **매매일지+포스트모템**(`live/journal.py`·`postmortem.py`): R-분해 자기검증·MFE/MAE·백테스트 분위수. LLM은 주문 경로 밖에서만 동작(규칙을 바꿀 수 없음), 가설은 구조화된 "노브 메뉴"{param,value}로만 제출.
- **주간 리서치 팩토리**(`research/factory.py`·`overrides.py`): 화이트리스트 4개 파라미터를 train(2020~24)+OOS(2025~) **이중 게이트**로 검증해 통과 시 자동 활성화, 주간 재검증 실패 시 자동 롤백. 결정 100% 데이터 기반(LLM 권한 없음). 워크포워드 메타백테스트에서 0건 채택/10건 기각 — **거짓 채택 0** 확인.

### 1-5. 텔레그램 알림 · 이상감지 워치독
- **실시간 거래 알림**(`live/notifier.py`, #325): 신규 진입/추가/청산마다 즉시 메시지(멱등 ID 마커, 콜드스타트 가드).
- **일일 상태 리포트**(`live/telegram_reporter.py`): 비전문가용 한국어로 전면 작성(롱/숏·R·PF·MDD → 상승/하락 베팅·배수·손익), `[시범운용(모의투자)]` 배너+면책 고지.
- **이상감지 헬스체크**(`live/healthcheck.py`, #327): 정상이면 침묵, 문제 시에만 운영자 알림(데몬 다운·에러 버스트·시세 정체·자본 이상·포지션 정체·섀도-데모 괴리 6종). 일반인용 시스템 가이드(`tasks/btc_system_guide.html`) 동봉.

> **운용 단계**: 현재 **데모(모의투자)** 입니다. 실거래소에 실주문을 내지만 가상자금이며, 실제 손익은 발생하지 않습니다. 2주 모니터링 후 실계좌 전환을 단계적으로 검토합니다.

---

## 2. 오닐식 매매엔진 심화 (KR/US 주식)

### 2-1. 멀티 이동평균 국면 템플릿 (#309)
단일 20MA로 시장 국면을 판단하던 방식을 **다중 MA**(KR 5/20/60/120, US 10/20/50/200)로 재설계했습니다. **200일선(US)/120일선(KR)을 강세·약세 1차 분기선**으로 삼아, 그 아래의 베어마켓 반등을 `strong_bull`이 아닌 `sideways`로 분류 → **약세장 반등 과매수를 차단**. 국면은 매수 매트릭스와 매도 트레일링 밴드를 함께 구동하므로 드로다운 축소에 직접 기여합니다. 기존 6개 출력 문자열·API 호출 수는 그대로(하위호환). 차트/리포트/매크로 에이전트 MA 세트도 시장별로 정합.

### 2-2. 분산일(Distribution Day) 결정론적 카운팅 + 국면 킬스위치 (#320)
LLM 판단에 맡기던 분산일 킬스위치를 **결정론적 계산**으로 교체했습니다. 25세션 롤링 윈도에서 "기관 매도 세션(상승 거래량에 종가 -0.2% 이상 하락)"을 세어(`+5% 회복 시 소멸) `index_summary.distribution_days`로 주입, 임계 초과 시 국면을 한 단계 자동 강등. **장기 리스크 기반 백테스트**(MaxDD/Sharpe)로 임계를 시장별 최적화 — **KR=6, US=7**(S&P 장기 우상향 특성상 6은 복리 손실로 과penalize). 킬스위치는 **신규 매수에만** 적용 — 보유 종목의 매도/트레일링은 원래 국면을 유지(분산일이 조기청산을 강제하지 않음).

### 2-3. 오닐 추세추종 규칙기반 매도 폴백 전면 교체 (#305)
2026-06-04 US 오후 사이클에서 OpenAI 429(쿼터 소진)로 전 종목 AI 매도분석이 실패하자, **구(舊) 폴백이 MU(+53%)·ANET(+24%) 등 승자를 포함해 5종목을 무차별 청산**했습니다. 근본원인은 반(反)오닐 폴백 로직(수익 ≥10% 자동매도 + 30/60/90일 시간기반 매도). 신규 `cores/oneil_fallback.py`(KR/US 동일)는 순수 stdlib 오닐/CANSLIM 매도 함수로 **TIER1 하드스탑(-7%)·TIER2 트레일링(+5%부터, 강세 -8%/약세 -5%)** 만 적용, 수익률·시간기반 매도는 완전 제거. **시장 국면을 매도 시점에 라이브로 계산**(OpenAI 비의존)해 매수시점의 낡은 값을 쓰지 않습니다. 사고 재현 시 승자 4종 보유·RL만 -8.5%에서 트레일 청산.

### 2-4. 법인이벤트 강제청산(TIER0) — 뉴스 기반 자율화 (#330~#335)
상장폐지·공개매수·정리매매·거래정지·감사의견 거절처럼 **기술적 매도 로직(추세/스탑/트레일링)으로는 빠져나올 수 없는** 사건(예: 공개매수로 가격이 고정돼 스탑이 안 걸리는 자진상폐)을 처리하는 최우선 청산 경로입니다. 5개 PR을 거쳐 **수동 오버라이드 리스트 → 완전 자율 뉴스 기반 AI 판단**으로 진화했습니다.
- **#330** 신규 `cores/corporate_status.py` + TIER0 단일 의사결정점 연결(시뮬레이터·실거래 동시 청산).
- **#331** 매 매도사이클에 KIS `iscd_stat_cls_code`를 1회 컨텍스트로 일괄 프리페치·주입(토큰 레이트리밋 회피).
- **#332** 자동 강제청산을 **관리종목(51)** 으로 한정 — 시장경고(52/53/54)는 단기 급등 과열 신호라 승자를 강제 손절시킬 위험, 거래정지(58)는 모호 → 제외.
- **#333** 운영 매도경로(`EnhancedStockTrackingAgent`)에 perplexity 기반 **Core-0 뉴스 체크** 추가 — 상폐/공개매수/정리매매/거래정지/감사거절을 뉴스로 확인 시 `[법인이벤트]`로 강제 매도(미확인 루머는 무시하는 정밀도 가드).
- **#334** `create_sell_decision_agent`의 `server_names`에 누락된 `perplexity` 추가(체크가 실제 동작하도록).
- **#335** 수동 오버라이드 리스트 **완전 제거**(유지보수 포인트 삭제) + **US 미러링**(상폐/공개매수/파산/거래소 컴플라이언스 상폐/SEC 등록말소/M&A 뉴스 체크 + perplexity 추가).

---

## 3. 학습 파이프라인 부활 · 관측성

### 3-1. US 매매일지 활성화 (#312·#313·#314)
US 에이전트는 매매일지 인프라(매도시 기록·매수시 경험기반 점수보정·텔레그램 노출)를 모두 갖췄지만 `enable_journal=False` 하드코딩으로 **한 번도 실행되지 않아**(US 0행) MU·SNDK를 한 주에 두 번 재진입·재손절했습니다. ENABLE_TRADING_JOURNAL env 존중(#312), `market` 인자 지원으로 US=yahoo_finance 전환(#313), 최근 60일 42건 일회성 백필(#314)로 "N일 전 청산 — 재추격 주의" 경고가 즉시 발화하도록 했습니다.

### 3-2. 직관 생성 부활 (#316·#317 + 직접커밋)
`trading_intuitions`가 **2026-02-22 이후 5주 연속 0행**이었습니다. 원인은 크래시가 아니라 layer2→3 압축이 주당 2~7건의 소량만 LLM에 공급해 "패턴 ≥2회 반복" 조건이 거의 안 맞은 것. `refresh_intuitions`(90일·40건 코퍼스 일괄, #316) + 출력 JSON 스키마 명시(#317) + 압축 스킵 가드 앞으로 이동(매 실행 보장)으로 복구.

### 3-3. US 주간 압축 연결 (#321) · 국면 분포 로깅 (#310)
- 주간 압축 잡이 KR 패스만 돌려 US 직관이 압축에서 도출되지 않던 문제를 `run_us_compression()`(importlib 로딩, KR 스킵에 비의존)으로 연결.
- 신규 멀티 MA 국면의 관측성: `logs/regime_history.jsonl` 사이클별 기록 + `tools/regime_backtest.py`(NEW vs LEGACY 분포 비교, 휩쏘 US -33%/KR -44%).

---

## 4. 운영 인프라 · 버그 정리

| # | 변경 | PR |
|---|------|-----|
| 4-1 | **Firecrawl Spark 에이전트 전환** — `/signal`·`/theme`·`/ask`(+US)를 Spark 에이전트 경로로 이전, 소스 그라운딩 강제(출처 URL 인용) | #300 |
| 4-2 | **Spark 타임아웃/폴백 가드** — `asyncio.wait_for`(120s/240s) + 실패 시 search+Claude 폴백, `/ask` 크레딧 400→2000 | #301 |
| 4-3 | **US AI 매도 프롬프트에 라이브 국면 주입** — 모델 자체 조회 대신 시스템 계산 국면을 권위값으로 주입(#305 폴백과 동일 소스 정합) | #306 |
| 4-4 | **도메스틱 주문창 KST 정합** — naive `datetime.now()` → `ZoneInfo("Asia/Seoul")` 기반 정규/마감/예약/불가 구간 분류 | #304 |
| 4-5 | **US 6PM 포트폴리오 리포트** — US 계좌 시작일(2026.01.20)·원금($10,000)·시즌수익 표시 | #329 |
| 4-6 | **주간 인사이트 US 매매원칙 집계** — 하드코딩 0 제거, `market='US'` 실제 쿼리 + KR/US 분리 | #315 |
| 4-7 | (버그) **US 매수신호 market 필드 누락** — publish 시 `market` 미복사로 US 매수가 국내 KIS로 라우팅→0 KRW÷0 division 오류, 매수 미체결 수정 | #322 |
| 4-8 | (버그) **신규 매수후보 누락 방지** — 시세 조회 단발 타임아웃에 3회 백오프 재시도(신규 후보는 DB 폴백이 0 반환되는 비대칭 해소) | #298 |
| 4-9 | (버그) **횡보 트리거 MA20 하락 게이트** — 하락추세(MA20 하단) 종목이 "거래량 증가 횡보주"로 매수되던 문제, 6개 트리거 중 유일하게 방향필터 부재였던 곳 보강 | #299 |
| 4-10 | (버그) **피라미딩 추가매수 섹터 집중 한도 우회(US)** — 이미 보유·섹터 카운트된 종목 추가매수를 막던 무의미 차단 해소 | #303 |
| 4-11 | (정리) refresh_intuitions 진단 로그 원문 덤프 제거(#317 검증 후) | #319 |

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `prism-btc/**` (신규 모듈 ~70파일) | 수집·엔진·코어·백테스트·라이브(섀도/데모)·리서치·알림·헬스체크 | #324, #325, #326, #327 |
| `cores/data_prefetch.py` · `prism-us/...` | 멀티 MA 국면, 분산일 카운팅/주입, 국면 스냅샷 로깅 | #309, #310, #320 |
| `cores/oneil_fallback.py` · `prism-us/...` (신규) | 오닐 규칙기반 매도 폴백(TIER1/2), 라이브 국면 계산 | #305, #309 |
| `cores/corporate_status.py` (신규) · `cores/agents/trading_agents.py` · `prism-us/...` | 법인이벤트 TIER0 + Core-0 뉴스 자율 매도(KR/US) | #330~#335 |
| `cores/agents/trading_agents.py` · `prism-us/...` | 분산일 킬스위치 프롬프트(신규매수 한정), 국면 주입 | #320, #306 |
| `prism-us/us_stock_tracking_agent.py` · `cores/agents/trading_journal_agent.py` | US 매매일지 활성화·market 인자 | #312, #313 |
| `tracking/compression.py` · `compress_trading_memory.py` | 직관 부활(refresh_intuitions·스키마), US 압축 연결 | #316, #317, #321 |
| `telegram_ai_bot.py` | Firecrawl Spark 전환·타임아웃 폴백 | #300, #301 |
| `trading/domestic_stock_trading.py` | KST 주문창 분류, `iscd_stat_cls_code` 반환 | #304, #331 |
| `messaging/gcp_pubsub_signal_publisher.py` · `messaging/redis_signal_publisher.py` | BUY 신호 market 필드 전파 | #322 |
| `tracking/helpers.py` · `prism-us/us_stock_tracking_agent.py` | 시세 재시도, 직관/압축 보조 | #298 |
| `trading/portfolio_telegram_reporter.py` · `weekly_insight_report.py` | US 포트폴리오/주간 집계 | #329, #315 |
| `tools/regime_backtest.py` · `tools/backfill_us_journal.py` (신규) | 국면 백테스트·US 일지 백필 | #310, #314 |
| `tests/**` | 신규/회귀 테스트 다수(주식 + BTC 269+) | 전반 |

---

## 업데이트 방법

```bash
git checkout main
git pull origin main
# requirements / env (주식) 변경 없음

# 운영서버
ssh root@<server>
cd /root/prism-insight
git pull --ff-only origin main
```

> **주식 시스템**: 의존성·DB 스키마 변경 없음. 기존 운영 그대로 동작합니다.
>
> **PRISM-BTC(선택, 데모 운용 시)**: `prism-btc/`는 주식 시스템과 독립 실행됩니다.
> - 신규 테이블 `btc_*`(positions/trading_history/equity_curve/events/meta/signal_log/journal/lessons)는 루트 `stock_tracking_db.sqlite`에 첫 실행 시 자동 생성(주식 테이블 불간섭).
> - 가격 DB는 별도 파일 `prism-btc/state/btc_market.db`(gitignore).
> - 신규 env(선택): `BTC_TELEGRAM_CHANNEL_ID`(미설정 시 `TELEGRAM_CHANNEL_ID` 폴백), `BTC_OPS_CHANNEL_ID`(헬스체크), Bybit 데모 키.
> - LaunchAgent 4종: `com.prism.btc-demo`(:02/:32), `com.prism.btc-telegram`(4h), `com.prism.btc-research`(일 18:05), 헬스체크. macOS는 crontab 대신 LaunchAgent 사용.

---

## 알려진 제한사항

1. **PRISM-BTC는 데모(모의투자) 단계**: 실거래소에 실주문을 내지만 가상자금이며 실제 손익은 없습니다. 2주 모니터링 후 실계좌 전환을 단계적으로 검토합니다(고정 3% 시작 → 5%). 라이브 실전 성과는 백테스트와 다를 수 있습니다.
2. **BTC 백테스트 한계**: 6년(2020.7~2026.6)·BTCUSDT 단일·이벤트드리븐 검증이나, 펀딩/슬리피지 모델·체제 의존성이 있어 라이브 관찰이 필수. 자기개선 오토루프는 화이트리스트 4개 파라미터만 자동 조정(구조 변경은 사람 검토).
3. **#320 분산일 임계**: KR=6/US=7은 장기 리스크 백테스트 기반 초기값. 라이브 분포로 재조정 가능(킬스위치 = 신규매수 한정, 매도 미간섭).
4. **#309 멀티 MA 국면**: 합성/과거 데이터 검증 완료이나 실시장 국면 전환 빈도는 관찰 필요(휩쏘 감소는 백테스트 수치).
5. **#305 매도 폴백 표본 한계**: 1회 OpenAI 장애 사고 + 페르소나 합의 기반 → 모니터링 필수.

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.14.0 — 비트코인 자동매매 모듈 신규 + 매매엔진 심화

지난 보름간 가장 큰 신규 작업과 주식 매매엔진 고도화를 함께 진행했습니다.

🪙 1) PRISM-BTC — 비트코인 선물 자동매매 모듈 신규 (시범운용/모의투자)
주식과 완전히 분리된 새 모듈로, 비트코인 무기한 선물을 추세추종 전략으로
자동매매합니다. 6년 백테스트로 검증한 전략에, 스스로 학습하며 개선하는
오토루프, 실시간 매매 알림, 일일 리포트, 이상감지 워치독까지 갖췄습니다.
※ 현재는 모의투자 단계입니다 — 가상자금으로 운용되며 실제 손익은 없습니다.

📈 2) 멀티 이동평균 국면판단 (주식)
시장 국면을 20일선 하나가 아닌 여러 이동평균(국내 5·20·60·120,
미국 10·20·50·200)으로 종합 판단합니다. 200일선(미국)·120일선(국내) 아래
약세장 반등을 "강세장"으로 오인해 추격하던 문제를 차단합니다.

🛡️ 3) 매도 안전판 전면 교체
AI 분석이 일시 장애(쿼터 소진)일 때, 예전엔 수익 종목까지 싸잡아 정리하던
대체 로직이 있었습니다. 이를 오닐식(손절·트레일링만, 수익/시간 기반 매도
제거)으로 바꿔 승자를 끝까지 보유하도록 정비했습니다.

📊 4) 분산일(기관 매도) 자동 감지
기관 매도 신호인 "분산일"을 사람/AI 판단 없이 자동으로 세어, 과열 구간의
신규 매수만 한 단계 보수적으로 조정합니다(보유 종목 매도는 건드리지 않음).

🚨 5) 상장폐지·공개매수 등 법인이벤트 강제청산
주가가 고정돼 손절이 안 걸리는 상장폐지·공개매수·거래정지 같은 사건을
뉴스로 자동 확인해 즉시 청산합니다(국내·미국 모두 적용).

🔧 그 외: 미국 매매일지 활성화, 멈췄던 학습(직관) 복구, 봇 명령어 데이터
수집 안정화, 주문 시간대(KST) 정합, 미국 매수신호 누락 버그 등 다수 개선.

📊 모든 주식 매매 로직은 5인 투자 거장(오닐·미너비니·드러켄밀러·버핏·퀀트)
관점으로 검토해 합의된 부분만 반영했습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.14.0 — New Bitcoin Auto-Trading Module + Deeper Trading Engine

Two weeks centered on a major new module and a deeper stock trading engine.

🪙 1) PRISM-BTC — new Bitcoin-futures auto-trading module (pilot / paper)
A brand-new module, fully separate from stocks, that auto-trades Bitcoin
perpetual futures with a trend-following strategy. It ships with a strategy
validated over 6 years of backtests, a self-improving auto-loop, real-time
trade alerts, daily reports, and an anomaly watchdog.
※ Currently in paper-trading mode — virtual funds only, no real P&L.

📈 2) Multi-moving-average regime detection (stocks)
Market regime is now judged from several MAs (KR 5/20/60/120, US 10/20/50/200)
instead of a single 20-day line. Bear-market rallies below the 200-day (US) /
120-day (KR) line are no longer mistaken for a "strong bull" and chased.

🛡️ 3) Sell safety-net fully replaced
When AI analysis briefly fails (quota exhaustion), the old fallback could
liquidate even winning positions. It's now O'Neil-style (stops & trailing only;
profit/time-based selling removed), letting winners run.

📊 4) Automatic distribution-day (institutional selling) detection
"Distribution days" are now counted deterministically — no human/AI guess —
to make only NEW buys one step more cautious in overheated phases (existing
holdings' sell logic is untouched).

🚨 5) Forced exit on corporate events (delisting, tender offers, halts)
Events where price is pinned and stops never trigger (delisting, tender offer,
trading halt) are now detected via news and liquidated immediately (KR & US).

🔧 Also: US trading-journal activated, stalled learning (intuitions) restored,
bot-command data fetching stabilized, KST order-window alignment, a US buy-signal
omission bug, and many more fixes.

📊 All stock trading logic was reviewed through 5 investing masters
(O'Neil · Minervini · Druckenmiller · Buffett · Quant) — only consensus adopted.
```

---

**Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>**
