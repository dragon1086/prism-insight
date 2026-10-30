# AGENTS.md — PRISM-INSIGHT (Codex · Claude Code 공통)

This is the single shared guide for every coding agent in this repository. Codex reads it directly;
Claude Code imports it from `CLAUDE.md` (`@AGENTS.md`). Put rules that both agents need **here**, and
only host-specific notes in `CLAUDE.md`. Detailed procedures live in the linked docs and skills —
keep this file to "what to always know" and "where to look first".

- Skills: `skills/` (Codex sees `.agents/skills/`, Claude sees `.claude/skills/`; both are links)
- Hooks: `.codex/hooks.json` (trust once with `/hooks`) and `.claude/settings.json`, both calling `tools/harness/hook.py`
- `tests/test_harness_layers.py` checks that these layers still agree.

## Harness charter (read first)

<!-- HARNESS_CORE:BEGIN (generated from docs/HARNESS_CORE_ko.md; edit that file) -->
# PRISM 하네스 헌장 (Claude·Codex 공통)

사용자와 에이전트가 같은 방향으로 개발하기 위한 최상위 약속이다. 세부 절차는 아래 표의 원문 문서에
있고, 이 헌장은 그 문서들을 언제 읽어야 하는지와 어겨서는 안 되는 선만 담는다. 원문과 충돌하면 더
보수적인 쪽을 따른다. 이 블록은 `AGENTS.md`에 그대로 복사되며, `tests/test_harness_layers.py`가
두 사본이 같은지 검사한다(고칠 때는 이 파일을 고치고 `python tools/harness/sync_agents_core.py` 실행).

## 북극성 — KR·US 주식 (사용자 정의, 2026-10-04)

- 오닐식 추세추종이다. 손익비를 관리하면서 **크게 가는 소수 종목**으로 계단식 성장을 한다.
- 초분할·재진입은 낙폭을 관리하면서 가는 종목을 놓치지 않고 올라타기 위한 수단이다.
- 핵심은 스크리닝과 좋은 종목을 사는 눈의 정확도다. 손절만 반복되는 빈도는 계좌를 녹인다.
- 단타 기준은 쓰지 않는다. 털린 종목은 다시 강해지면 재진입해 큰 흐름을 탄다. 다음 개선의 중심은 재진입이다(2026-10-07).
- 모든 변경·검증·보고는 큰 수익 종목 포착과 비중 확대, 손실·낙폭, 손절 비용, 스크리닝 정확도로 판단한다.
- BTC의 목표는 따로 있다: 실행·측정을 믿을 수 있고 비용 차감 후 개선을 입증하는 시스템(`docs/BTC_ROADMAP_ko.md` 1절).

## 함께 일하는 방식

- 사용자 정정(2026-10-25): 이미 승인된 작업은 필요한 확인·실행·검증까지 이어간다. 같은 범위의 진행을 위해
  사용자가 "계속"을 반복하게 하지 않는다. 새 위험 수용처럼 별도 결정이 필요한 경우만 구체적인 이유와 선택지를 제시한다.
- 실행 복구와 독립적인 과거 자료 분석·오프라인 수익성 연구는 함께 진행한다. 전략 배포의 선행 조건을
  연구 착수의 선행 조건으로 확대하지 않는다. 하위 에이전트 사용 금지와 작업 범위·중단 지시는 별도로 준수한다.
- 자동화는 등록·예약, 실제 시작, 종료 결과, 후속 관측을 구분한다. 등록 성공만으로 "자동 진행된다"거나
  "다시 요청할 필요 없다"고 보장하지 않는다. 미실행·실패를 확인하면 기존 승인을 재사용해 직접 복구한다.
- 비용을 아끼기 위해 기존 자료·검증을 재사용하고 변경 부분만 로컬 테스트하며 전체는 PR CI에 맡긴다.
  sleep/wait 반복 폴링과 결과 없는 반복 조회로 대기하지 않는다. CI 진행 중에는 독립 작업을 진행하고,
  이어서 실행할 수 없으면 미완료 상태와 실제 재개 경로를 명시한다. 새 증거 없이 실패한 실험을 반복하지 않는다.
