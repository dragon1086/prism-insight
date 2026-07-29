# Trading Evolution Infographics Design

## Purpose

Add a compact visual history to the README family that explains how PRISM-INSIGHT's
Korean trading behavior evolved from excessive observation, through entry-bias
corrections and a later drawdown, into deterministic regime and risk controls.

The graphic is explanatory rather than promotional. It must distinguish measured
trading history from interpretation and clearly label the latest controls as still
being validated.

## Evidence represented

- 2025 Korean trading began cautiously: only 41 completed buys from September
  through December, including a 12-day December gap with no new entry.
- Of 74 tracked December analyses, 68 were observation decisions. Their recorded
  30-day outcomes averaged +13.3%; 33 were at least +10%, while 13 were at most
  -10%. These are counterfactual observations, not realizable portfolio returns.
- The principal 2025 drawdown ran from November 18 to December 9:
  cumulative realized return fell 36.86 percentage points before recovering.
- Entry-bias corrections appeared in v1.16.7 and v2.11; v2.13 improved candidate
  screening after another zero-entry period.
- Later losses exposed a different failure mode: regime misclassification,
  stale stop state, cooldown bypass, and duplicate-sell risk.
- v2.17 and v2.18 introduced deterministic high-volatility overrides, sell-state
  controls, Market Pulse correction/FTD state, score floors, and re-entry gates.
- After July 21, the observed sample contained 20 skips and no new Korean buy.
  This shows the controls are active, but is not enough evidence of durable
  performance improvement.

## Visual direction

Create two separate 16:9 raster infographics with identical structure:

- `docs/images/trading-evolution-ko.png`
- `docs/images/trading-evolution-en.png`

Use a white background, crisp editorial typography, generous gutters, thin
connector lines, and restrained color:

- red for critical failures and drawdowns;
- amber for transitions and incomplete corrections;
- blue for implemented controls;
- green only for verified recovery or active protection;
- charcoal and gray for body copy and caveats.

Avoid gradients, decorative illustration, mock application chrome, stock-photo
elements, and exaggerated upward charts.

## Information architecture

1. Title and one-sentence thesis.
2. KPI strip for the observation period and 2025 drawdown.
3. Four-stage horizontal timeline:
   - 2025 observation bias;
   - January-May entry activation;
   - June-July risk-control failure;
   - v2.17-v2.18 deterministic safeguards.
4. A bottom row showing the system-level meaning:
   prompt bias → candidate quality → explicit market/risk state.
5. A clearly visible validation note: monitor 7-, 14-, and 30-day outcomes.

Text inside each image should be short enough for reliable raster rendering.
The README section will carry accessible prose and the methodological caveat so
the image is not the only source of meaning.

## README integration

Insert the new section immediately after each README's Trading Performance
section and before the US Stock Market Module:

- Korean heading and Korean image in `README_ko.md`.
- English heading and English image in `README.md`.
- Localized headings and introductions, with the shared English image, in
  `README_es.md`, `README_ja.md`, and `README_zh.md`.

Use repository-relative image paths and descriptive alt text. Include a brief
note that summed trade-level profit rates and counterfactual analysis outcomes
are diagnostic measures, not time-weighted portfolio returns.

## Acceptance criteria

- Both image files are legible at common GitHub README widths.
- Korean and English images communicate the same facts and chronology.
- All five README files place the section consistently.
- Links resolve and the new images decode successfully.
- No database, credentials, temporary research files, or unrelated untracked
  files are committed.
