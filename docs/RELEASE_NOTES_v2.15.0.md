# PRISM-INSIGHT v2.15.0 — 실시간 리스크 루프(Loop A/B/C) · ChatGPT 구독 기반 LLM(OAuth) · CI/코드품질

> **Release Date**: 2026-06-22
> **Range**: `v2.14.0`(46f2d14b) → `main`(6e4bf5b7) · 43 commits / 19 PRs (#336–#355)

## 개요

v2.15.0은 네 갈래의 작업을 묶습니다.

1. **실시간 리스크 관리 루프 신설 (Loop A/B/C)** ⭐ — 배치(오전/오후) 사이의 공백을 메우는 **고빈도 손실방어 루프** 3종. Loop A(파국적 하드스톱), Loop B(종가확인형 추세이탈/50MA), Loop C(미체결 추격 + KIS 정정·취소·미체결 TR 래퍼). 기존 배치 매도 경로(시뮬레이터·KIS·텔레그램)를 그대로 재사용해 일관성을 보장하며, **전부 SHADOW 기본**으로 안전하게 출시됩니다.

2. **ChatGPT 구독(OAuth) 기반 LLM 전환 ⭐** — 공급자 비종속 LLM 포트 계층 + openai-agents 백엔드 + **OAuth Responses 프록시**(#347)를 토대로, API키 없이 **ChatGPT Plus 구독으로 전 분석 배치를 구동**할 수 있게 했습니다. Codex 백엔드 호환 이슈 3종(gpt-5-nano 미지원·previous_response_id 미지원·멀티턴 툴콜 상태)을 수정(#353·#354)하고, **OAuth 헬스/사용량 워치독 + 주간 쿼터 모니터**(#348·#355)를 추가했습니다.

3. **매도/청산 견고성 강화 (KR/US)** — 법인이벤트 청산 프롬프트의 결정성 강화(#336), 매도 최종확인 로직 견고화(#337), 미국 시즌 수익률을 **KIS 결제정합 총자산·USD 기준**으로 정정(#338·#339).

4. **CI/코드품질 · 운영 가시성** — GitHub Actions CI 워크플로 + Codacy 등급 배지(#346·#349), BTC 6시 리포트에 **보류 판단근거 상세화**(#352), .env 예시 동기화(#345).

모든 주식 매매 로직 변경은 5인 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 관점으로 검토해 합의 영역만 채택했습니다.

---

## 1. 실시간 리스크 관리 루프 — Loop A/B/C ⭐

배치 분석은 오전/오후 정해진 시각에만 돕니다. 그 사이 장중에 급변(파국적 하락, 추세 이탈, 미체결 방치)이 일어나면 다음 배치까지 무방비였습니다. Loop A/B/C는 이 **배치 간 공백**을 메우는 경량·고빈도 루프입니다. 셋 다 **기존 배치 매도 경로를 재사용**(시뮬레이터/KIS 실주문/텔레그램 알림 동일)하며 **SHADOW(관측 전용) 기본**으로 출시됩니다.

### 1-1. Loop A — 고빈도 파국적 하드스톱 (#340·#341·#342·#343·#344)
- **목적**: TIER1 하드스톱(-7% 등 손절선)·dormant 종목을 **장중 10분 주기**로 감시해, 다음 배치를 기다리지 않고 즉시 방어. 순수 결정론(`cores/oneil_fallback.py`의 `decide` 재사용, LLM 비의존 — 노이즈 회피).
- **프로세스 격리(#341)**: KR/US를 별도 프로세스로 분리해 `cores` 섀도잉(이름 충돌) 문제 해소.
- **경로 정합(#342)**: 배치 매도 경로를 그대로 재사용 → 시뮬레이터·KIS·텔레그램이 배치와 100% 일관.
- **알림/설정 정합(#343·#344)**: 기존 `TELEGRAM_CHANNEL_ID` 재사용, `.env` 로드로 `LOOP_A_*` 환경변수 정상 해석.
- **운용**: 기본 SHADOW. 킬스위치/활성화는 env(`LOOP_A_LIVE`, `LOOP_A_ENABLED`).

### 1-2. Loop B — 종가확인형 추세이탈 (50MA TIER1.5 / 트레일링) (#350)
- **목적**: 50일선 이탈·트레일 이탈을 **종가 기준 + ATR/버퍼**로만 인정하는 저빈도·확인형 매도. **휩쏘(50MA 노이즈 돌파 후 재지지) 회피**가 핵심 — "확인" 게이트로 발동 자체를 늦춥니다.
- SHADOW-gated. 50MA 라이브 배선은 Loop B 안에서만 활성화(Loop A엔 주입 안 함).
- 활성화 전 cadence-aware 백테스트로 휩쏘 vs 드로다운 순효과 검증 예정.

### 1-3. Loop C — 미체결 추격 + KIS 주문 TR 래퍼 (#351)
- **목적**: 지정가 주문의 **미체결 추격**(재가격/취소·재주문)과, 이를 위한 **KIS 정정·취소·미체결 조회 TR 래퍼** 신설.
- SHADOW-gated. 신규 TR은 소액 왕복 실주문 검증 후 LIVE 예정(현재 mock 단위테스트 통과).

> **운용 단계**: Loop A/B/C 모두 **SHADOW 기본 출시**입니다. 실거래 전환은 env 게이트 + 단계적 검증을 거칩니다.

---

## 2. ChatGPT 구독(OAuth) 기반 LLM 전환 ⭐

API키 종량과금 대신 **ChatGPT 구독(Plus)** 으로 LLM을 구동할 수 있도록, 공급자 비종속 포트 계층과 OAuth 프록시를 도입하고 Codex 백엔드 호환 문제를 해결했습니다.

### 2-1. LLM 포트 계층 · openai-agents 백엔드 · OAuth Responses 프록시 (#347)
- **공급자 비종속 LLM 포트**(`cores/llm/`) — 백엔드를 플래그로 교체(`LLM_BACKEND`, 기본 off=기존 mcp_agent). `tracking/journal.py` 등부터 포트 경유(Phase 3, 기본 비활성).
- **OAuth Responses 프록시**(`cores/chatgpt_proxy/`) — 로컬 프록시가 Chat Completions↔Responses API를 번역하고 ChatGPT 계정(Codex) 토큰으로 호출. 토큰 자동 리프레시(`token_manager.py`).
- **의존성**: openai 1.x→**2.x 허용 + openai-agents 0.7** 추가(기존 mcp_agent와 공존).

### 2-2. Codex 백엔드 호환 수정 3종 (#353·#354)
ChatGPT 계정(Codex) 엔드포인트는 일부 모델/파라미터를 거부합니다. OAuth 첫 풀배치에서 드러난 3개 장애를 수정:
- **gpt-5-nano 미지원**(#353): 프록시 모델맵에 `gpt-5-nano → gpt-5.4-mini` 추가 → 번역 등 경량 호출 정상화(API키 모드는 프록시 미경유라 nano 유지).
- **previous_response_id 미지원**(#353): 패스스루 strip 목록에 추가(store=False 강제라 무용).
- **멀티턴 툴콜 상태(#354)**: 매수/매도 결정 LLM(`cores/llm/openai_responses_llm.py`)을 **stateless로 전환** — `previous_response_id` 대신 매 턴 function_call+output을 누적 input으로 전송. store=False(OAuth)에서도 멀티턴 툴콜이 정확히 동작(멀티턴 실검증 통과).

### 2-3. OAuth 워치독 · 주간 쿼터 모니터 (#348·#355)
- **OAuth 헬스/사용량 워치독**(`tools/oauth_healthcheck.py`, #348 + 기반 #347): 토큰 건강·로그 에러 버스트 감시, **별도 알림 봇 토큰** 지원(`OAUTH_ALERT_BOT_TOKEN`) + `--test-alert`.
- **주간 쿼터 모니터(#355)**: Codex 200 응답 헤더(`x-codex-secondary-*`=주간 7일, `x-codex-primary-*`=5시간, `x-codex-plan-type`)를 파싱해 **사용량/잔량/리셋시각**을 테스트 채널로 보고(`--quota`). 잔량 부족·429 시 경보 → 구독 한도 소진을 사전 감지.

---

## 3. 매도/청산 견고성 (KR/US)

| # | 변경 | PR |
|---|------|-----|
| 3-1 | **법인이벤트 청산 프롬프트 결정성 강화** — 이벤트 강제청산(TIER0) 판단을 더 결정적으로 | #336 |
| 3-2 | **매도 최종확인 로직 견고화** — 매도 직전 최종 점검 경로의 예외/엣지 처리 강화 | #337 |
| 3-3 | **US 시즌 수익률 — KIS 결제정합 총자산 기준** — 18:00 시즌수익을 결제 정합 총자산으로 산출 | #338 |
| 3-4 | **US 시즌 수익률 — USD 기준 정정** — 시즌수익 = USD 표시 자산 − 시작자본 | #339 |

---

## 4. CI/코드품질 · 운영 가시성 · 문서

| # | 변경 | PR |
|---|------|-----|
| 4-1 | **GitHub Actions CI 워크플로** — Python 3.10/3.11/3.12 테스트 매트릭스 | #346, #349 |
| 4-2 | **Codacy 등급 + CI 상태 배지** — README 상단에 코드품질 배지(이슈 #346 제안 반영) | #349 |
| 4-3 | **BTC 6시 리포트 판단근거 상세화** — 관망(보류) 시에도 방향·점수·진입문턱거리·보류사유·추세강도·점수흐름 노출 | #352 |
| 4-4 | **.env 예시 동기화** — 운영 .env에 있으나 .env.example에 없던 8개 키 문서화 | #345 |

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `tools/loop_a_hardstop.py` (신규) · `cores/oneil_fallback.py` | Loop A 고빈도 하드스톱(결정론), KR/US 프로세스 격리, 배치 매도경로 재사용 | #340~#344 |
| `tools/loop_b_trend_exit.py` (신규) | Loop B 종가확인형 50MA/트레일 추세이탈 | #350 |
| `tools/loop_c_fill_chaser.py` (신규) · `trading/domestic_stock_trading.py` | Loop C 미체결 추격 + KIS 정정/취소/미체결 TR 래퍼 | #351 |
| `cores/llm/**` (신규) · `cores/chatgpt_proxy/**` (신규) | LLM 포트 계층, openai-agents 백엔드, OAuth Responses 프록시, 토큰매니저 | #347 |
| `cores/chatgpt_proxy/api_translator.py` · `cores/llm/openai_responses_llm.py` | Codex 호환: nano 매핑·previous_response_id strip·멀티턴 stateless | #353, #354 |
| `tools/oauth_healthcheck.py` | OAuth 헬스 워치독 + 별도 알림봇 + 주간 쿼터 모니터(`--quota`) | #348, #355 |
| `cores/agents/trading_agents.py` · `prism-us/...` | 법인이벤트 청산 결정성·매도 최종확인 견고화 | #336, #337 |
| `prism-us/...` · `trading/portfolio_telegram_reporter.py` | US 시즌수익 결제정합·USD 기준 | #338, #339 |
| `.github/workflows/ci.yml` (신규) · `README.md` | CI 매트릭스 + Codacy/CI 배지 | #346, #349 |
| `prism-btc/live/telegram_reporter.py` | 6시 리포트 보류 판단근거 상세화 | #352 |
| `.env.example` · `requirements` | env 키 동기화, openai 2.x + openai-agents | #345, #347 |
| `tests/**` | OAuth 프록시/멀티턴·루프 회귀 테스트 다수 | 전반 |

---

## 업데이트 방법

```bash
git checkout main
git pull origin main

# 운영서버
ssh root@<server>
cd /root/prism-insight
git pull --ff-only origin main
```

> **기본 동작 불변**: `LLM_BACKEND` 미설정(=기존 mcp_agent), `PRISM_OPENAI_AUTH_MODE` 미설정(=API키)이면 종전과 동일하게 동작합니다. openai 2.x로 올라가나 mcp_agent와 공존 검증됨.
>
> **ChatGPT 구독(OAuth) 운용 시(선택)**:
> - `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth`를 해당 배치 cron에 적용하면 그 프로세스의 LLM 호출이 로컬 OAuth 프록시 경유.
> - 토큰: `python -m cores.chatgpt_proxy.oauth_login`(브라우저 로그인) → `~/.config/prism-insight/chatgpt_auth.json`. **ChatGPT Plus 이상 계정 필요**(무료 플랜은 쿼터 부족).
> - 워치독/쿼터: `tools/oauth_healthcheck.py`(헬스 `*/30`), `--quota`(주간/5시간 한도 보고). 알림봇 `OAUTH_ALERT_BOT_TOKEN`/`OAUTH_ALERT_CHAT_ID`.
>
> **Loop A/B/C(선택)**: 기본 SHADOW(관측 전용, 주문 없음). 실거래 전환은 env 게이트(`LOOP_A_LIVE` 등) + 단계 검증. cron 10분 주기(장중) 권장.

---

## 알려진 제한사항

1. **Loop A/B/C는 SHADOW 기본**: Loop A는 env 게이트로 LIVE 전환 가능하나 단계적 모니터링 필요. Loop B(50MA)는 cadence-aware 백테스트 순효과 검증 후, Loop C는 신규 KIS TR **소액 왕복 실주문 검증** 후 LIVE 권장.
2. **OAuth/Codex 호환은 비공식 표면 의존**: `x-codex-*` 쿼터 헤더·Codex 모델/파라미터 정책은 비공식이라 변경 시 영향 가능. 워치독/쿼터 모니터로 사전 감지하나, 멀티턴 stateless 전환은 매 턴 전체 input 재전송이라 토큰 비용이 소폭 증가합니다(저빈도 결정 경로라 영향 제한적).
3. **OAuth는 구독 한도 내**: ChatGPT Plus 주간/5시간 한도를 초과하면 429. 전 배치 OAuth 운용 시 쿼터 모니터로 관측 필수.
4. **#338·#339 US 시즌수익**: KIS 결제 타이밍·환율 반영 기준 변경으로, 과거 표기와 수치가 달라질 수 있습니다.

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.15.0 — 실시간 리스크 루프 + ChatGPT 구독 기반 AI

이번 릴리즈는 "배치 사이의 공백"을 메우는 실시간 방어 장치와,
구독(ChatGPT Plus)만으로 AI를 돌리는 인프라가 핵심입니다.

🛡️ 1) 실시간 리스크 관리 루프 3종 신설 (Loop A/B/C)
오전·오후 배치 사이 장중에 급변이 와도 다음 배치까지 무방비였던 공백을
메웁니다. Loop A(파국적 손절을 10분 주기로 즉시 방어), Loop B(50일선
이탈을 '종가 확인'으로만 인정해 휩쏘 회피), Loop C(미체결 주문 추격).
모두 기존 매도 경로를 그대로 써 일관되며, 안전하게 관측(SHADOW) 모드로
먼저 출시했습니다.

🔑 2) ChatGPT 구독(OAuth)으로 AI 구동
API 종량과금 없이 ChatGPT 구독만으로 전 분석 배치를 돌릴 수 있는
인프라를 도입했습니다(공급자 비종속 LLM 계층 + OAuth 프록시). 구독
백엔드 호환 이슈(특정 모델·파라미터·멀티턴 툴콜)를 해결하고, 주간
사용 한도를 미리 알려주는 쿼터 모니터와 헬스 워치독까지 갖췄습니다.

🚨 3) 매도/청산 안전성 강화
상장폐지·공개매수 등 법인이벤트 강제청산 판단을 더 확실하게,
매도 직전 최종 점검을 더 견고하게 다듬었습니다.

💵 4) 미국 시즌 수익률 정확도 개선
미국 계좌 시즌 수익률을 결제 정합 총자산·달러 기준으로 정정했습니다.

🔧 그 외: 코드 품질 자동 점검(CI) + 품질 배지, 비트코인 6시 리포트에
'왜 지금 사지 않는지' 판단근거 상세화 등 다수 개선.

📊 모든 주식 매매 로직은 5인 투자 거장(오닐·미너비니·드러켄밀러·버핏·퀀트)
관점으로 검토해 합의된 부분만 반영했습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.15.0 — Real-time Risk Loops + ChatGPT-subscription AI

This release centers on real-time defenses that fill the gaps between
scheduled batches, plus infrastructure to run the AI on a ChatGPT
subscription alone.

🛡️ 1) Three new real-time risk loops (Loop A/B/C)
Between the morning/afternoon batches, intraday shocks used to go
unmanaged until the next run. Loop A guards catastrophic stop-losses on a
10-minute cadence; Loop B exits 50-day-MA breaks only on *closing
confirmation* (avoiding whipsaws); Loop C chases unfilled orders. All
reuse the existing sell path for consistency and ship in SHADOW
(observe-only) mode first.

🔑 2) Run the AI on a ChatGPT subscription (OAuth)
New infrastructure lets all analysis batches run on a ChatGPT
subscription instead of metered API keys (provider-agnostic LLM layer +
OAuth proxy). We fixed subscription-backend compatibility (certain
models, parameters, multi-turn tool calls) and added a weekly quota
monitor plus a health watchdog that flags limits before they're hit.

🚨 3) Stronger sell / forced-exit safety
More decisive forced-exit on corporate events (delisting, tender offers)
and a more robust final pre-sell check.

💵 4) More accurate US season returns
US season P&L is now computed on settlement-coherent, USD-denominated
total assets.

🔧 Also: automated code-quality checks (CI) + badges, and the Bitcoin 6pm
report now explains *why it's holding off* in detail — among many fixes.

📊 All stock trading logic was reviewed through 5 investing masters
(O'Neil · Minervini · Druckenmiller · Buffett · Quant) — only consensus adopted.
```

---

**Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>**