- PR은 의존 순서·최신 main·다른 세션의 변경을 먼저 확인하고 관련 수정과 검증을 모아 push한다. 대기열 정리는
  자신이 만든 PR의 대체된 커밋으로 한정한다. 후순위 Draft의 최신 검사를 중단하면 미완료로 기록하고,
  병합 전 정확한 최신 head의 모든 필수 CI를 다시 통과시킨다. 다른 세션의 작업은 취소하지 않는다.
- BTC 목표 작업은 운영 DB 전체 복사·VACUUM·대용량 연구 산출물을 새로 만들지 않는다. 디스크 여유와
  작업 중 필요한 최대 임시 공간을 양쪽 서버에서 확인한다. DB 논리 크기·실제 할당·재사용 페이지를 구분하고,
  기존 백업과 제한된 집계를 우선 이용하며 정기 백업과 7일 정리의 실행 여부를 별도로 검증한다.
- 방향이 갈리는 결정(가설 정의·대상 종목·시간축·성공 기준·적용 방식, 완화 vs 강화)은 실행 전에 사용자에게 묻는다.
- "비교해봐"는 지금 몇 건으로 하는 일회성 비교다. 비교 요청을 SHADOW·운영 코드로 넓히려면 먼저 한 줄로 확인받는다.
- 전략 실험 전에 `docs/RESEARCH_LESSONS_ko.md`에서 같은 질문의 결론이 있는지 본다. 실험 설계: 재현 도구가 실제 성과를 맞추는지 먼저 확인,
  최소 검출 효과를 표본으로 사전 계산, 후보를 넓게 비교, 과거 재현 → 소액 실전 2단계로 판정. 검증 모집단은 그 신호를 쓸 종목 집단으로 정하고,
  운영 규칙의 입력 출처(손절 후·보류 후 등)를 표본이 모두 덮는지 확인하며, 가상 표본은 (시장·종목·규칙·날짜) 중복부터 없앤다.
  성과는 고정 7·14·30일 종가가 아니라 최고·최저 수익률도 본다.
- 수익률은 한 개의 평균(예: "매매당 평균 +0.04%")으로 보고하지 않는다. 왜곡된다. 항상 **승률·건수, 평균 수익률(이긴 매매),
  평균 손실률(진 매매), 손익비(평균 수익 ÷ |평균 손실|)** 로 나눠 쓴다. 전체 평균은 필요하면 부록에만 둔다(사용자 2026-10-22).
- 매매 규칙은 KR·US를 같은 방향으로 바꾼다. 한쪽에서 정한 결정을 다른 쪽에서 다시 묻지 않는다.
- 사용자 보고는 한국어, 쉬운 투자자 용어로 짧게(결론·핵심 숫자·다음 행동) 한다. 내부 약어는 풀어 쓴다.
  사용자는 모바일로 보므로 결과 본문을 대화에 직접 붙이고, 파일 경로는 덧붙이는 용도로만 쓴다.
- 매매 사실(왜 샀나·팔았나, 어떤 점수·국면·관문이었나, 체결됐나)은 ClickStack 원장을 먼저 조회해 답한다.
  매매 한 건은 `trace_id` 하나로 스크리닝부터 매도까지 이어진다(`tools/clickstack_query.py`). 원장에 없으면 MISSING이다.

## 넘지 않는 선

