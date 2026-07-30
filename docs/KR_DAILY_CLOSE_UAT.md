# KR daily-close SHADOW product UAT

Status: execution checklist; user approval pending

## Safety boundary

This UAT is read-only. It may call approved KIS market-data and fundamental endpoints, public AgentNews, and the existing ChatGPT OAuth model path. It may write local research/ops/paper SQLite files and local report/dashboard artifacts. It must not load an account number, read balances or holdings, call a broker/order/cancel/replace API, send a message, activate a schedule, or create an executable order intent.

## Required command path

The production composition root is:

```bash
python -m prism_app kr-daily \
  --as-of <timezone-aware-now> \
  --research-db <isolated-dir>/research.sqlite \
  --paper-db <isolated-dir>/paper.sqlite \
  --ops-db <isolated-dir>/ops.sqlite \
  --report-output <isolated-dir>/kr-daily.md \
  --dashboard-output <isolated-dir>/dashboard.json
```

Use isolated output files. Never point this UAT at the user's existing databases. A fixed-symbol `product-uat` run or a fixture test is supporting evidence only; it is not proof that the discovered daily candidate traversed this command.

For live mode, `--as-of` is an operator upper-bound check, not a request to reconstruct a historical snapshot. Current-only KIS fundamentals are fetched first and their receipt clock freezes the PIT decision instant. Re-running the command performs a new approved read-only observation and therefore is not expected to report `IDEMPOTENT_REPLAY`. Recovery evidence must replay the same frozen invocation/snapshot identity without another provider fetch; a genuinely new live receipt must retain a new append-only identity.

## Completion matrix

| Gate | Required evidence | Current state |
|---|---|---|
| Candidate discovery | Real KIS/KRX candidate funnel; raw assertions, stable unique identities, no hidden cap | Live partial: the 2026-07-30 01:17 KST invocation retained 14 raw assertions as 10 stable identities with `truncated=0`; the legacy batch source was the visible `NAVER` fallback after KRX failed, while KIS volume-rank context returned HTTP 200. Discovered-candidate analysis did not complete. |
| Candidate identity | Same `security_id` and provider symbol in candidate projection, decision rows, report, and dashboard | Fixture verified. Live fixed-symbol readback verified for provider symbol `214450`; discovered-candidate same-identity traversal remains incomplete. |
| Fundamentals | KIS-primary finance call evidence and visible comparable-pair status/gaps; no silent FMP substitution | Live verified for `214450`: all six KIS finance endpoints returned HTTP 200 at 2026-07-30 08:14 KST, with DART/KIND visibly unavailable and FMP unused. |
| SWING/TREND propagation | Both exact strategy IDs tied to one `data_snapshot_id` per analyzed candidate | Live fixed-symbol verified on snapshot `a9c39489-69da-53f7-a170-8a7b847e1256`; discovered-candidate daily traversal remains incomplete. |
| Score audit | Score/feature IDs, versions, raw/normalized components, weights, exact recomposition, threshold version/comparisons/vetoes | Live fixed-symbol verified: SWING `71.519912`, TREND `43.121888`, both exact recomposition matches; separate score/threshold versions persisted and rendered. |
| Scenario state | Each strategy is explicitly `WATCH`, `NO_ENTRY`, or `ENTRY_CANDIDATE`, or is separately invalid/incomplete/report-only with reasons | Live fixed-symbol verified as SWING `WATCH` and TREND `POLICY_REJECTED` with three named deterministic vetoes. No action state was invented for the rejected strategy. |
| Persistence | Decision snapshot and proposal rows read back before publication | Live fixed-symbol verified: two decision snapshots, two proposals, and 27 field-disposition events read from the isolated research DB. |
| Report/dashboard | Existing report and dashboard contain the same candidate, snapshot, strategy results, and score audit | Live fixed-symbol verified on snapshot `a9c39489-69da-53f7-a170-8a7b847e1256`; dashboard hard-veto recovery was regression-tested after the live readback exposed the missing projection. |
| Recovery | The same frozen invocation/snapshot is labeled replay and is not counted as a fresh completion; a new live fetch is a new observation | Fixture verified; same-snapshot runtime replay remains pending. The repeated 14/10 live funnel performed new provider fetches and therefore was not an idempotent replay proof. |
| External effects | Sanitized network evidence only; broker/account/message/schedule effects all false | Verified for recorded live attempts: KIS/AgentNews/OAuth reads and local isolated persistence only; broker/account/order/message/schedule effects false. |
| User acceptance | User personally observes artifacts and approves UAT | Not approved |

