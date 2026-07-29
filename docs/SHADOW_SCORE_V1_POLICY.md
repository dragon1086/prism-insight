# SHADOW_SCORE_V1 deterministic indicator and scoring policy

Status: authoritative Phase 1 research policy
Feature version: `SHADOW_FEATURES_V1`
Score versions: `SHADOW_SCORE_V1.SWING_V1`, `SHADOW_SCORE_V1.TREND_V1`
Threshold versions: `SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1`, `SHADOW_ENTRY_THRESHOLDS_V1.TREND_V1`

## Authority and scope

SHADOW_SCORE_V1 is deterministic, long-only, research-only code. It may inform an LLM proposal, but the LLM cannot calculate the authoritative score, select or weaken a bound, or remove a threshold veto. The score never sizes a position, changes risk, creates an order intent, accesses an account, or calls a broker. KR and US use the same V1 formulas, bounds, weights, and numeric thresholds. Provider, exchange calendar, benchmark, currency, evidence, and as-of semantics remain market-specific.

All price windows end on the same latest completed exchange session. Stock and benchmark observations must be exactly session-aligned and available at the decision `as_of`. Prices are arithmetic close-to-close returns on the declared snapshot price basis; raw and adjusted series are never mixed. Decimal calculations run in a local fixed high-precision context and persisted feature values are rounded half-even to 12 decimal places. Any missing, stale, partial, unavailable, or conflicting core input prevents a meaningful score. `QuantScoreService` accepts only `FRESH` plus `ACCEPT` snapshots.

V1 bounds and weights are characterized, pre-outcome baseline choices derived from the repository's existing O'Neil/CAN-SLIM intent. They were not fitted to future returns and are not evidence of market-specific calibration.

## Canonical indicators

The notation `C[t]`, `H[t]`, and `V[t]` means the latest completed-session close, high, and volume. `B[t]` is the benchmark close on the identical session. Returns are percentage values, not fractions.

| Canonical name | Exact V1 formula and window | Unit | Direction / score bounds | Missing-quality rule |
|---|---|---|---|---|
| `swing_v1.price_return_5d_percent` | `(C[t] / C[t-5] - 1) * 100`; 6 aligned closes | percent | higher; linear `-10..+10` | core reject |
| `swing_v1.benchmark_excess_return_20d_percentage_points` | `((C[t]/C[t-20]-1) - (B[t]/B[t-20]-1)) * 100`; 21 exactly aligned stock/benchmark sessions | percentage points | higher; `-10..+20` | core reject |
| `swing_v1.volume_expansion_20d_percent` | `V[t] / mean(V[t-20:t-1]) * 100`; latest volume versus the preceding 20 sessions | percent of prior mean | higher; `50..200` | core reject; prior mean must be positive |
| `swing_v1.atr_percent_14d` | mean of 14 true ranges `max(H-L, abs(H-prevC), abs(L-prevC))`, divided by `C[t]`, times 100; simple mean, not Wilder EWM | percent | lower; inverse linear `2..8` | core reject; close positive |
| `swing_v1.catalyst_recency_sessions` | compatibility observation: completed business days since the AgentNews board `source_updated_at`; `999` when absent | sessions | report context only; not scored because it is board freshness, not a security-specific catalyst | evidence defect remains visible |
| `swing_v1.regime_compatibility` | live-provider V1: `round_half_even_0.01(clip(50 + benchmark_return_5d_fraction * 500, 0, 100))`; equivalent to benchmark return percent times 5 | score 0..100 | higher; `0..100` | core reject; static overrides are fixture/manual evidence, not live-formula proof |
| `swing_v1.average_volume_20d_shares` | `mean(V[t-19:t])` | shares/session | higher; entry threshold only, not score | core reject |
| `swing_v1.breakout_distance_20d_percent` | `(C[t] / max(H[t-20:t-1]) - 1) * 100`; latest close is excluded from resistance | percent | higher; entry threshold only | core reject; resistance positive |
| `trend_v1.price_above_200d` | `(C[t] / mean(C[t-199:t]) - 1) * 100` | percent | higher; `-10..+30` | core reject |
| `trend_v1.moving_average_alignment` | `(mean(C[t-49:t]) / mean(C[t-199:t]) - 1) * 100` | percent | higher; `-5..+20`; also trend-strength threshold input | core reject |
| `trend_v1.benchmark_excess_return_60d_percentage_points` | `((C[t]/C[t-60]-1) - (B[t]/B[t-60]-1)) * 100`; 61 aligned sessions | percentage points | higher; `-15..+30` | core reject |
| `trend_v1.earnings_trend` | `(current_PIT_earnings / abs(previous_PIT_earnings) - 1) * 100` | percent | higher; `0..50` | supplemental fundamental defect is report-only; previous value cannot be zero |
| `trend_v1.industry_leadership` | legacy compatibility observation: `round_half_even_0.01(clip(50 + (stock_return_20d_fraction - benchmark_return_20d_fraction) * 500, 0, 100))` | score 0..100 | report context only; not scored because this is benchmark-relative strength, not industry leadership | supplemental evidence defect is report-only |
| `trend_v1.regime_compatibility` | live-provider V1: `round_half_even_0.01(clip(50 + benchmark_return_20d_fraction * 500, 0, 100))`; equivalent to benchmark return percent times 5 | score 0..100 | higher; `0..100` | core reject; static overrides are fixture/manual evidence, not live-formula proof |
| `trend_v1.average_volume_20d_shares` | `mean(V[t-19:t])` | shares/session | higher; entry threshold only, not score | core reject |
| `trend_v1.distance_below_52_week_high_percent` | `max(0, (1 - C[t] / max(H[t-251:t])) * 100)`; exactly 252 completed sessions required | nonnegative percent below high | lower; entry threshold only | core reject if fewer than 252 sessions |

