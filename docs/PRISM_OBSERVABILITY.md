# PRISM Trading Observatory

> 데이터 생성부터 ClickStack 적재, 대시보드, AI 검증 루프까지 이어지는 한국어
> 설명은 [PRISM Logging Intelligence Architecture](LOGGING_ARCHITECTURE_ko.md)를
> 참고하세요.
>
> 진입품질은 별도 SHADOW 시스템이 아니라 기존 관측 원장을 먼저 확장합니다. CAPTURE에서
> 구체 규칙 SHADOW와 LIVE까지의 단계 계약은
> [Entry Quality Evolution](ENTRY_QUALITY_EVOLUTION_ko.md)을 참고하세요.
> 반복 가능한 분석 절차, exact join, 결측·체결·표본·holdout 기준은
> [Entry Quality Data Analysis Harness](ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md)를
> 따릅니다.

## Contract

Trading processes append versioned JSON events to `logs/prism_events.jsonl`.
The append is fail-open and performs no network I/O. A separate shipper converts
events to OTLP/HTTP and advances its checkpoint only after a successful batch.

Required correlation fields:

- `event_id`, `event_type`, `timestamp`
- `trace_id`, `span_id`, optional `parent_event_id`
- optional `decision_id`, `position_id`
- `git_sha`, `policy_version`, `config_hash`
- `market`, `ticker`, `service`, `environment`

Sensitive attribute keys such as account, token, password, secret, cookie, and
authorization are replaced with `[REDACTED]` before the local append.

## Failure boundary

- ClickStack, network, tunnel, or shipper failure never blocks trading.
- Failed batches do not advance the checkpoint.
- One corrupt JSONL record is skipped and cannot block later records.
- ClickStack is not queried from any buy, sell, hard-stop, or fill path.

## Operations watchdog (maintenance chat)

`tools/ops_watchdog.py` (db-server cron, every 5 minutes, added 2026-10-21 after US re-entry
orders failed silently from 10/13 to 10/20) reads the job logs incrementally and sends one grouped
message to the private maintenance chat (`prism_core.ops_alert`):

- `[장애]`: Traceback/CRITICAL, failed sell/amend/cancel orders, a market a loop skipped (trading
  context or holdings failed), failed re-entry orders, a loop with no completed run inside its session
  window, a daily job past its deadline.
- `[주의]`: data a check could not get (price, daily bars, live regime, sell facts) and other ERROR lines.
- The same signature is held for 6 hours, then re-sent with its count; a stopped job that runs again
  sends `[복구]`; a daily summary at 08:30 KST lists runs and re-entry signals → orders. No morning
  summary means the watchdog itself is down.

Jobs and their heartbeat lines are listed in `JOBS` (the loops' own summary lines, pinned in
`tests/test_ops_watchdog.py`). Add a job there when a new scheduled job is added. Jobs under systemd
(the B3 worker `prism-b3-ae-shadow@kr|us`, which also runs the LIVE in-slot adds) are read from journald
(`journalctl -u <unit>` after a cursor kept in the state file); its per-minute `b3-ae-worker-v1` line is
the heartbeat. Without a readable journal such a job is skipped, never alerted. Rollback:
`OPS_WATCHDOG_ENABLED=false` or remove the cron line. State: `runtime/ops_watchdog_state.json`.

## Deployment topology

- `db-server`: PRISM pipeline, JSONL spool, shipper, SSH local-forward tunnel.
- `prism-backend`: resource-limited ClickStack container and an independent,
  token-authenticated ClickStack OTel Collector.
- ClickStack UI: backend localhost `18080`, proxied by authenticated Nginx `8443`.
- OTLP/HTTP: backend localhost `14318`, reachable from db-server only through SSH.