## Acceptance rules

1. Current `SHADOW_SCORE_V1.*` results fail the product proof gate unless `feature_snapshot_id`, component details, exact `recomposition_matches=true`, `threshold_version`, and threshold comparisons are present.
2. The stored `quant_score` object is rendered without consulting current policy to reinterpret old rows.
3. Weighted displayed component contributions must sum to the independently recomposed six-decimal total, and a valid current row must record `recomposition_matches=true` against the separately persisted total.
4. Missing, stale, partial, conflicting, unavailable, or structurally incomplete evidence remains visible and cannot become an actionable state.
5. Model parse/schema failure is `INVALID_PROPOSAL` or `ANALYSIS_INCOMPLETE`, never investment `NO_ENTRY`.
6. A live provider/model call, green tests, and generated artifacts do not establish user acceptance. The final gate stays open until the user reviews and approves the run.
7. `COMPLETED_WITH_POLICY_REJECTIONS`, `REPORT_ONLY`, and `IDEMPOTENT_REPLAY` are definitive read-only outputs and exit successfully; they remain distinct from `COMPLETED` and never count as a fresh dual-strategy scenario proof. Candidate failures, invalid/incomplete readbacks, and capability failures remain nonzero.

## Evidence record

Record only sanitized values: timestamp, git SHA, exact command shape, candidate counts, stable IDs, snapshot IDs, score/threshold versions, scenario states, endpoint host/path/status/time/hash evidence, SQLite row counts, artifact paths, and test results. Do not record credentials, authorization headers, account identifiers, or raw private payloads.

### 2026-07-30 bounded live evidence

- Daily composition at 01:17 KST: KRX snapshot failed and the explicit Naver fallback supplied the latest completed 2026-07-29 session. The funnel persisted 14 raw assertions, 10 unique identities, zero exclusions, zero invalid records, and zero truncation. Candidate analysis then failed for all ten identities because the local OAuth proxy port was already in use; this is not a completed discovered-candidate UAT.
- Daily composition at 08:12 KST: the command failed closed before discovery because KRX remained unavailable and Naver exposed current-day 2026-07-30 rows rather than the latest completed 2026-07-29 session. No report/dashboard/DB product proof was claimed from that attempt.
- Fixed-symbol product at 08:14 KST: real candidate symbol `214450` traversed KIS market data, all six KIS fundamentals calls, AgentNews, ChatGPT OAuth `gpt-5.4-mini`, isolated SQLite persistence/readback, report, and dashboard export. The strict happy-path command exited nonzero because TREND was correctly policy-rejected, but the persisted user surfaces were exported and inspected.
- Fixed-symbol snapshot: `a9c39489-69da-53f7-a170-8a7b847e1256`; SWING score `SHADOW_SCORE_V1.SWING_V1=71.519912` (`WATCH`); TREND score `SHADOW_SCORE_V1.TREND_V1=43.121888` (`POLICY_REJECTED`). Both recomposed exactly. TREND vetoes were minimum score, minimum trend strength, and maximum pullback from high.
- Local verification after review: 30 checked-in CI-equivalent pytest commands produced 1,533 pass events with one intentional deselection; compileall, broker-boundary audit (`violations: 0`), and `git diff --check` passed.
- User UAT and operated readiness remain open. This evidence proves the fixed-symbol runtime/readback slice and fail-closed daily behavior, not a completed uncapped discovered-candidate product run.
