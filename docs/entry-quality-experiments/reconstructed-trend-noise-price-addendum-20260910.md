# Price provenance addendum (before trial computation)

Recorded 2026-09-09T17:03:40Z. This corrects input provenance, not trial thresholds.
Both SNDK/SPGI losses were already seen; neither becomes independent holdout.

- Separate supplementary Packet 0591f85fa253fce18a0d993d, same original cohort
  and as-of; file SHA256 f708e92e1b5238d7a0e11d0c53f1413657c80862254b0926bdd633fed5c4dae0.
- Schema-1 entry.price_evidence is linked through the existing exact strategy
  entry event, not inferred from return or broker fill. Original Packets remain
  immutable. Source generator SHA256:
  67d0a916f049e900726575e7d48973b87d49893c55b99396c2298c523c8f5335.
- SNDK event ab3a17c1e5cc9416: reference 1794.175 (unrounded original strategy
  reference), initial stop 1740. Use 1794.175*.93 for catastrophic reference;
  normal remains 1740*.995=1731.30. This is not a broker fill.
- SPGI event 511b0e4c52fef708: reference 446.64, initial stop 422.5. The prior
  missing-stop exclusion can now be replaced with a separately labelled static
  initial-stop approximation. Dynamic stop history remains unavailable. Its exit
  equals the fixed cutoff, so it cannot supply post-exit recovery evidence.
- Three fixed arms/cadence/fill delays/costs remain unchanged. Five-minute ordinary
  confirmation also requires the current scheduled close still below threshold;
  this clarifies confirmation rather than selling after a recovered current quote.
- Correct separate log-report SHA256:
  08fd58eb041bba2468bb4e5254bbb1ac5aa17fa2ca1cce54e36502845c13bd0c.
  This report supplies SNDK actual exit reference 1731.29 and stored stop 1740;
  its older entry-price wording and obsolete fill-gate conclusions are not reused.
- Additional-case analysis does not increase the independent discovery count;
  report SNDK discovery and SPGI already-seen static-stop approximation separately.
