# PRISM-INSIGHT v2.13.0 — 매매 엔진 대규모 업그레이드 (피라미딩 · 매도 안정화 · KR 스크리닝 재설계)

> **Release Date**: 2026-05-29
> **Range**: `v2.12.0`(dff7350) → `main`(2a1ba8f) · 13 PRs

## 개요

v2.13.0은 세 갈래의 작업을 묶습니다.

1. **KR 종목 스크리닝 재설계 (#289)** — 당일 raw 급등률 기반 선정이 폭등장에서 과열(climax) 종목을 양산해 매수에이전트가 정당하게 거절하던 병목을, 윌리엄 오닐/CAN SLIM식 **상대강도(RS) + 과열도(extension)** 소프트스코어로 해소.
2. **매매 사이클 안정화** — 0~1일 노이즈 매도 정비(#279), 강세장 추가매수(피라미딩, #288), 매수 보류 메시지 명확화(#281), 매매일지 피드백 루프 투명화(#280·#282).
3. **운영 인프라 버그 정리** — US 거래소 코드 해결(#271·#275), ChatGPT OAuth 프록시 SSE 파싱(#278), US 네임스페이스 충돌 3종(#276·#277·#284), Firecrawl MCP 핀(#286), 봇 명령어 날짜 앵커(#283).

모든 매매 로직 변경은 5인 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 검토를 거쳐 합의 영역만 채택했습니다.

---

## 1. KR 종목 스크리닝 재설계 (#289) ⭐

### 문제
KR 스크리닝이 **"당일 raw 급등률"** 로만 후보를 걸러, 가격제한폭 ±30%와 개인 매수 광기 탓에 같은 상승률이라도 US보다 훨씬 **과열·climax(소진 급등)** 종목이 올라왔습니다. 그 결과 CAN SLIM 매수에이전트가 과열 추격으로 정당하게 **2/10점**을 매겨 거절 → 폭등장에서 진입률이 낮았습니다(최근 30일 score=2가 51건, 진입 2주째 0). 매수에이전트는 정상이며, 진짜 병목은 스크리닝 품질이었습니다.

### 해법: RS + extension 소프트스코어 (후보 컷 없이 재정렬)
선정 점수를 **국면별 가중치**로 재구성했습니다.

```
final_score = w_comp·모멘텀 + w_agent·R/R + w_rs·RS + w_ext·과열점수
```

| 국면 | 모멘텀 | R/R | RS | 과열 |
|---|---|---|---|---|
| strong_bull | 0.20 | 0.35 | **0.30** | **0.15** |
| moderate_bull | 0.25 | 0.35 | 0.20 | 0.20 |
| sideways | 0.20 | 0.35 | 0.15 | **0.30** |
| moderate/strong_bear | 0.15 | 0.35 | 0.15 | **0.35** |

- **RS (상대강도)**: 종목의 다주(60일) 수익률을 후보군 내 정규화 → "시장보다 꾸준히 강한 주도주"에 가점. 오닐 학파의 핵심 기둥.
- **과열도(extension)**: `(현재가 − MA20)/MA20`를 그 종목 **ADR(평균 일일 변동폭)로 정규화** → MA20에서 변동성 대비 멀리 뜬 climax 종목에 감점. **52주 신고가가 아닌 MA20+ADR 기준**이라 매수에이전트 모멘텀신호 #3(신고가 가점)과 **이중 평가 충돌 없이 상호보완** — 건강한 돌파는 통과, climax만 down-weight.
- **국면 차등**: strong_bull은 RS↑·과열 완화(단 0이 아님 — 오닐의 "상승장 말기 climax" 경고 반영), 평온·약세장은 과열 페널티 강화(추격이 가장 위험한 구간).
- **macro 섹터 리더 트리거를 strong_bull에서도 재활성화** — 폭등장에서도 섹터 주도주가 후보로 surface.

### 안전 설계 (백테스트 불가 전제)
- **후보를 자르지 않고 순위만** 바꾸는 soft 방식 → 폭등장에서 후보 0이 되는 위험 없음.
- 선정 JSON에 `rs_score`·`rs_relative`·`extension_score`·`extension_in_adr` 기록 → **라이브 관찰**로 효과 검증.
- 가중치는 모듈 상수(`REGIME_SCORE_WEIGHTS`) 1곳 → 재배포 없이 튜닝. **킬 스위치 = RS·과열 가중치 0**.
- **US 미적용**: US는 시장 구조가 달라(±제한폭 없음, 과열 적음) 스크리닝이 건강하며 병목은 슬롯/섹터 캡 — 별개 과제.

### 운영 검증 (2026-05-29 오후 cron, strong_bull 국면)
```
[#289] Blend weights for regime 'strong_bull': composite=0.2, agent=0.35, RS=0.3, extension=0.15
- 대덕전자: Composite=0.621, RS=1.000, Ext=0.723(adr=3.1), Final=0.931 ← 1위
- NAVER:   Composite=0.662(모멘텀 최고), RS=0.155, Final=0.725 ← 3위로 밀림
```
모멘텀 최고였던 NAVER가 RS 부족으로 밀리고, **상대강도 압도적인 대덕전자(시장 대비 +279%)** 가 1위로 — 의도한 오닐식 주도주 선별이 라이브에서 작동 확인.

---

## 2. 매매 사이클 안정화

### 2-1. parabolic regime + 매도 룰 전면 정비 (#279)
지난 5일 매매(KR 6 / US 9)에서 **0~1일 보유 16%, 평균 -4~5% 손실** 패턴 확인. 4가지 구조적 모순을 정리:
- **parabolic이 strong_bull보다 보수적이던 역전 수정** — 폭등장 진입 차단 + 조기 손절 해소.
- 매수 시나리오의 sell_triggers 예시("목표가 도달 시 전량 매도")가 LLM에 박제되던 문제 → 트레일링 스탑 친화적으로 재작성.
- **장중(intraday) wick 손절 → 종가 기준 매도**로 전환 (노이즈성 손절 방지).
- LLM이 1.1% 같은 비정상 손절폭을 잡던 stop_loss 산정 규칙 보정.
> 사용자 철학(오닐 추세추종, "let winners run", 0/1일 노이즈 회전 혐오)과 정합.

### 2-2. 강세장 추가매수 — 피라미딩 (#288)
강한 강세장에서 **이미 보유한 수익 종목에 추가 진입(피라미딩)** 을 독립 행 모델로 지원. UNIQUE 제약 제거 마이그레이션 + 분할매도 + 행별 id 스코핑 + KIS quantity 파라미터. KR+US 미러. 테스트 101개 통과.
> 운영서버 첫 실행 시 자동 스키마 마이그레이션(`*_pre_pyramiding_backup` 생성).

### 2-3. 매매일지 피드백 루프 투명화 (#280, #282)
매매일지가 매수/매도 판단에 어떻게 영향을 줬는지 가시화: `journal_reflection` 필드, 최근성 ⚠️ 태그(≤5거래일), `score_adjustment` 영속화, 텔레그램 매수/보류 메시지 노출, 주간 영향 통계. 당일 매도 후 재매수(#282)의 근본원인 추적에 활용.

### 2-4. 매수 보류 메시지 명확화 (#281)
실제 매수인데 "매수사인"으로 오인되던 혼동 해소 — 보류 메시지에 `결정: Enter (실제 보류 — 사유: …)` 인라인 명시 (KR).

---

## 3. 운영 인프라 버그 정리

### 3-1. US 거래소 코드 해결 — CBOE/PYPL 버그류 (#271, #275)
KIS `APBK0656 - 해당종목정보가 없습니다` 에러의 근본 해결:
- 40종목 하드코딩 거래소 목록 제거 → **`data/exchange_cache.json` 자동 캐시 + yfinance 동적 조회 + KIS 가격 API 프로브로 권위적 해결**.
- yfinance `'BTS'`(Cboe BZX) 등 미매핑 거래소 → KIS 분류(NASD/NYSE/AMEX)로 정확 변환.
- **시뮬레이터 독립화**: KIS 실주문 실패 시 `us_stock_holdings` 레코드를 삭제하던 버그 제거(시뮬레이터와 실거래 분리 — KR 설계와 일치).

### 3-2. ChatGPT OAuth 프록시 SSE 파싱 (#278)
Codex 엔드포인트의 SSE 스트림이 완료된 output을 `response.output_item.done` 이벤트로 전달하는데 프록시가 `response.completed`(빈 output)만 읽어 **모든 에이전트 호출이 0자 반환**하던 치명적 버그 수정. `reasoning_effort` → Responses API `reasoning.effort` 매핑, `oauth_login --force` 플래그 추가.

### 3-3. US 네임스페이스 충돌 3종 (#276, #277, #284)
`prism-us/`가 `sys.path` 선두일 때 루트 패키지를 가리는 충돌:
- **#276**: `OpenAIResponsesLLM` import 실패 → `importlib.util` 절대경로 로딩.
- **#277**: US 대시보드 JSON cron이 `trading.kis_auth` import 실패로 매일 08:00 KST 갱신 안 되던 문제 해결.
- **#284**: Pub/Sub subscriber에서 US trading 모듈을 파일경로 로딩으로 분리.

### 3-4. Firecrawl MCP 버전 핀 (#286)
운영서버의 unpinned `firecrawl-mcp` npx 캐시 손상으로 MCP 서버가 도구 0개로 기동 → LLM이 학습데이터로 fallback해 "실시간 원문 데이터가 확보되지 않아…" 스팸 생성하던 문제. `firecrawl-mcp@3.17.0` 핀으로 캐시 키 고정.

### 3-5. 봇 명령어 날짜 앵커 (#283)
Firecrawl 봇 명령어(`/ask`·`/signal`·`/theme`·US판) followup이 현재 날짜를 몰라 1년 전 데이터를 조회하던 문제 → 현재 날짜 앵커 + `time` MCP 서버 + `get_current_time` 지침.

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `trigger_batch.py` | KR 스크리닝 RS+과열 소프트스코어, 국면 가중치, 헬퍼 2종, 임계 통일 | #289 |
| `cores/agents/trading_agents.py` · `prism-us/...` | 매도 룰 정비, parabolic 역전 수정, sell_triggers 재작성 | #279 |
| `stock_tracking_agent.py` · `stock_tracking_enhanced_agent.py` · `prism-us/...` | 피라미딩 독립 행 + 보류 메시지 명확화 | #288, #281 |
| `tracking/db_schema.py` · `tracking/helpers.py` · `prism-us/tracking/...` | UNIQUE 제거 마이그레이션, 분할매도, 행별 id | #288 |
| `tracking/journal.py` · `compress_trading_memory.py` · `prism-us/...` | 매매일지 영향 추적·투명화 | #280, #282 |
| `prism-us/trading/us_stock_trading.py` · `data/exchange_cache.json` | 거래소 동적 조회 + KIS 권위적 해결 | #271, #275 |
| `cores/chatgpt_proxy/` | SSE output item 수집, oauth_login --force | #278 |
| `report_generator.py` · `telegram_ai_bot.py` | 봇 명령어 날짜 앵커 | #283 |
| `examples/generate_us_dashboard_json.py` · `examples/messaging/...` | US 네임스페이스 충돌 해소 | #277, #284 |
| `mcp_agent.config.yaml.example` | firecrawl-mcp@3.17.0 핀 | #286 |
| `tests/test_issue_288_pyramiding.py` · `tests/test_issue_289_screening.py` | 신규 테스트 (101 + 59) | #288, #289 |

---

## 업데이트 방법

```bash
git checkout main
git pull origin main
# requirements / env 변경 없음

# 운영서버
ssh root@<server>
cd /root/prism-insight
git pull --ff-only origin main
# #288 첫 실행 시 자동 스키마 마이그레이션 (UNIQUE 제거, *_pre_pyramiding_backup 생성)
# → 적용 전 stock_tracking_db.sqlite 백업 권장
```

> 의존성(requirements.txt) 변경 없음. DB 마이그레이션은 #288 피라미딩에 한해 첫 실행 시 자동.

---

## 알려진 제한사항

1. **#289 백테스트 불가**: 이 시스템은 백테스트가 불가능해 효과 검증은 라이브 관찰(2~4주 KR 진입수·평균 buy_score 추세)로만 가능. 효과 미미 시 가중치 상향 또는 극단 climax 하드컷(현재 v1 제외)을 2차로 검토.
2. **extension/RS 임계 튜닝 필요**: `EXTENSION_ADR_T_LOW=2 / T_HIGH=6`, RS lookback 60일은 초기값. 라이브 분포를 보고 조정.
3. **US 캡 병목 미해결**: US는 #289 대상 아님(슬롯/섹터 캡이 병목) — 대칭 과제로 남김.
4. **#279 표본 한계**: 매도 룰 정비는 5일·15건 매매 패턴 + 페르소나 합의 기반 → 모니터링 필수.

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.13.0 — 매매 엔진 대규모 업그레이드

지난 한 달간 매매 전 과정을 다듬었습니다. 핵심 5가지입니다.

📈 1) 강세장 추가매수(피라미딩) 도입
강한 추세가 확인된 보유 종목에 "불타기"로 추가 진입할 수 있게 했습니다.
수익 종목의 추세를 끝까지 활용하고, 분할 매도로 차익을 단계적으로 실현합니다.
(이번 릴리즈 중 변경량이 가장 큰 작업 — 한국·미국 양쪽 적용)

🛡️ 2) 매도 로직 안정화 — 0~1일 노이즈 매도 근절
매수 직후 장중 흔들림만으로 손절되던 문제를 "종가 기준 매도"로 바꿨습니다.
"목표가 도달 시 전량 매도"가 시나리오에 박제되던 것도 손질해,
수익은 추세 따라 길게 끌고 가도록(let winners run) 정비했습니다.

📊 3) 한국 종목 스크리닝 재설계
"당일 급등률"로만 고르던 방식을 오닐식 "상대강도 + 과열도"로 바꿨습니다.
시장보다 꾸준히 강한 진짜 주도주를 위로, 이미 고점까지 치솟은
과열 종목(막판 불꽃)은 아래로 정렬합니다. (한국 시장 전용)

📝 4) 매매일지 피드백 투명화
과거 매매 기록이 현재 매수/매도 판단에 어떻게 반영됐는지
매수·보류 메시지에 투명하게 표시하고, 주간 영향 통계를 제공합니다.

