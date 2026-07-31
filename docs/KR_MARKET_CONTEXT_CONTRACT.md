# KR market-context contract seam

Status: fixture contract for the bounded P3.1 KR market-context slice.

## Approved seam

The existing thin application seam is `prism_app.kr_daily_product._run_command` -> `prism_core.market.composer.KRMarketContextComposer`. The composer already receives narrow read-only KIS and AgentNews ports and returns one immutable `KRMarketContext` for the daily batch. P3.1 extends that constructor with an optional official-market port exposing only:

```text
fetch_market_context(*, as_of: aware datetime) -> ProviderPayload
```

The concrete official adapter belongs under `prism_core.market`; it must remain separate from KIS account, holdings, balance, order, cancel, replace, messaging, scheduling, credential mutation, and risk-setting paths. Legacy callers and broker-capable modules are not changed by this contract slice.

## Required output

For the latest completed KRX trading session, the composed context must expose:

- KOSPI close, trailing 20-session arithmetic mean, and 10-session return using the close from ten completed sessions earlier;
- true KOSPI + KOSDAQ listed-equity breadth: advancing, declining, unchanged, eligible-equity, excluded-non-equity, and unclassified counts;
- optional aggregate investor-flow metrics when the approved official adapter can provide them;
- an explicit deterministic `kr_regime_v1` result derived from the normalized index metrics, or `UNKNOWN` with named missing features;
- aware observation, availability, ingestion, and context as-of clocks; source role, quality, immutable evidence identity, and payload hash provenance;
- visible optional-source omissions for DART and KIND without treating those omissions as missing core fields when KIS and KRX core market data are sufficient.

The complete-session KIS and KRX observations must refer to the same session. Core missing, stale, partial, unavailable, or conflicting observations remain action-ineligible; optional flow, DART, or KIND omissions remain visible but do not fabricate values.

The governed formulas use an explicit decimal context (`precision=28`,
`ROUND_HALF_EVEN`) around the complete calculation:

```text
kospi_ma20 = sum(last 20 completed-session closes) / 20
kospi_return_10d_pct = (latest close / close 10 completed sessions earlier - 1) * 100
```

`kr_regime_v1` uses this deterministic rule order:

| Condition | Regime |
|---|---|
| close > MA20 and return10 >= 5% | `STRONG_BULL` |
| close > MA20 and return10 > 0% | `MODERATE_BULL` |
| close < MA20 and return10 <= -5% | `STRONG_BEAR` |
| close < MA20 and return10 < 0% | `MODERATE_BEAR` |
| complete inputs not matching the directional rules | `SIDEWAYS` |
| any required index feature missing | `UNKNOWN` with the sorted missing feature names |

## Breadth authority

KIS volume-rank output is candidate/ranking evidence, not a market universe. Its top 30 rows must never populate `KRMarketContext.breadth`. Breadth requires an official complete KOSPI + KOSDAQ listed-equity universe with explicit product classification. ETFs, ETNs, leveraged products, inverse products, and every other non-equity product are excluded and counted as excluded rather than as advancing/declining equities. Unclassified rows cannot silently shrink the denominator or produce fresh/action-eligible breadth.

## Determinism and provenance

Given the same normalized payloads and clocks, canonical context output and regime classification must be identical. Collection ordering is stable, metric units are explicit, and every metric references evidence carried by the context. Formula arithmetic must not depend on ambient decimal precision. Adapter payload identity binds a lowercase SHA-256 digest of the complete normalized source material; composed source clocks retain that immutable evidence identity and an explicit ingestion time.

## Fixture boundary

`tests/market/test_kr_market_context_contract.py` is the RED handoff. It defines the minimum end-to-end composer contract, the explicit rejection of volume-rank-as-breadth, and product exclusion at the official adapter boundary. These fixtures prove only the contract foundation; they do not prove live integration, runtime wiring, or operated readiness.

The task-referenced `.hermes/plans/2026-07-30_220501-kr-leadership-analysis-cohort.md` was not present in the checkout at contract time. This note therefore relies on the approved product baseline, repository rules, current `KRMarketContext`/composer interfaces, and the card's explicit acceptance criteria; dependent implementation must reconcile any later-restored plan without weakening these constraints.