### Naming and compatibility

- A raw return is named `*_return_*_percent`. Its bounded 0..100 score is separately named `*_momentum_state_score` or another `*_state_score`.
- Benchmark excess return is a signed percentage-point value. It is not an O'Neil cross-sectional rank. No field in SHADOW_FEATURES_V1 is called an RS percentile because the current single-security composition has no PIT cross-sectional rank universe.
- Signed scenario field `distance_from_52_week_high_percent = (close/high - 1)*100` remains nonpositive. Policy field `distance_below_52_week_high_percent = max(0,(1-close/high)*100)` is nonnegative. They must not be compared without the explicit sign conversion.
- Legacy aliases `price_momentum_5d`, `relative_strength_20d/60d`, and `volume_expansion_20d` retain their original values for compatibility. SHADOW_SCORE_V1 scores only the canonical unit-explicit names. Existing persisted `phase1.features.v1`, `quant.features.v1`, `swing-score.shadow.v1`, `trend-score.shadow.v1`, or other historical versions are read as stored and are never rewritten or reinterpreted.

## Deterministic scores

Every component is clipped to 0..100 after linear normalization. An inverse component uses `100 - normalized_score`. The total is the sum of component score times exact Decimal weight and is rounded half-even to six decimals. Weights sum exactly to 1.

The weights and bounds are pre-registered V1 operating parameters, not fitted estimates. They encode the characterized O'Neil/CAN-SLIM baseline emphasis on price structure, benchmark-relative strength, confirming volume, market direction, bounded volatility, and PIT earnings. Inputs whose current names overstate their semantics (market-board age as a catalyst and benchmark-relative strength as industry leadership) receive zero score authority. SWING assigns the released weight to price momentum and volume confirmation; TREND assigns it to long-price structure and moving-average alignment. The two TREND structure components are intentionally correlated views of long-term position and slope, not independent evidence, and their combined 0.45 weight is explicit.

### SWING_V1

| Component | Source | Weight |
|---|---|---:|
| `momentum_state_score` | 5-session raw return | 0.30 |
| `relative_strength_state_score` | 20-session benchmark excess return | 0.20 |
| `volume_state_score` | 20-session volume expansion | 0.20 |
| `volatility_state_score` | inverse ATR% | 0.10 |
| `regime_state_score` | regime compatibility | 0.20 |

The corrected momentum mapping is: `-10% -> 0`, `0% -> 50`, `+10% -> 100`; `-0.1% -> 49.5` and `+0.1% -> 50.5`. The former `-0.10..+0.10` bounds were a unit bug because the source formula already multiplied by 100.

### TREND_V1

