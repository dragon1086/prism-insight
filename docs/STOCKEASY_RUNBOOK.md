# StockEasy Supplemental Source Runbook

## Safe capability check

Run:

```bash
python -m prism_app stockeasy-capability
```

The command performs no browser, network, account, broker, message, or schedule action. Without supplied local contracts it returns `STOCKEASY_UNAVAILABLE`, the four required evidence rows, timestamp/hash gaps, and `APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED`. KIS/KRX remain authoritative and StockEasy remains supplemental.

## Non-secret prerequisite

Before an import UAT can run, record all of the following without including private values:

1. current terms verification record ID;
2. user authorization basis ID;
3. exact approved generic sections;
4. exactly one approved capture method: visible read-only UI or official export;
5. a bounded sanitized `stockeasy_sanitized_snapshot_v1` JSON file produced outside PRISM;
6. a separate `StockEasyPermissionRecord` JSON with the record IDs, exact source scope, sections, and method;
7. if a temporary image is used, its declared SHA-256 and a deletion-verification plan.

Do not provide credentials, browser state, private endpoint details, account information, or payment/profile data.

## KR daily import

Use ignored/private paths, never tracked repository fixtures:

```bash
python -m prism_app kr-daily \
  ... \
  --stockeasy-snapshot "$PRIVATE_DIR/stockeasy_sanitized_snapshot_v1.json" \
  --stockeasy-permission-record "$PRIVATE_DIR/stockeasy_permission_record_v1.json"
```

Supplying only one path reports `STOCKEASY_REJECTED / SNAPSHOT_AND_PERMISSION_RECORD_REQUIRED`; it never treats a local path as permission. Invalid, stale, unapproved, oversized, symlinked, hash-mismatched, or prohibited-field content is rejected fail-soft. A rejected artifact reports `SITE_STATUS_UNKNOWN`, not `SITE_AVAILABLE`.

After a successful pair, verify all of the following in both Markdown and dashboard JSON:

- `status=CONNECTED`, `site_status=SITE_AVAILABLE`, `ingestion_status=IMPORTED`;
- `site_status_as_of` equals the capture clock, `site_status_basis=OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT`, and `site_currently_verified=false`; do not read `SITE_AVAILABLE` as a check-time network claim;
- all four required evidence rows are `IMPORTED`;
- `price_authority=KIS_KRX`, `entry_signal_authority=false`, `fail_soft=true`;
- `authority_crosscheck_status=NOT_PERFORMED` and `supplemental_numeric_values_used_for_strategy=false` until a real per-symbol KIS/KRX comparison map is wired; the downstream KIS refetch does not retroactively prove capture-value reconciliation;
- exact source/permission IDs, clocks, content hash, optional image hash, and sanitized observations are present;
- every supplemental candidate traversed the normal KIS/official-evidence/SWING/TREND/policy path; no count cap was added;
- temporary-capture deletion is verified. If no image was created, require `temporary_capture_used=false` and `temporary_capture_deletion_verified=true` rather than inventing a deletion event.

No operator should call this a live API integration, operated readiness, or user acceptance. `docs/STOCKEASY_UAT.md` records the bounded task-branch evidence and remaining human gate.
