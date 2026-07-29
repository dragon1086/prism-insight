# StockEasy Supplemental Leadership Integration

## Authority and effect boundary

StockEasy is a read-only, supplemental leadership source. KIS/KRX remain the authority for prices, returns, traded value, security eligibility, and point-in-time market state. StockEasy observations never become a standalone entry signal, change an order, or authorize execution.

The importer has no browser or network capability. It may accept only a bounded `stockeasy_sanitized_snapshot_v1` created from a currently authorized visible UI capture or official export. It rejects credentials, cookies, tokens, internal endpoint details, and account, balance, holdings, order, payment, or profile fields. Private APIs must not be reverse-engineered.

## Required evidence matrix

| Requirement | Generic evidence role | Required attempt | Unavailable behavior |
|---|---|---:|---|
| `SECURITIES` | `SECURITY_LEADERSHIP` observations | yes | `UNAVAILABLE`; KIS/KRX path continues |
| `MARKET_OVERVIEW` | `MARKET_BREADTH` or `INVESTOR_FLOWS` | yes | `UNAVAILABLE`; KIS/KRX path continues |
| `LEADING_SECURITIES` | `SECURITY_LEADERSHIP` nominations | yes | `UNAVAILABLE`; no candidate is fabricated |
| `LEADING_SECTORS` | `LEADING_GROUPS` observations | yes | `UNAVAILABLE`; no group is fabricated |

Each successful import preserves stable KR `security_id`, provider symbol, source snapshot ID, `observed_at`, `available_at`, `ingested_at`, as-of time, approved capture method, content hash, optional image hash, quality, and evidence gaps. KIS/KRX conflicts remain visible and supplemental.

## Current capability

Until current terms, exact user authorization, approved sections, and one visible UI or official export method are recorded, the runtime returns `STOCKEASY_UNAVAILABLE` with prerequisite code `APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED`. This is fail-soft: normal KIS/KRX analysis, report generation, and dashboard generation continue.

The unavailable projection records a `capability_checked_at` clock and explicitly sets `collection_attempted=false`; it does not reuse a market as-of clock as proof that StockEasy collection occurred. Observation, availability, ingestion, and hash fields remain null with a visible evidence gap.

The current implementation state is a permission-gated import contract plus a verified unavailable capability path. It is not live StockEasy integration, runtime collection, operated readiness, or user UAT approval.
