# StockEasy bounded-snapshot UAT — task t_57dc74b9

> Evidence date: 2026-07-30 KST
> Scope: one read-only, user-approved visible-UI snapshot imported into the real KR daily product
> Verdict: runtime composition and local readback verified; product remained `ANALYSIS_INCOMPLETE` because one KIS symbol fetch failed; user UAT approval and operated readiness remain false

## 1. Permission, terms, and collection boundary

- The user enabled the StockEasy integration setting before collection and explicitly approved one bounded read-only extraction for this task.
- The active logged-in page and the service terms visible from the UI were inspected on 2026-07-30 before recording the contract. The collection record is `.hermes/uat/t_57dc74b9/stockeasy_collection_record_v1.json`.
- The permission contract is `.hermes/uat/t_57dc74b9/stockeasy_permission_record_v1.json`.
- Exact approved scope: `kr-home-visible-market-leadership` via `APPROVED_UI` only. No credential, cookie, browser storage, internal API, background scrape, or account/broker surface was collected.
- The importer requires the snapshot and permission paths as a pair. Neither path alone activates import; absent or rejected inputs remain fail-soft.
- The contracts are local UAT evidence under ignored `.hermes/uat/`; they are not public redistribution artifacts.

## 2. Sanitized visible evidence captured

The capture-time observation was `2026-07-30T10:17:42+09:00`; availability was recorded at `2026-07-30T10:30:24+09:00`. The snapshot preserves only visible, normalized evidence:

- KOSPI breadth: advance 652 / unchanged 24 / decline 237;
- KOSDAQ breadth: advance 1,074 / unchanged 25 / decline 610;
- visible KOSPI/KOSDAQ investor-flow states;
- visible leading groups: shipbuilding, defense/aerospace, shipbuilding equipment, electrical equipment, integrated oil/gas, finance;
- visible leader rows for `009540`, `042660`, `010140`, `329180`, `012450`, `079550`, and `047810`, with visible turnover and session-return observations;
- visible market-level `NO_NEW_HIGH_VISIBLE` 52-week-high state.

The sanitized snapshot contains no `password`, `cookie`, `token`, `authorization`, `account`, or `session_id` key/value and references no retained image. Snapshot SHA-256: `7026060d9f492c6c2542d63694ae889e08c4517e3840625c2db89ebc4927e6cd`. Permission-record SHA-256: `31293cd7a4a81cd81c9a7ee170bec3105086d3af577d1306f09b8fe4550035e7`.

## 3. Real paired-snapshot product run

The real operator command used the documented `python -m prism_app kr-daily` entrypoint with task-local research/paper/ops databases, report/dashboard outputs, and both StockEasy arguments. It completed at product as-of `2026-07-30T11:12:19.671530+09:00` and resolved session date `2026-07-29`.

Artifacts are private and ignored under `.hermes/uat/t_57dc74b9/runtime-connected-final-v2/`:

- `result.json` — SHA-256 `584fec771337d4827471c53111ca08d45c225b4a70db67f34db673328b73494e`;
- `kr-daily.md` — SHA-256 `5cbae8c5c2d72963a9e133fcf9ed62cf567f9b2cea0b3655cd7fbcfb278b3690`;
- `dashboard.json` — SHA-256 `08c17820bae19846ee13d5fea53ecbac7ef715d4eb413b3447316be34217775b`;
- `research.sqlite` — SHA-256 `8f402cc4418163be3cecc85c1933e2a99a1a92c27feda5761e0979da9da00ee3`.

Observed runtime result:

- StockEasy: `CONNECTED`, `SITE_AVAILABLE / IMPORTED`, quality `FRESH`;
- site status is explicitly capture-time operator attestation: `site_status_as_of=2026-07-30T10:17:42+09:00`, `site_status_basis=OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT`, `site_currently_verified=false`;
- all four required rows were `IMPORTED`: `SECURITIES`, `MARKET_OVERVIEW`, `LEADING_SECURITIES`, and `LEADING_SECTORS`;
- `price_authority=KIS_KRX`, `entry_signal_authority=false`, `fail_soft=true`;
- `authority_crosscheck_status=NOT_PERFORMED` and `supplemental_numeric_values_used_for_strategy=false`; the integration nominated symbols but did not inject StockEasy numeric values into strategy features;
- `temporary_capture_used=false`, `temporary_capture_deletion_verified=true`;
- 19 raw assertions: 12 `CORE_PRISM` plus 7 `SUPPLEMENTAL_LEADERSHIP`, deduplicated to 14 identities with zero exclusions and zero truncation;
- 13 candidates persisted both SWING and TREND audit attempts, producing 26 decision snapshots and 26 trade-plan proposal records; all 26 proposal parse/validation statuses were `REJECTED`, so no valid scenario completion is claimed;
- the 14th candidate (`079550`) failed visibly with `KISMarketDataTransportError` before strategy analysis;
- report and dashboard therefore correctly remained `ANALYSIS_INCOMPLETE`; there was no parse/schema failure projected as investment `NO_ENTRY`;
- dashboard StockEasy projection and counts matched the command result exactly;
- `broker_called=false`, `message_sent=false`, `schedule_activated=false`, `uat_accepted=false`, and `operational_readiness=false`.

## 4. SQLite readback

Read-only SQLite queries against the task-local databases returned:

| Record | Count |
|---|---:|
| `decision_snapshots` | 26 |
| `trade_plan_proposals` | 26 |
| `proposal_dispositions` | 0 |
| `proposal_outcomes` | 0 |
| `retrospectives` | 0 |
| `lesson_candidates` | 0 |
| `ops.job_runs` | 26 |

This proves the decision audit spine for the 13 candidates with persisted audit records, not completed valid scenarios or a closed self-feedback loop. Outcomes, retrospectives, lessons, and later-run SHADOW retrieval remain absent and are not fabricated by this StockEasy task.

## 5. Report and dashboard readback

The generated Markdown shows `CONNECTED`, capture-time site basis/as-of, all sanitized observations, the explicit not-performed authority cross-check, and each supplemental symbol in the normal candidate sections. `009540` appears as a StockEasy-only nomination; overlapping `042660` and `012450` preserve both `CORE_PRISM` and `SUPPLEMENTAL_LEADERSHIP` channels after stable-identity reconciliation.

The dashboard preserves the same StockEasy capability object and product counts. It does not claim a live browser/API connection, current site verification, authoritative StockEasy prices, an entry signal, or operational readiness.

## 6. Negative controls and private-artifact boundary

- No paired inputs: `STOCKEASY_UNAVAILABLE`, product continues.
- One-sided pair: import remains inactive and product continues.
- Missing, malformed, oversized, symlinked, hash-mismatched, stale, future-dated, out-of-scope, or prohibited-field input: deterministic rejection or stale labeling without broker/message/schedule effects.
- Temporary image: bounded, regular-file-only, hash-verified, and deleted in `finally`; this UAT used no temporary capture image.
- Both snapshot and permission JSON receive a recursive prohibited-field/value scan before projection.
- `git check-ignore .hermes/uat` must identify the task evidence as ignored.
- `git ls-files .hermes/uat` must return no tracked evidence.

## 7. Honest completion boundary

This run proves actual approved snapshot import, real KR product runtime composition, persistence/readback, and rendering in the existing report/dashboard. It does not prove continuous scheduling/recovery, a live StockEasy API, future terms compatibility, valid scenario completion, closed SHADOW feedback, user acceptance, or operated readiness. Current state: `user UAT approval: not granted`. The final gate remains human UAT on the pushed exact-head branch.