🔧 5) 운영 안정성 다수 개선
미국 거래소 코드 자동 인식, ChatGPT OAuth 연결, Firecrawl 데이터 수집,
봇 명령어 날짜 인식, 시세 조회 자동 재시도 등 안정성을 폭넓게 강화했습니다.

📊 모든 매매 로직은 5인 투자 거장(오닐·미너비니·드러켄밀러·버핏·퀀트)
관점으로 검토해 합의된 부분만 반영했습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.13.0 — Major Trading-Engine Upgrade

A month of refinements across the entire trading cycle. Five highlights:

📈 1) Strong-bull pyramiding
You can now add to winning positions when a strong trend is confirmed —
riding winners further and scaling out of profits in stages.
(The largest change in this release — applied to both KR and US.)

🛡️ 2) Sell-logic stability — eliminating 0-1 day noise stops
Sells now trigger on the CLOSING price, not intraday wicks, so freshly
bought positions aren't stopped out by momentary swings. We also stopped
"sell-all at target" from being hard-coded into every scenario — letting
winners run with the trend.

📊 3) Korean screening redesign
From "today's raw surge %" to O'Neil-style "Relative Strength + extension":
true leaders consistently stronger than the market rank up, while
already-extended "blow-off" stocks rank down. (Korea-only.)

📝 4) Transparent trading-journal feedback
How past trades influenced the current buy/sell decision is now shown
transparently in buy/hold messages, with weekly impact stats.

🔧 5) Broad operational hardening
US exchange-code resolution, ChatGPT OAuth, Firecrawl data fetching,
bot date anchoring, and automatic price-query retries — all strengthened.

📊 All trading logic was reviewed through 5 investing masters
(O'Neil · Minervini · Druckenmiller · Buffett · Quant) — only consensus adopted.
```

---

**Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>**