1. SHADOW·LIVE를 자동으로 켜지 않는다. LIVE는 사용자의 명시적 승인이 필요하다. BTC는 Bybit 데모이며 실자금은 별도 승인이다.
2. `MISSING`(모름)을 0·실패·통과로 채우지 않는다. 판단 당시 없던 값으로 과거를 복원해 일치했다고 말하지 않는다.
3. 한 거래·한 트리거의 결과를 반례 검증 없이 전역 차단 규칙이나 프롬프트 벌점으로 올리지 않는다.
4. 한 번에 한 결정 계층만 바꾼다. 같은 근거를 스크리닝·게이트·프롬프트에 중복으로 넣지 않는다(각 층에 측정된 별도 역할이 있을 때만 예외).
5. 운영에서 LLM이 승인하는 규칙을 결정론 재생만으로 채택·기각하지 않는다.
6. 뉴스·테마는 참고 자료다. 매수·보유·매도 규칙의 근거로 쓰지 않는다.
7. 매매 프롬프트를 바꾸면 형제 프롬프트(BUY·SELL·재진입 × KR·US × ko·en)와 실제 코드를 대조한다. 방향이 갈리는 모순은 사용자와 정한다.
8. 테스트 통과는 운영 검증이 아니다. 배포 확인과 첫 정기 실행 확인을 따로 보고한다.

## 상황별로 먼저 읽을 문서

| 상황 | 먼저 읽기 | 스킬·도구 |
|---|---|---|
| KR·US 스크리닝·국면·진입·청산·비중 변경, 새 전략·트리거 | `docs/TRADING_CHANGE_REVIEW_HARNESS.md`(전략 도입 검토·최소 검토 기록 6항목), `docs/RESEARCH_LESSONS_ko.md` | `prism-trading-change-review` |
| BUY·SELL·보유·재진입 프롬프트 변경 | 위 하네스의 "Prompt framing and logical-consistency review" 절 | `tests/test_sell_prompt_consistency.py` 방식 |
| 매수품질·트리거 비교·재생·승격 검토 | `docs/ENTRY_QUALITY_EVOLUTION_ko.md`, `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` | `prism-entry-quality-analysis` |
| BTC 전략·실행·백테스트·위험·배포 | `docs/BTC_ROADMAP_ko.md`(마일스톤 ID, 5절 개발 규칙, M5 최소 게이트) | `prism-btc-change-review` |
| 뉴스·테마 작업 | `docs/THEME_NEWS_ROADMAP_ko.md` | |
| 배포·장애 수리 | `docs/SERVER_GIT_OPERATIONS_ko.md`의 변경 범위별 검증 게이트 | |
| 2주 정기 점검 | `docs/TWO_WEEK_REVIEW_ko.md`, 하네스의 프롬프트 주기 점검 | `tools/two_week_review.py` |
| 매매 복기, 특정 종목·포지션의 판단 경과 | `docs/PRISM_OBSERVABILITY.md`의 "Trace linkage" | `prism-trade-review`, `tools/clickstack_query.py` |
| 책·외부 지식을 읽고 우리 로직에 녹이기, 예전에 읽은 지식 찾기 | `docs/knowledge/INDEX_ko.md`, `docs/knowledge/README_ko.md`(가공 원칙·카드 양식) | `prism-knowledge-intake` |
| 새 가설·관측 등록, 지금 LIVE·SHADOW 상태 확인 | `docs/HYPOTHESIS_REGISTRY_ko.md`(등록일 기록, 2주 점검 때 재측정) | `tools/feature_status.py` |

## 이 헌장이 들어오는 길

공용 지침은 `AGENTS.md` 하나다. Codex는 그대로 읽고, Claude는 `CLAUDE.md` 맨 위의 `@AGENTS.md`로 읽는다
(둘 다 있으면 Claude는 `CLAUDE.md`만 읽으므로 이 가져오기가 없으면 `AGENTS.md`가 안 보인다). Codex는 가져오기를
못 하므로 이 헌장은 `AGENTS.md`에 사본으로 들어가고, 고칠 때는 이 파일을 고친 뒤 `tools/harness/sync_agents_core.py`를
실행한다. 매매·BTC·복기·분석·배포 관련 질문이나 매매·BTC 파일 수정 때는 훅이 요약을 다시 넣는다(`tools/harness/hook.py`,
막지 않음, Claude는 대화 압축 뒤 전체 요약을 다시 보여 준다). 스킬은 `skills/`에 있고 `.claude/skills/`와
`.agents/skills/`가 같은 폴더를 가리킨다. 규칙 내용은 원문 문서에서만 고치고, 감지 단어·매매 파일 목록은 `hook.py`에서 조정한다.
<!-- HARNESS_CORE:END -->

