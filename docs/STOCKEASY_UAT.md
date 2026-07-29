# StockEasy Supplemental Source UAT

## Current evidence — 2026-07-29T14:36:05Z

No approved visible logged-in UI or official export was accessible to this task. No StockEasy page, export, account area, private endpoint, or source payload was inspected. A bounded desktop access attempt was denied, so no temporary capture was created and there was nothing to delete.

The verified capability result is `STOCKEASY_UNAVAILABLE` with all required rows unavailable:

- `SECURITIES`
- `MARKET_OVERVIEW`
- `LEADING_SECURITIES`
- `LEADING_SECTORS`

The KIS/KRX product path remains fail-soft and authoritative. This task did not establish actual StockEasy import, DB/report/dashboard readback from StockEasy evidence, operated readiness, or user UAT approval: not granted.

## Future approved-import UAT checklist

Only after the runbook prerequisite is satisfied:

1. verify the permission record covers the exact generic sections and capture method;
2. inspect only the approved visible read-only UI or official export;
3. produce one bounded sanitized snapshot without private fields;
4. verify source, stable security identity, observed/available/ingested/as-of clocks, capture method, content hash, optional image hash, and evidence gaps;
5. import it and confirm any KIS/KRX disagreement remains a visible supplemental conflict;
6. confirm every supplemental candidate traverses the normal KIS/official-evidence/SWING/TREND/validator path without truncation;
7. verify SQLite, generic report, and dashboard readback preserve the same identity and provenance;
8. delete every temporary capture and verify deletion;
9. confirm no account, balance, holdings, broker, order, message, or schedule effect occurred;
10. obtain explicit user observation and UAT approval.

A fixture, a sanitized screenshot alone, or a successful capability command does not satisfy this UAT.