The tunnel target is supplied outside Git through
`/etc/prism-observability/tunnel.env` as `PRISM_BACKEND_HOST=...`.
The ingestion token is supplied outside Git through
`/etc/prism-observability/clickstack.env` as `OTLP_AUTH_TOKEN=...` and is also
configured on db-server as `PRISM_OBSERVABILITY_OTLP_TOKEN`.
The shipper sends this exact value in the `Authorization` header, matching the
ClickStack static bearer-token extension contract.
db-server loads the value from `/etc/prism-observability/shipper.env`, which
must remain mode `0600`.
The same environment file holds the dedicated `prism_otel` ClickHouse password;
only its SHA-256 hash is written to the mounted ClickHouse user configuration.
Since 2026-10-18 the XML users carry explicit grants on `default.*` only: `prism_otel`
(collector writer) has SELECT, INSERT, SHOW, CREATE DATABASE/TABLE/VIEW and ALTER TTL,
no DROP/TRUNCATE/SYSTEM; `prism_reader` (dashboard exporter, `tools/clickstack_query.py`)
has SELECT and SHOW only, with its password in `/etc/prism-observability/reader.env` (0600).
Write the XML in place (`cat > file`): it is a single-file bind mount, and ClickHouse
reloads it without a restart.
The mounted XML contains no plaintext password and must be readable by the
ClickHouse process (mode `0644`).

## Trading context ledger

- `deployment.applied`
- `trigger.performance_feedback`
- `market.regime_snapshot` (live on every computed KR/US regime observation)
- `candidate.evaluated`
- `candidate.outcome` (live when 30-day tracking completes)
- `screening.third_slot_shadow_evaluated` (KR weak-regime 2-vs-3 candidates)
- `screening.third_slot_shadow_outcome` (exact 1/3/5/10 trading-day returns)
- `micro_split.shadow_batch_completed` (US analysis/tracking completion, exact batch linkage;
  not translation delivery or confirmed fills)