## Project

PRISM-INSIGHT is an AI stock analysis and automated trading system for Korea (KR) and the US,
plus a separate BTC lane on Bybit demo (`prism-btc/`). Python 3.10+, mcp-agent, OpenAI models
(defaults in `report_model_config.py`; trading/bot models at their call sites — read the call site,
not docs, before changing a model), SQLite, Telegram, KIS broker API.

```
trigger_batch.py / prism-us/us_trigger_batch.py      surge·momentum screening → candidates JSON
  → stock_analysis_orchestrator.py (KR) / prism-us/us_stock_analysis_orchestrator.py (US)
      data prefetch → 6 analysis agents in order (technical, trading flow, financial, industry,
      news, market) → investment strategist → PDF report → Telegram summary (Korean)
  → stock_tracking_agent.py / prism-us/us_stock_tracking_agent.py   BUY/SELL decision → KIS order
      every account in trading/config/kis_devlp.yaml; Telegram report from the primary account only
Trend-following tools around the core loop (north-star mechanisms):
  micro-split entry/adds  tools/run_micro_split_add.py, prism_core/micro_split*.py, prism_core/add_plan*.py
  re-entry signal (v3)    tools/run_reentry_v3_entry.py, prism_core/reentry*.py
  runner hold             prism_core/runner_hold*.py
  hard stop / trend exit  tools/hardstop_seller.py, tools/trend_exit_seller.py
```

Which gates are LIVE/SHADOW/OFF on a server: `python tools/feature_status.py` (reads `.env` and
crontab; `docs/FEATURE_FLAGS.md` is the intended-state registry and can lag). Agent roles and
prompts: `docs/CLAUDE_AGENTS.md`. Release notes: `docs/RELEASE_NOTES_v*.md`.

## Repository map

- `cores/` analysis engine, report generation, `cores/agents/` agent prompts, `cores/chatgpt_proxy/` OAuth proxy
- `prism_core/` shared trading policy modules (scoring, sizing, re-entry, runner hold, sector cap …)
- `trading/` KR broker integration · `prism-us/` US mirror (own `cores/`, `trading/`, `tests/`)
- `tracking/` journal, memory, trading state · `messaging/` Redis / GCP Pub/Sub signal publishing
- `tools/` batch runners, sellers, research and review tools · `tests/` regression tests · `docs/` design and harness docs

## Commands

```bash
pip install -r requirements.txt && python3 -m playwright install chromium   # setup
python stock_analysis_orchestrator.py --mode morning --no-telegram          # KR run, no Telegram
python prism-us/us_stock_analysis_orchestrator.py --mode morning            # US run
python trigger_batch.py morning INFO / python prism-us/us_trigger_batch.py morning INFO   # screening only
python demo.py 005930 / python demo.py AAPL --market us                     # single report
python prism-us/us_pending_order_batch.py --dry-run                         # US queued orders
python weekly_insight_report.py --dry-run
python -m pytest tests/test_<area>.py -q                                    # focused tests
```

- Run tests through `pytest` only: the root `conftest.py` blocks real signal publishing that importing
  trading modules would otherwise trigger. Ad-hoc scripts that import trading agents must set
  `PRISM_DISABLE_SIGNAL_PUBLISH=1`.
- KR and US packages shadow each other's module names: run `prism-us/tests` in a separate pytest
  process (as `.github/workflows/ci.yml` does).