| Component | Source | Weight |
|---|---|---:|
| `price_structure_state_score` | close distance above 200-session mean | 0.25 |
| `trend_strength_state_score` | 50/200 moving-average alignment | 0.20 |
| `relative_strength_state_score` | 60-session benchmark excess return | 0.20 |
| `earnings_state_score` | PIT earnings trend | 0.15 |
| `regime_state_score` | regime compatibility | 0.20 |

## Entry thresholds and vetoes

Threshold evaluation is deterministic and fail-closed. A failed or missing input produces a `shadow_score_v1:*` hard veto before proposal validation. Bound equality passes.

| Strategy | Threshold | V1 value | Unit / comparison |
|---|---|---:|---|
| SWING_V1 | `min_liquidity` | 100,000 | shares/session; 20-session mean `>=` |
| SWING_V1 | `min_quant_score` | 65 | score `>=` |
| SWING_V1 | `max_atr_percent` | 8 | percent `<=` |
| SWING_V1 | `entry_breakout_buffer` | 0.5 | close percent above prior 20-session resistance `>=` |
| TREND_V1 | `min_liquidity` | 100,000 | shares/session; 20-session mean `>=` |
| TREND_V1 | `min_quant_score` | 65 | score `>=` |
| TREND_V1 | `min_trend_strength` | 0 | 50/200 alignment percent `>=` |
| TREND_V1 | `max_pullback_from_high` | 15 | nonnegative percent below 252-session high `<=` |

These shared thresholds are an initial baseline, not a claim that KR and US liquidity or volatility distributions are identical. A future market-specific change requires a new version plus prospective/OOS evidence; V1 must not be silently edited.

## Explicit inclusions and exclusions

- Price/trend structure: SWING uses breakout as a hard threshold rather than double-counting it in score; TREND scores both 200-day position and 50/200 alignment.
- Relative strength: signed benchmark excess return is scored. Cross-sectional RS percentile is excluded until a PIT universe/ranking contract exists.
- Momentum: SWING scores 5-day return. TREND excludes a separate momentum component because 200-day position, MA alignment, and 60-day excess return already overlap; adding it would double-count trend.
- Volume/liquidity: SWING scores expansion; both strategies enforce minimum average share volume. Raw notional liquidity is retained for reporting but excluded from the shared numeric threshold because KRW and USD are not comparable without an explicit base-currency/FX contract.
- Volatility/ATR/gap: SWING scores ATR and applies a maximum ATR veto. Gap risk remains visible in the scenario pack but is excluded from V1 score because the existing score feature snapshot does not yet provide an independently versioned gap feature. TREND volatility is excluded from score to avoid penalizing long-horizon leaders twice; ordinary stop/risk hard limits remain separate.
- 52-week high/peak: TREND enforces nonnegative pullback from a 252-session high. It is not scored to avoid overlap with price structure. SWING peak state remains scenario context and is not scored because short-horizon breakout already represents the V1 structure intent.
- Catalyst/fundamentals: SWING excludes the existing AgentNews-board age because it is source freshness, not a security-specific catalyst formula. TREND scores PIT earnings trend. A future security catalyst score requires timestamped symbol linkage, credibility, and a new feature/score version.
- Industry/sector leadership: excluded from both scores until a PIT security-to-industry identity and industry-universe ranking contract exists. The legacy field named `trend_v1.industry_leadership` is retained only as context/compatibility and must not be interpreted as industry leadership because its current live formula duplicates 20-session stock-versus-benchmark strength.
- Regime compatibility: scored separately in both strategies and cannot override a stale/conflicting core-data veto.

## Persistence and operated-state labels

Score ID identity includes the feature snapshot ID, score version, rules, component names, bounds, directions, weights, and total. Each SHADOW_SCORE_V1 policy also requires `SHADOW_FEATURES_V1`; legacy feature versions cannot be silently scored under the new policy. Decision snapshots persist feature and score versions plus raw feature/component values. Migrations remain append-only; no destructive or retroactive update is authorized.

Acceptance labels must remain separate:

1. policy/spec + fixture-tested implementation;
2. actual approved-provider SHADOW observation with source/as-of/call evidence;
3. runtime composition and persisted report/dashboard readback;
4. operated readiness and user UAT.

Passing fixture tests establishes only item 1. A live read-only run may establish item 2 without authorizing messages, schedules, account/balance/holding access, broker calls, or execution.