- `entry.executed`
- `entry.fill_reconciled` (US CAPTURE: 제출과 실제 체결을 구분하는 주문 provenance)
- `exit.executed`
- `holding.evaluated` (every KR/US hold-or-sell review after the runner guard; shares the
  position's `trace_id`, because the holding-decision tables keep only the latest row)
- `broker.order_request` (every KIS order TR from `trading/kis_auth._url_fetch`: accepted,
  rejected, HTTP error or exception; account numbers dropped, `execution_profile_ref` kept;
  acceptance is not a fill)
- `job.run_completed` (run heartbeat for the analysis batches, hard-stop, trend-exit,
  fill-chaser and US pending orders, so a missing run shows up as a time gap)
- `btc.scenario.decision_input` / `intent_committed` / `exchange_call` /
  `settlement_recorded` (bounded summaries of committed `llm_scenario_audit_events`; raw
  exchange order IDs and response bodies stay in SQLite, only `order_id_hash` ships),
  `btc.scenario.notice` (HALTED, MODEL_ERROR, fills… as queued) and
  `job.run_completed` with `job=btc-scenario` (one per runner invocation; the runner's
  stdout status lines carry no timestamp)

`candidate.evaluated`, `entry.executed`, and `exit.executed` use context schema
version 1. The entry scenario keeps the authoritative deterministic market
snapshot, security trend facts, gate findings, score adjustments, and portfolio
slot state. `decision_id` links the candidate to the entry, `position_id` links
entry to exit, and a stable `trace_id` ties the complete lifecycle together.

The exit snapshot uses the live computed market context when available and
falls back to the latest local regime-history row without network I/O. Long
reports and prompts are not copied into ClickHouse; the event retains the
structured decision facts and source identifiers needed for reconstruction.
Every append remains fail-open and happens only after the simulator database
transaction has committed.

US `candidate.evaluated` events may include versioned `entry_quality_context`
when `ENTRY_QUALITY_CAPTURE_ENABLED` is not explicitly disabled. The capture is
enabled by default; setting the variable to `0`, `false`, `no`, or `off` is the
single kill switch. It uses only the existing scenario and local trigger
feedback tables. Missing daily/weekly base or structured event evidence remains
`MISSING`, and broker acceptance remains `SUBMITTED_ONLY` until an authoritative
fill source confirms it.

Entry-quality analysis must be derived with
`tools/build_entry_quality_evidence_packet.py` from a local sanitized JSONL
export. The tool performs no network or database access, excludes legacy and
backfill candidates from prospective evidence, deduplicates event and decision
identities, enforces exact `decision_id`/`position_id` joins, and emits explicit
coverage, missingness, fill, linkage, leakage, cohort, and insufficiency facts.
Future sessions should use `skills/prism-entry-quality-analysis/SKILL.md` rather
than ad-hoc SQL. The packet never enables SHADOW or LIVE behavior.

KR weak-regime third-slot analysis remains outside the candidate-performance
tables so it cannot influence trigger priors or trading prompts. The screening
event stores the unchanged live ranks 1 and 2 beside the counterfactual rank 3.
`tools/track_third_slot_shadow.py` later appends exact 1/3/5/10 KRX trading-day
close, MFE, and MAE outcomes. `tools/build_third_slot_evidence_packet.py`
deduplicates and compares these cohorts without claiming fills or account PnL.

## Trace linkage

One `trace_id` reads a decision end to end. Read it with `tools/clickstack_query.py`
(`ticker <TICKER>`, `trace <trace_id>`; read-only, through db-server); review steps are in
`skills/prism-trade-review/SKILL.md`. The id is
`sha256("trade-trace|<MARKET>|<decision_id>")[:32]` (`observability.trading_context`).

- **Screening → position**: when `candidate.evaluated` is emitted for a `report:<pdf>`
  decision, `screening.candidate_linked` copies the matching
  `runtime/candidate_ledger.sqlite` rows (market, trade date, session, ticker parsed from
  the report name) onto the same trace, timestamped at the screening run.
- **Position lifecycle**: `candidate.evaluated` → `entry.executed` → `entry.fill_reconciled`
  → every `holding.evaluated` → `exit.executed`.
- **Re-entry campaign**: every `reentry_v3.*` watch event (trigger, recheck, live
  entry/skip, exit, campaign end) uses the trace of the stopped-out or skipped original
  decision, falling back to a watch-scoped trace when it is unknown. A live re-entry buy
  is a new position with its own trace. `reentry_v3.live_entry` carries its
  `entry_trace_id`, and that position's context events carry `origin_trace_id`.
- **Buy decision quarterly EPS (2026-10-23)**: `decision_inputs.shadow_captured` (same trace as
  the decision's `candidate.evaluated`) carries `quarterly_eps` (`quarterly_eps_v1`,
  `prism_core/decision_snapshot.quarterly_eps`): `period`, `eps`, `prior_year_period`,
  `prior_year_eps`, `yoy_pct` (only on a positive base), `turnaround` (base <= 0 < eps),
  `change` (`yoy`, `turned_to_profit`, `turned_to_loss`, `loss_continued`, `zero_base`,
  `fell_to_zero`), `currency`, `eps_basis`, `statement_basis`, `source`, `asof`, `status`,
  `missing`. Reported quarters only, taken from the table the report step already collected in
  the same run (KR WiseReport quarterly Financial Summary, period `YYYY/MM`, reported EPS in KRW;
  US yfinance quarterly income statement, period = quarter-end date, diluted EPS in USD). No
  extra fetch at decision time; a block collected after the decision or absent from the report
  run is `MISSING` (`not_in_report_meta`, `collected_after_decision`). Observation only.
- **Re-entry evidence (2026-10-21)**: `reentry_v3.shadow_trigger` and
  `reentry_v3.shadow_near_miss` (the primary rule missed only on its own filters: R/R floor,
  no target, chase, below the level, shakeout volume) carry a `snapshot`
  (`prism_core/decision_snapshot.py`, `decision_snapshot_v1`). It holds MA20/50/200 values,
  distances and 10-bar slopes, the 50-day line flags, ATR14, the 60/252-day high distance,
  50-day volume ratios and RS60. Uncomputable values are `null` and listed in `missing`.
  The events also carry the level basis, previous close, day low, prior attempts and the
  original decision. `reentry_v3.shadow_exit` adds `max_gain_pct`/`max_loss_pct` while held.
  Every signal and near miss is also a `runtime/candidate_ledger.sqlite` row
  (`mode=reentry_v3_signal|reentry_v3_near_miss`). The weekly outcome pass emits
  `candidate.forward_outcome` (MFE/MAE over 5/10/20/30 sessions, `hit_50`, 7/14/30 returns,
  descriptors) on the same trace once the row is final.
- **Pocket Pivot Base observation (2026-10-22, default off)**: with
  `POCKET_PIVOT_TRIGGER_MODE=observe` the KR/US afternoon batches write every detector pass
  (`prism_core/pocket_pivot.py`) as a candidate-ledger row, `mode=pocket_pivot_observe`,
  `trigger_type="Pocket Pivot Base"`, `selected=0`, `trade_date` = the completed signal session
  (features: pivots, structural stop, depth, dry-up, kind, rank; `earnings_within_5d` stays null).
  The same block is in the trigger JSON as `metadata.pocket_pivot_observe`. It never feeds selection.
- **Steady Leader Pullback (T-B, 2026-10-22, default off)**: with
  `STEADY_LEADER_TRIGGER_MODE=observe` the KR/US afternoon batches write every detector pass
  (`prism_core/steady_leader.py`) as a candidate-ledger row, `mode=steady_leader_observe`,
  `trigger_type` = the market's trigger key (KR "꾸준 상승 주도주 눌림", US "Steady Leader Pullback"),
  `selected=0`, `trade_date` = the completed signal session (features: MAs, 252-session high,
  pullback date, steadiness, 120-session return, natural stop MA20 x 0.99, rank). With `live`
  the rows use `mode=steady_leader_live` and the one pick has `selected=1`; a regular pick it
  replaced loses its `selected` flag in the afternoon rows. The block, including the live
  decision (`applied`, `reason`, `displaced_ticker`, `max_selections`), is in the trigger JSON as
  `metadata.steady_leader`.
- **Broker fill confirmation (2026-10-21)**: `tools/reconcile_fills.py --market KR|US`
  runs after the close. It reads each BUY in `order_intents`/`broker_orders` for that
  market-local day (read-only), queries the sending account's daily executions (KR
  TTTC0081R, US TTTS3035R; inquiry only), and appends `entry.fill_reconciled` on the
  trade trace. Micro-split adds use the entry decision's trace, found through
  `positions.entry_intent_id`. The event carries `fill_status` CONFIRMED / PARTIAL /
  NOT_FILLED / MISSING, filled and ordered quantity, average fill price, `fill_vs_limit_pct`
  and `match_basis` (order number, amend chain, or a unique symbol/quantity/time match).
  It also carries a `fill_provenance` that the evidence packet reads (NOT_FILLED →
  CANCELLED, MISSING → UNKNOWN). A failed or ambiguous inquiry stays MISSING. KIS gives
  no per-execution time, so `confirmed_fill_at` stays null.
  `runtime/fill_reconciliation_state.json` prevents a final status from being emitted
  twice. Suggested cron (KST): `10 16 * * 1-5 … --market KR`, `10 7 * * 2-6 … --market US`.
