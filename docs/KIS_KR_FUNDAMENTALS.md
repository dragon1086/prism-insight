# KIS-primary Korean fundamental data

Status: authoritative Phase 1 provider, strategy-input, and smoke runbook

## Provider authority

KIS is the normal primary read-only provider for Korean prices and fundamental inputs. DART may verify or replace a missing KIS earnings pair when point-in-time filing acceptance metadata is available. KIND/KRX may add listing, suspension, and trading-risk evidence. Missing DART or KIND/KRX data does not reject ordinary analysis when KIS core data is fresh and sufficient.

A KIS-enabled composition must not silently substitute FMP when a required KIS capability or comparable earnings pair is absent. It records a named KIS capability/supplemental gap and may use valid DART filing evidence as the explicit fallback. The compatibility path where no KIS fundamental port is configured retains the older DART/FMP behavior until the product composition root migrates.

These adapters are market-data only. They contain no account number, balance, holdings, order, cancel, replace, broker-paper, or live-trading operation.

## Verified KIS finance capabilities

The allowlist is defined by `KIS_FUNDAMENTAL_ENDPOINTS` in `prism_core/data/providers/kis_fundamentals.py`.

| Normalized role | Official KIS path | TR ID | Selected normalized fields |
|---|---|---|---|
| Balance sheet | `/uapi/domestic-stock/v1/finance/balance-sheet` | `FHKST66430100` | current/fixed assets, current/non-current liabilities, total assets/liabilities/equity |
| Income and earnings trend | `/uapi/domestic-stock/v1/finance/income-statement` | `FHKST66430200` | revenue, gross/operating/ordinary profit, net income |
| Per-share/financial ratios | `/uapi/domestic-stock/v1/finance/financial-ratio` | `FHKST66430300` | EPS, BPS, ROE, retention ratio |
| Profitability | `/uapi/domestic-stock/v1/finance/profit-ratio` | `FHKST66430400` | return on assets/equity, gross margin, net margin |
| Balance-sheet stability | `/uapi/domestic-stock/v1/finance/stability-ratio` | `FHKST66430600` | debt, borrowings dependency, current and quick ratios |
| Growth | `/uapi/domestic-stock/v1/finance/growth-ratio` | `FHKST66430800` | revenue, operating-profit, equity, and total-asset growth |

The implementation follows the official `koreainvestment/open-trading-api` finance examples. Every endpoint is a GET using only the market division, six-digit provider symbol, and annual division selector. An unsupported or rejected endpoint produces `KIS_CAPABILITY_UNAVAILABLE:<category>`; a successful response with a malformed schema produces `KIS_SCHEMA_INVALID:<category>`. Neither path echoes provider text or fabricates a metric.

## Point-in-time and units

KIS returns `stac_yymm` but does not expose a filing acceptance timestamp or revision identity on these finance endpoints. Therefore:

- `observed_at` is the end of the reported settlement month;
- `available_at` and `ingested_at` are the successful live response receipt time;
- `as_of_date` is the normalization cutoff and must be at or after receipt;
- data cannot be replayed at an earlier historical as-of time merely because its settlement month is old;
- future settlement rows are excluded with `KIS_FUTURE_PERIOD:<category>:<YYYYMM>`;
- source record IDs are stable by provider/category/symbol/period/metric and the raw response SHA-256 is retained as `source_hash`.

The endpoints do not document a currency/scale contract precise enough to label provider amount fields as KRW. Amounts therefore use `KIS_REPORTED_AMOUNT` (or `KIS_REPORTED_AMOUNT_PER_SHARE`), while ratios use `PERCENT`. Cross-provider amount comparison requires an explicit unit conversion contract; none is inferred.

The result always exposes these limitations:

- `KIS_FILING_ACCEPTED_AT_UNAVAILABLE`
- `KIS_PROVIDER_AMOUNT_SCALE_UNSPECIFIED`
- `KIS_REVISION_ID_UNAVAILABLE`

## TREND_V1 and SHADOW_SCORE_V1 input

The adapter emits the two latest net-income observations whose settlement months are exactly twelve months apart as canonical `net_income` observations. It does not compare an incomplete current quarter with a completed fiscal year. The existing deterministic feature path consumes these as `earnings_current` and `earnings_previous` and computes:

`trend_v1.earnings_trend = (current / abs(previous) - 1) * 100`

A missing comparable pair is `KIS_COMPARABLE_ANNUAL_EARNINGS_UNAVAILABLE`. A zero prior-year base, for which the configured percentage formula is undefined, is `KIS_EARNINGS_TREND_UNDEFINED_ZERO_BASE`; the adapter withholds the pair instead of letting feature computation fail. Either condition remains a visible supplemental-fundamental defect and must not be replaced by a fabricated value. Profitability, balance-sheet, and growth observations are evidence-bearing normalized inputs in this slice; SHADOW_SCORE_V1 does not add new weights for them.

When fresh KIS core earnings exist, conflicting duplicate DART supplemental rows are retained as `DART_SUPPLEMENT_CONFLICT` but do not veto the KIS-primary analysis. Without a usable KIS pair, the same unresolved official-filing conflict remains `SEVERE_OFFICIAL_FILING_CONFLICT` and fails closed. This is an explicit provider-authority rule, not a normalization upgrade of DART quality.

## Verification and readiness states

As of 2026-07-30 KST:

1. Module/contract implementation: implemented in `prism_core/data/providers/kis_fundamentals.py`, the bounded KIS HTTP transport, and the `prism_app.kr_evidence_composer` KIS-primary seam.
2. Fixture/unit verification: implemented for endpoint allowlisting, normalization, missing capabilities, future-period rejection, KIS precedence, optional DART conflict, and no silent FMP substitution.
3. Actual endpoint verification: all six endpoints returned HTTP 200 and successful schema-bearing output for provider symbol `005930`; only endpoint/status/TR ID/timestamps/hashes/period labels were retained in smoke output.
4. Runtime product composition: selected by the default KR `product_uat` composition and therefore by the daily-close candidate analyzer; KIS prices and KIS fundamentals use separate bounded transports with the same secure token cache so their sanitized evidence remains distinct. The returned daily candidate record binds the persisted SWING/TREND decisions and SHADOW score/threshold audits to the same data snapshot identity. Reports and dashboards show the resulting decision states and explicit reasons; they do not convert a missing comparable earnings pair or incomplete analysis into `NO_ENTRY`.
5. User-visible operation/recovery and UAT: not established.

The honest status is **foundation, bounded live integration, and default runtime composition implemented; user-visible operation/recovery and UAT incomplete**.

## Bounded live smoke

The smoke uses existing local `KIS_APP_KEY` and `KIS_APP_SECRET` values without displaying or changing them. It reuses the mode-0600 market-data token cache when valid (otherwise it performs one OAuth read), then performs six finance GETs. It does not read an account or call a broker API.

```bash
PRISM_RUN_KIS_LIVE=1 PRISM_KIS_SMOKE_EVIDENCE=1 \
  python -m pytest \
  tests/integration/test_kis_live_market_data.py::test_live_kis_fundamentals_prove_all_six_account_free_capabilities \
  -q -s
```

Default CI does not set `PRISM_RUN_KIS_LIVE`; the marker remains skipped there. Missing credentials, entitlement, network, schema, or any endpoint are visible as skip/failure or named capability gaps, never fixture-backed live evidence.
