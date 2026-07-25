# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1C Task 17 — point-in-time research engine
- Branch: `prism-insight/t_47846a70-prism-phase-1c-task-17-pit-research-engi`
- Base: `origin/main` at `e145e0d55fff7099c78612437f34454921a6fc4b`
- Implementation state: focused foundation, explicit CI discovery, TDD remediation, independent read-only review, and definitive local gates are complete; delivery remains
- Runtime state: dormant research/backtest foundation only; no application caller, persistence, paper ledger, `OrderIntent`, model/provider transport, messaging, account, broker, or operational behavior is wired

## Implemented scope

- Added point-in-time universe snapshots with separate effective and availability boundaries and rejection of current-constituents-only performance evidence.
- Bound every signal's declared source boundary to an actual security/snapshot/bar-end/availability record and reject duplicate universe effective/availability boundaries.
- Added next-bar-open research fills, prohibited same-close fills, and fail-closed future-data and unavailable-terminal-mark checks.
- Added explicit injected commission, sell tax, spread, and slippage assumptions with deterministic `Decimal` arithmetic.
- Added separate SWING_V1/TREND_V1 cash, position, realized/unrealized PnL, transaction-cost, NAV, and consolidated accounting.
- Added explicit share-changing actions, cash dividends, positive and zero-recovery delistings, post-delisting fill rejection, and corporate-action availability gating.
- Added walk-forward/sealed-OOS contracts and monotonic strategy/calendar-window exposure across refreshed data vintages.
- Added deterministic config hashing plus data-snapshot and code-SHA experiment provenance.
- Added explicit Python 3.10/3.11/3.12 CI discovery for `tests/research` without weakening existing jobs.

## Contract decisions and deferred seams

- Every cost and starting-cash assumption is injected research configuration; there are no production or market-specific defaults.
- Raw prices remain explicit. Corporate-action effects are ledger events rather than silently adjusted-price transformations.
- A zero-recovery delisting bypasses the ordinary positive-price cost model through an explicit zero-value forced exit, preserving total-loss representation.
- OOS exposure is strategy-scoped and calendar-window-scoped, not snapshot-vintage-scoped; once observed it cannot be relabelled fresh within the registry store.
- Durable registry persistence, runtime composition, provider/model transport, internal paper, and application wiring remain deferred to later tasks.

## TDD and verification checkpoint

- Observed vertical RED→GREEN for next-bar execution, future-data/current-universe traps, cost modeling, strategy/consolidated accounting, corporate actions/delistings, experiment provenance/OOS exposure, public exports/CI discovery, refreshed-vintage OOS contamination, zero-recovery delisting, terminal-mark availability, source-record binding, corporate-action availability, and duplicate-universe rejection.
- Current focused suite: `python -m pytest tests/research -q` — 18 passed.
- Definitive local gates passed against the handoff-inclusive tree: compileall; broker audit (22 inventoried legacy findings, 0 violations); all checked-in exact CI groups totaling 908 passed with 1 intentional deselection; `pip check`; diff check; and private/secret/dangerous-pattern checks.

## Independent review

- Verified Claude Code read-only review used only `Read`, returned exit 0 and empty stderr, and initially found blocking zero-recovery-delisting and terminal-mark-availability defects.
- Accepted findings were reproduced with focused red tests and fixed without weakening the positive-price signal cost model.
- Verified targeted follow-up review returned exit 0, empty stderr, `MERGEABLE`, and no new CRITICAL/HIGH/MEDIUM defect; it also confirmed the refreshed-vintage OOS remediation.
- A second recovery review identified source-record binding, corporate-action availability, duplicate-universe ambiguity, and execution/terminal horizon gaps. Focused regressions resolved each finding; a verified read-only follow-up returned `MERGEABLE` with no new CRITICAL/HIGH/MEDIUM defect.
- Non-blocking residuals: registry exposure is in-memory until the later durable research store, `TradeCosts` lacks constructor-level invariant validation, reverse-split ratio semantics are caller-convention-based, and implementation shortfall is embedded in execution PnL rather than separately surfaced in book transaction-cost reporting.

## Side effects and safety

- Git/GitHub delivery and read-only Claude reviews are the only network activity through this checkpoint.
- No live PRISM model/provider request, external message, credential read/change, account/broker/order call, `OrderIntent`, user database access, migration, deployment, runtime activation, or live-trading effect occurred.
- Task 17 foundation PR #29 was squash-merged as `7d6ea999d148fce9b0e2436a32d86397b9fdf099`; the final review-remediation commit/PR remains to be delivered.

## Remaining closeout

1. Commit, push, open the review-remediation PR, and verify exact-head Python 3.10/3.11/3.12 CI includes the explicit research step in every job.
2. Squash merge the remediation only when every required gate is provable; verify post-merge CI, merge SHA, expected files, and remote branch deletion.
3. Complete the Kanban task with delivery evidence; successor work remains separately bounded.

Stop conditions remain: block on compatibility change, destructive migration, provider/credential/account/broker/live/risk scope, conflict, unresolved HIGH/CRITICAL review, or an unverifiable required gate.