- **BTC**: `btc.scenario.*` share `sha256("scenario:<id>")`. A `decision_input` joins the
  scenario trace when its run touched exactly one scenario (`scenario_inferred=true`).
- **BTC replay observations**: new `btc.research.source` account events may include
  `ticker_observation` with only BTCUSDT Mark, Last, bid, ask, and the local HTTP request/response
  times plus Bybit's response timestamp. `exchange_call` events may include `exchange_observation`
  with local wrapper times and hashed order references. The wrapper starts before the final guard;
  neither timestamp is an exchange fill time, and proximity alone does not link a quote to an order.
  Clock regression, missing prices, or crossed quotes remain partial/unknown. The exporter does not
  backfill historical account or audit events; this is prospective evidence, not fill or P&L proof.
- Not linked by design: `broker.order_request` (HTTP layer; join by ticker, time and
  `execution_profile_ref`) and `job.run_completed` (run level).

## Historical baseline

tools/backfill_observability.py backfills only verifiable facts:

- realized KR/US rows from the production trading-history tables
- completed watched-candidate 7/14/30-day outcomes
- recorded regime snapshots
- actual db-server pull timestamps from Git reflog

Every row is marked ingestion_mode=backfill, includes its source table or
reflog provenance, and receives a deterministic event ID. Historical prompts,
gates, and decision traces are not reconstructed.

## Curated dashboard snapshot

tools/export_observability_insights.py aggregates ClickHouse events into a
credential-free JSON snapshot. tools/publish_observability_insights.py
publishes it atomically to app-server every five minutes through the systemd
timer template.

The publisher uses a dedicated SSH identity and the unprivileged prism account
on app-server. Host, port, user, identity path, and destination stay outside Git
in /etc/prism-observability/dashboard-export.env.

The existing dashboard reads /observability_insights.json independently from
its portfolio JSON. Missing or delayed observability data hides only the new
panel and never breaks the existing dashboard.

## Rollback

1. Stop and disable `prism-observability-shipper` and tunnel units.
2. The trading pipeline continues; local event appends remain harmless.
3. Stop ClickStack with `docker compose down` without deleting its volumes.
4. Remove the Nginx `8443` site and firewall rule if external UI access is no
   longer needed.
