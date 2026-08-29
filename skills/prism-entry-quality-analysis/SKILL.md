---
name: prism-entry-quality-analysis
description: Analyze PRISM-INSIGHT entry or buy quality and trigger experiments from sanitized observability JSONL using the deterministic Evidence Packet workflow. Use for requests such as "매수품질 데이터 분석해줘", "진입품질이 좋아졌나", "어떤 트리거가 좋은가", trigger replacement, replay, SHADOW readiness, or LIVE promotion review. Do not use ad-hoc SQL or infer confirmed fills.
---

# PRISM Entry Quality Analysis

Use the repository's deterministic analysis contract. Do not change trading behavior.

## Required context

Read these files before analysis:

1. `docs/ENTRY_QUALITY_EVOLUTION_ko.md`
2. `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md`
3. `docs/TRADING_CHANGE_REVIEW_HARNESS.md` only if SHADOW/LIVE is being considered

Inspect the roadmap's current state. Do not describe CAPTURE observations as SHADOW results.

## Build evidence first

Use only a local, sanitized observability JSONL export. If it is missing or stale, report
`INPUT_UNAVAILABLE`; do not replace it with improvised SQL or reconstructed facts.

```bash
.venv/bin/python tools/build_entry_quality_evidence_packet.py \
  --input logs/prism_events.jsonl \
  --output .omx/evidence/entry-quality-evidence.json \
  --market US
```

Pass `--prospective-start` only when a verified deployment boundary is documented. Never move
the boundary after seeing outcomes.

Read the generated Packet rather than raw attributes. Re-run with the same inputs when
reproducibility matters and require the same `packet_id`.

### Production input

For an operating-data request, run the generator on `db-server` inside
`/root/prism-insight` against `logs/prism_events.jsonl`. Keep the raw JSONL on that server;
retrieve or inspect only the sanitized Packet. Verify the server tool commit and the Packet's
contract version before analysis. The server worktree must be clean before attempting a normal
pull; never reset or clean it as part of an analysis request.

If SSH or the sanitized spool is unavailable, report `INPUT_UNAVAILABLE`. Do not replace it with
dashboard screenshots, current database rows, report prose, or reconstructed history.

## Analysis order

1. Report Packet ID, schema/contract version, as-of, prospective boundary.
2. Check duplicates, linkage, leakage, capture coverage, component missingness, and fill status.
3. List every `readiness.insufficiency_reasons` code before performance claims.
4. Keep candidate outcomes separate from `CONFIRMED` actual outcomes.
5. Compare only explicit trigger × regime × policy-version cohorts.
6. Inspect ranked robustness inputs; report highest-winner removal and counterexamples.
7. Apply preregistered rules only. If no preregistration exists, propose one instead of tuning a
   rule on the same data.
8. End with exactly one verdict: `CONTINUE_CAPTURE`, `PREREGISTER_REPLAY`,
   `START_RULE_SHADOW_REVIEW`, `LIMITED_LIVE_REVIEW`, or `RETIRE`.

`MISSING` means unknown, not fail or pass. `SUBMITTED_ONLY`, `PARTIAL`, `UNKNOWN`,
`REJECTED`, and `CANCELLED` are never confirmed realized samples.

## Experiment discipline

Follow the harness lifecycle:

`hypothesis → preregister → offline replay → one concrete rule SHADOW → limited LIVE review → promote/retire`

Count every tried feature/threshold in the multiple-testing family. Use chronological holdout,
restart it after rule/version changes, and keep winner-removal evidence. Never enable SHADOW or
LIVE automatically. LIVE requires the trading review harness and explicit user approval.

## Output

Keep the report concise but include:

- data period and sample sizes
- coverage/fill/leakage status
- answerable and unanswerable questions
- cohort results with counterexamples
- insufficiency reasons
- verdict and the smallest next action

Do not claim causality from observational data. Update the roadmap only when verified operational
state actually changes.
