# StockEasy Supplemental Leadership Integration

## Authority and effect boundary

StockEasy is a read-only, supplemental leadership source. KIS/KRX remain the authority for prices, returns, traded value, security eligibility, and point-in-time market state. StockEasy observations never become a standalone entry signal, change an order, or authorize execution.

The importer has no browser or network capability. It may accept only a bounded `stockeasy_sanitized_snapshot_v1` created from a currently authorized visible UI capture or official export. It rejects credentials, cookies, tokens, internal endpoint details, and account, balance, holdings, order, payment, or profile fields. Private APIs must not be reverse-engineered.

## Required evidence matrix

| Requirement | Generic evidence role | Required attempt | Unavailable behavior |
|---|---|---:|---|
| `SECURITIES` | `SECURITY_LEADERSHIP` observations | yes | `UNAVAILABLE`; KIS/KRX path continues |
| `MARKET_OVERVIEW` | `MARKET_BREADTH` and `INVESTOR_FLOWS` | yes | `UNAVAILABLE`; KIS/KRX path continues |
| `LEADING_SECURITIES` | `SECURITY_LEADERSHIP` nominations | yes | `UNAVAILABLE`; no candidate is fabricated |
| `LEADING_SECTORS` | `LEADING_GROUPS` observations | yes | `UNAVAILABLE`; no group is fabricated |

Each successful import preserves stable KR `security_id`, provider symbol, source snapshot ID, `observed_at`, `available_at`, `ingested_at`, as-of time, approved capture method, content hash, optional image hash, quality, and evidence gaps. KIS/KRX conflicts remain visible and supplemental.

## Current capability

The KR daily entrypoint accepts a snapshot only as a pair:

```text
--stockeasy-snapshot <bounded-sanitized-json>
--stockeasy-permission-record <separate-scoped-permission-json>
```

Neither path alone activates import. A one-sided pair is `STOCKEASY_REJECTED` with `SNAPSHOT_AND_PERMISSION_RECORD_REQUIRED`; no pair is `STOCKEASY_UNAVAILABLE` with `APPROVED_UI_EXPORT_NOT_CONFIRMED`. Both states are fail-soft: normal KIS/KRX analysis, persistence, report generation, and dashboard generation continue.

When no pair is supplied, the visible prerequisite remains `APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED`; a filename or local artifact alone never proves permission.

`CONNECTED` is emitted only after the local importer verifies the schema, bounded regular-file inputs, content hash, PIT clock ordering, exact source scope, capture method, approved sections, kind/scope agreement, candidate evidence bindings, and prohibited-field scans over both local contracts. This means “approved one-shot snapshot imported,” not a permanent browser session or live API connection. `SITE_AVAILABLE` is an operator-attested capture-time observation only: `site_status_as_of` and `site_status_basis=OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT` qualify it, while `site_currently_verified=false` prevents the importer from claiming a fresh network check it cannot perform. The capability projection also exposes `ingestion_status`, capture method, observation/availability/ingestion clocks, source snapshot ID, content/image hashes, permission record ID, source scope, actual sanitized observations, and temporary-capture deletion state. A stale artifact is `CONNECTED_STALE`, never unqualified `CONNECTED`.

Rejected local artifacts do not prove site reachability and report `SITE_STATUS_UNKNOWN / REJECTED`. A successful imported artifact reports capture-time `SITE_AVAILABLE / IMPORTED`. The report and dashboard preserve the actual sanitized evidence while keeping `price_authority=KIS_KRX`, `entry_signal_authority=false`, and `fail_soft=true`. The current runtime uses StockEasy numbers only as discovery evidence and refetches each nominated symbol through the normal KIS/official-evidence strategy path; it does not inject StockEasy numeric values into strategy features. Because no per-symbol authoritative comparison map is composed at the nomination stage, it reports `authority_crosscheck_status=NOT_PERFORMED` rather than claiming that visible StockEasy returns/turnover were reconciled against KIS/KRX.

The importer remains local and has no browser or network capability. A successful task-branch UAT proves a bounded snapshot import and runtime readback only; it does not prove operated scheduling/recovery, grant redistribution rights, authorize broker/account effects, or constitute user UAT approval.