- Prefer `--no-telegram`, `--dry-run`, demo mode and isolated tests. Avoid production-like runs unless the task needs them.

## Configuration

| File | Holds |
|---|---|
| `.env` | Telegram tokens/channels, Redis/GCP, feature gates, `PRISM_OPENAI_AUTH_MODE` (`api_key` or `chatgpt_oauth`) |
| `mcp_agent.secrets.yaml` | API keys |
| `mcp_agent.config.yaml` | MCP servers |
| `trading/config/kis_devlp.yaml` | KIS credentials, `accounts:` list (first = primary) |

Copy the `*.example` files to set up. Never change or commit real credentials, tokens or secrets, and
never print them in logs or messages.

Troubleshooting (Playwright, fonts, KIS auth, OAuth proxy …): `docs/CLAUDE_TROUBLESHOOTING.md`.

## Code conventions

- **Async**: no blocking network calls (`requests.get`) inside async paths; use the repo's async clients
  (`async with AsyncTradingContext(mode="demo") as trader: await trader.async_buy_stock(...)`).
- **Sequential agents**: keep LLM report sections sequential; do not swap in `asyncio.gather(...)`
  (rate limits and section order matter). Market analysis is cached — keep that when editing orchestration.
- **KIS numbers**: the API may return `''`; use `_safe_float` / `_safe_int` helpers, not direct casts.
- **Korean text**: every Korean report/message uses formal polite style (합쇼체: "~습니다"), enforced in
  `cores/report_generation.py` and each agent instruction. Code comments and logs are English;
  broadcast languages come from the translation agent.
- Keep changes narrow and in the existing pattern — much behavior lives in prompts and orchestration order.
  Preserve prompt/report structure unless the task asks for a redesign.
- Treat logs, PDFs, JSON outputs and SQLite databases as user data unless the task targets them.

## Trading and data safety

- Judge and report every trading change by the north star in the charter above.
- Default to demo. Keep portfolio limits (`MAX_SLOTS = 10`, `MAX_SAME_SECTOR = 3` in the KR/US tracking
  agents) and stop-loss logic (per-trigger `sl_max` in each `TRIGGER_CRITERIA`) unless the task changes trading rules.
- Before changing screening, regime, entry, exit, sizing, adds, re-entry or runner hold, follow
  `skills/prism-trading-change-review/SKILL.md` (source: `docs/TRADING_CHANGE_REVIEW_HARNESS.md`).
  Natural-language proposals ("이 전략 접목해보자") count; no special command is required. The
  compatibility review traces real screening code through report inputs, BUY/SELL prompts,
  deterministic gates, execution and exit management — a concept or prompt-only review is not enough.
- For every BUY/SELL/holding/re-entry prompt change, apply the harness's prompt framing and
  logical-consistency review, including per-stock user prompts (f-strings) and scenario sell triggers.
- Keep execution/data bug repair separate from strategy research, and do not delay a proven safety fix for it.
- KR vs US: data KIS + kospi_kosdaq MCP vs yfinance + sec-edgar MCP; market-cap floor 5000억 KRW vs
  $1B (`US_SCREENING_MIN_MARKET_CAP_USD`); tables `stock_holdings` vs `us_*` (`us_stock_holdings`,
  `us_trading_history`, `us_watchlist_history`, `us_holding_decisions`, `us_pending_orders`).
- US orders outside market hours are reserved orders: buys need `limit_price`; sells use `limit_price`
  or `use_moo=True`. Orders before 10:00 KST are queued for `us_pending_order_batch.py` (10:05 KST).

### BTC

- Before BTC strategy, execution, backtest, risk, observability or deployment work, follow
  `skills/prism-btc-change-review/SKILL.md` (roadmap milestone ID, safety before optimization,
  evidence gates). BTC stays on Bybit demo unless the user separately approves real funds.
