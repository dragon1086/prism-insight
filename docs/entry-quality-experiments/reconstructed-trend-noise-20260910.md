# Reconstructed trend/noise diagnostic registration v1

Registered at 2026-09-09T16:57:47Z, before new post-exit fetch or trial computation.
The closed SNDK/SPGI losses and SNDK discovery hypothesis were already seen.
This is retrospective diagnostic registration, NOT prospective preregistration.

- Fixed data cutoff: 2026-09-09T16:18:06.031436Z.
- US Packet: f3c2ab7221ef572267d3b98b; KR Packet: 1dd6ba3b5424da1ac95dd890.
- Frozen initial data artifact: 5d1765f3c4d30d459ad23a59612ba0e01c69f1e06ed135fc4ef9532cd33e2a56.
- Additional collection round: each linked closed trade's exact exit through the
  fixed cutoff, separately for 1m/5m. Preserve original sources and new retrieval
  times/hashes. SPGI exits at the cutoff, so its extension is explicitly empty.
- ADX: reuse the four existing `trend_quality_research.registry()` trials and
  scoped trigger list without tuning. Seed Wilder on exactly the last 60
  completed daily bars before each decision. Reject missing bars, unknown actions,
  splits in the relevant window, or unresolved exchange/calendar provenance.
  No fabricated observed_at; RECONSTRUCTED_REPLAY has its own validation.
- ADX primary: paired closed-strategy keep/drop delta, no-trade comparator zero.
  Secondary: preserved winners, completed forward 1/3/5-session price returns.
  Costs 0/10/30 bps per side; retain original broker-independent strategy rows.
- Date split: chronological 70/30 whole dates, 30-calendar-day embargo, original
  minimum 30 closed rows/20 dates, four-hypothesis correction. Insufficient data
  means no valid independent holdout or meaningful uncertainty estimate.
- Restricted slot replay: only recorded eligible strategy entries, one occupied
  slot per exact observed entry, known close frees its slot. No missing rejected
  alternative or unknown original BUY/SELL decision may be invented. No capacity
  policy or price-derived return for an unknown original entry price is invented.
- Noise discovery: exact SNDK decision_ref 185b1d1f8cf09bc7. Supplied discovery
  inputs: entry 1794.17; stored stop 1740; buffer .005; normal threshold 1731.30;
  catastrophic entry*.93. Reference exit 1731.29 at 2026-09-09T13:08:04.952851Z.
  These prices were supplied by the orchestration task; the existing reassessment
  report does not contain them and MUST NOT be cited as price-source verification.
  Until a matching source is supplied, label price provenance UNVERIFIED_PRICE_SOURCE.
  Report hash c646803a5df6c3a90b45faf2dab054d839c51c3f833afb0315a6b2009e3f4d8a
  supports discovery context only. SPGI original stop missing: exclude noise study.
- Three fixed noise arms: immediate scheduled close threshold; completed 5m close
  confirmation; two consecutive scheduled breaches with every intervening 1m
  observation present and below normal threshold. Missing observations reset
  continuity. Catastrophic threshold always runs on the same fast observation lane.
- Observations approximate verified US cron at minutes 4,8,14,18,24,28,34,38,44,48,
  54,58, hours 9 through 16 inclusive, America/New_York weekdays. Use completed 1m
  closes, NOT minute lows or tick truth. Exact cron execution/quote cadence unknown.
- Execute at next available 1m open STRICTLY after decision. Record delay/gaps;
  costs 0/10/30 bps each side. If no exit, common-cutoff last completed 1m mark.
  Missing executable next price is MISSING, never backfilled optimistically.
- Provider 5m bar preferred only when completed and wholly after entry. If missing,
  derive OHLC only from all five consecutive matching 1m bars. No partial-bin proof.
- Report MFE/MAE, ordinary-stop recovery, additional adverse excursion, and actual
  reference exit separately. n=1 discovery cannot estimate a population adverse
  tail or establish stop improvement. No independent holdout, no policy promotion.
