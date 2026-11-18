---
name: prism-trading-change-review
description: Review any PRISM-INSIGHT KR/US stock trading change before implementing it — screening, triggers, regime, entry, exit, stop-loss, sizing, micro-split adds, re-entry, runner hold, or BUY/SELL/holding prompt edits. Use for requests such as "이 규칙 바꿔줘", "손절 넓혀볼까", "새 트리거 넣자", "매수 프롬프트 고쳐줘", "이 전략 접목해보자", or any proposal that changes an economic trading decision. Not for BTC (use prism-btc-change-review) or pure data analysis (use prism-entry-quality-analysis).
---

# PRISM Trading Change Review (KR/US)

This skill runs the review in `docs/TRADING_CHANGE_REVIEW_HARNESS.md`. It does not replace it:
read the sections named below. Development-time only; never add a runtime component.

## 0. Classify first

- **Proven implementation defect** (ledger/order mismatch, wrong column, wrong alert channel):
  fix with a regression test and the safety gates in `docs/SERVER_GIT_OPERATIONS_ko.md`. Do not
  delay it behind strategy research.
- **Economic decision change** (what we buy, when, how much, when we sell): continue below.
- Both: split them into separate changes.

## 1. Before touching code

1. Read `docs/HARNESS_CORE_ko.md` (north star, lines not to cross), `docs/RESEARCH_LESSONS_ko.md`
   and `tasks/lessons.md`. If the question was already answered there, say so instead of re-testing it.
2. Read the harness sections "Strategy adoption and fit gate" and "Required sequence".
3. **Ask the user** about anything that sets direction: the hypothesis definition, target
   population (e.g. stocks PRISM already analyzed vs the whole market), time axis, success
   criterion, how it would be applied. Do not fill these with textbook defaults.
4. If the user asked to "compare", run a small one-off comparison now. Do not build SHADOW.

## 2. Trace the real path (not just the prompt)

Follow one candidate end to end and note file:line and the final authority at each step:

| Step | KR | US |
|---|---|---|
| Screening / trigger | `trigger_batch.py` | `prism-us/us_trigger_batch.py` |
| BUY decision prompt | `cores/agents/trading_agents.py` | `prism-us/cores/agents/trading_agents.py` |
| Regime | `cores/market_pulse.py` → `cores/regime_policy.py` | same shared modules |
| Deterministic gates | `cores/buy_gate.py`, `prism_core/entry_score_policy.py`, `prism_core/sector_cap.py`, `prism_core/trading_scenario_contract.py` | same shared modules |
| Order / sizing | `prism_core/order_budget.py`, `prism_core/slot_weight.py`, `prism_core/micro_split*.py`, `prism_core/add_plan*.py`, `prism_core/b3_ae_*.py`, `tools/run_b3_ae_shadow.py`, `tools/run_micro_split_add.py` | same |
| Hold / sell | `stock_tracking_agent.py`, `tools/trend_exit_seller.py`, `tools/hardstop_seller.py`, `prism_core/sell_regime_context.py`, `prism_core/runner_hold*.py` | `prism-us/us_stock_tracking_agent.py` + shared |
| Re-entry | `prism_core/reentry_v3_live.py`, `prism_core/stopout_reentry.py`, `prism_core/pivot_reentry.py` | same |

Check: same trade assumptions across steps (holding period, intraday vs close, target vs
trailing), field names/units/dates, duplicated rewards or penalties, fallback order.
Mark any path you did not read as unverified.

## 3. Prompt edits

Apply the harness section "Prompt framing and logical-consistency review" including items 8–11:
unfold the assembled prompt in order, write each rule as condition → result, compare with code
constants and execution times, then grep the same contradiction type in BUY · SELL · re-entry ×
KR · US × ko · en. Ask the user about any contradiction whose fix changes direction.
Pin numbers that must match code in a test (pattern: `tests/test_sell_prompt_consistency.py`).
Update reviewed prompt-hash tests with a date and reason comment.

## 4. Record before implementing

Write the six-item record ("구현 전 남길 최소 검토 기록") into the plan or PR body: change type and
baseline; hypothesis, regime, falsifier; fit with existing strategy and the step-by-step trace;
same-condition comparison, counterexamples, holdout, sample size; how to tell causes apart and
stop/keep/return conditions; current verdict (keep / hold / preregistered comparison / SHADOW
review / LIVE review).

## 5. Validate

- Before any strategy experiment: confirm the replay tool reproduces actual live performance,
  compute the minimum detectable effect from the sample size, collect a wide candidate list and
  test only the top of it, and judge in two stages (past replay, then small live money).
- Judge outcomes by max/min excursion and big-winner capture, keeping average win and loss
  separate; fixed 7/14/30-day closes alone distort the picture.
- If the rule is approved by an LLM in production, judge it with an LLM replay, not a
  deterministic replay alone.
- Deduplicate virtual samples by (market, ticker, rule, date) before aggregating.
- When the change alters a rule the full-ops research engine copies (first size, adds, pyramid rows,
  re-entry, sell lines, slots, batch times, LIVE flags), update the engine and rerun its `revalidate.sh`
  in the same task (harness "Required sequence" step 8; `tests/test_research_engine_contract.py` fails until then).
- Add one rejected counterexample and one retained valid example as tests. Verify unrelated
  triggers are unchanged.
- Log a stable reason code. Keep rollback to one flag or one small change.
- SHADOW/LIVE only with explicit user approval. Report deploy verification and the first
  scheduled run separately.

Finish with a short Korean report: conclusion, key numbers, next action.