- BTC trade notifications follow `docs/BTC_POSITION_MESSAGE_CONTRACT_20260915_ko.md`: verified broker
  margin/leverage/equity with currency and capture time, never strategy capital in place of broker
  figures, never another position's snapshot on an old exit, never delay protection or execution for
  notification enrichment. Public notices stay compact and event-specific (no raw account snapshots or
  repeated unknown fields on exits); keep full diagnostic data, group essential missing-data warnings,
  and never hide unconfirmed settlement status. Missing data stays explicitly unknown.

### News/theme

- Before work on KIS headlines, headline stores, theme maps, theme briefs, `/theme`·`/signal` evidence or
  research using news/theme data, read `docs/THEME_NEWS_ROADMAP_ko.md`, name the step ID (R*/U*) and follow
  its order. News/theme is reference material, never a buy/hold/sell rule.

### Trade review (ClickStack ledger)

- Route "왜 샀어/팔았어", "이 종목 복기", fill checks and any question about what the system decided through
  `skills/prism-trade-review/SKILL.md`: query the ClickStack ledger first with `tools/clickstack_query.py`
  (`ticker`, then `trace <trace_id>`; read-only, via db-server). Use server SQLite or log grep only for
  gaps the ledger does not cover, and keep absent events `MISSING`. One trade is a fact check, not rule evidence.

### Entry-quality analysis

- Route "매수품질 데이터 분석해줘", trigger comparison/replacement, replay and promotion review through
  `skills/prism-entry-quality-analysis/SKILL.md`; build the deterministic Evidence Packet with
  `tools/build_entry_quality_evidence_packet.py` instead of ad-hoc SQL, fuzzy joins or reconstructed fills.
- Strategy-ledger outcomes are independent of broker funding; broker-realized PnL needs confirmed fills.

### Status tables

- Roadmap/registry status changes need verified evidence. Implementation, tests, CI, deployment,
  first scheduled run and outcome proof are separate states. New hypotheses go to
  `docs/HYPOTHESIS_REGISTRY_ko.md` with a registration date; research conclusions go to
  `docs/RESEARCH_LESSONS_ko.md`.

## Git, PR, merge and deployment

- Code changes (`.py`, `.ts`, `.js` …) go through a feature branch from `origin/main` and a PR
  (`feat/`, `fix/`, `refactor/`, `test/`, `docs/`). Docs-only `.md` changes may commit to main.
  Commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`.
- `git stash` is shared by all worktrees: use a WIP commit or `git worktree add --detach <tmp> <base>`
  for base comparisons, never bare stash/pop.
- User instruction (2026-09-06): task-scoped, validated changes may be merged and deployed without a
  separate approval review. Main requires zero approving reviews; keep the PR requirement, CI and
  all other protections. Before merging, check the exact PR head, dependency order, relevant tests and
  that **every** status check is SUCCESS (`gh pr view N --json statusCheckRollup`, including the license job).
- Any feature change, incident repair or deployment first applies the change-scope verification gates in
  `docs/SERVER_GIT_OPERATIONS_ko.md`: clean target, verified commit, ff-only deploy, no destructive
  reset/stash/clean, no credential or runtime-data edits. Record pre-deploy tests, post-deploy smoke,
  and whether the first scheduled run has actually completed.
- The review waiver does not cover failing or unrelated work, new risk-budget defaults, strategy
  promotion, real BTC funds, or unrelated app-server deployments.

## Before finishing

- Run the smallest test or command that validates the change; for a pipeline component, run the
  pipeline (e.g. the batch to its candidate count), not only the function.
- Screening/provider changes also need real-module KR consumer and US morning/afternoon
  batch-to-JSON integration tests with flat/MultiIndex/empty/ambiguous provider fixtures (network,
  orders and channel sends disabled). After deployment, check the production Python and a bounded
  read-only provider smoke before claiming recovery; never rerun live orders as a test.
- If you could not validate, say so and why. Report changed files and any trading, messaging or
  credential risk — in Korean, short, plain investor terms (see the charter).
