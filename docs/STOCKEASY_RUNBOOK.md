# StockEasy Supplemental Source Runbook

## Safe capability check

Run:

```bash
python -m prism_app stockeasy-capability
```

The command performs no browser, network, account, broker, message, or schedule action. In the current state it returns `STOCKEASY_UNAVAILABLE`, the four required evidence rows, timestamps and hash gaps, and `APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED`. KIS/KRX remain authoritative and StockEasy remains supplemental.

## Non-secret prerequisite

Before an import UAT can run, record all of the following without including private values:

1. current terms verification record ID;
2. user authorization basis ID;
3. exact approved generic sections;
4. exactly one approved capture method: visible read-only UI or official export;
5. a bounded sanitized `stockeasy_sanitized_snapshot_v1` JSON file produced outside PRISM;
6. if a temporary image is used, its declared SHA-256 and a deletion-verification plan.

Do not provide credentials, browser state, private endpoint details, account information, or payment/profile data.

## KR daily behavior while unavailable

`python -m prism_app kr-daily ... --stockeasy-snapshot <path>` accepts the legacy optional argument, but without a separately verified permission record the runtime does not read the supplied snapshot. It reports `STOCKEASY_UNAVAILABLE`, records `snapshot_argument_supplied=true`, and continues the KIS/KRX candidate and analysis path. Supplying a path is not permission and cannot activate collection.

No operator should claim successful import, DB/report/dashboard readback, or live integration until the UAT in `docs/STOCKEASY_UAT.md` passes against an approved source and all temporary captures are verified deleted.
