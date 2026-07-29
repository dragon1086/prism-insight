# Pipeline Architecture PNG Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing architecture artwork with 13 large-type, code-audited Korean PNG diagrams and reconnect `PIPELINE_ARCHITECTURE_ko.md` around the four-stage investment story.

**Architecture:** A deterministic Pillow renderer owns the shared visual language and all diagram copy. Each diagram is declared as structured cards rather than hand-painted pixels, allowing layout checks and text audits to run without OCR. The Markdown document remains the accessible text counterpart and carries source links and caveats that would make the images too dense.

**Tech Stack:** Python 3, Pillow already declared in `requirements.txt`, pytest, Markdown, PNG

## Global Constraints

- Final assets are PNG only; do not create or link SVG deliverables.
- Render every diagram at exactly 1920×1080.
- Use a bright off-white background and a minimum body font size of 30px.
- Use large Korean-first labels and explain unfamiliar terms at first use.
- Preserve the four-stage order: screening → analysis → trading → feedback.
- Include only claims grounded in current code, prompts, tests, or explicit feature-status documentation.
- Do not modify trading behavior, prompts, feature flags, cron, or runtime configuration.
- Do not add dependencies.
- Preserve unrelated untracked files.

---

### Task 1: Lock the PNG renderer contract

**Files:**
- Create: `tests/test_pipeline_architecture_pngs.py`
- Create: `tools/generate_pipeline_architecture_pngs.py`

**Interfaces:**
- Consumes: Pillow `Image`, `ImageDraw`, `ImageFont`
- Produces: `build_diagrams() -> list[DiagramSpec]`, `render_all(output_dir: Path) -> list[Path]`

- [ ] **Step 1: Write renderer contract tests**

Test that `build_diagrams()` returns 13 unique filenames, includes the nine existing filenames and four approved new filenames, and that every diagram has a non-empty title, stage, source list, and 3–6 primary cards.

- [ ] **Step 2: Run the contract test and verify it fails**

Run:

```bash
python3 -m pytest tests/test_pipeline_architecture_pngs.py -q
```

Expected: import failure because the generator does not exist.

- [ ] **Step 3: Implement the shared renderer**

Create immutable `DiagramSpec`, `SectionSpec`, and `CardSpec` dataclasses. Implement:

- Korean font discovery with Apple SD Gothic Neo, Nanum Gothic, and Arial Unicode fallbacks
- pixel-accurate word wrapping
- title, subtitle, stage marker, card grid, flow arrows, glossary note, source footer
- stage palettes for screening, analysis, trading, and feedback
- contrast-safe warning and status chips
- exact 1920×1080 RGB PNG output

- [ ] **Step 4: Run the contract test**

Expected: contract tests pass without creating images.

- [ ] **Step 5: Commit**

Commit the renderer and its contract tests with Lore trailers.

### Task 2: Encode the 13 audited diagrams

**Files:**
- Modify: `tools/generate_pipeline_architecture_pngs.py`
- Modify: `tests/test_pipeline_architecture_pngs.py`

**Interfaces:**
- Consumes: the shared renderer from Task 1
- Produces: structured specifications for all 13 approved images

- [ ] **Step 1: Add failing content tests**

Assert the following required terms appear in the structured copy:

- `윌리엄 오닐의 M`, `분산일`, `기관성 매도 압력이 의심`
- all six base-trigger Korean names
- `CAN SLIM` and all seven Korean questions
- `매수하지 않음` with no final `관심 종목` outcome
- `긴급 손절`, `추세 이탈 매도`, `미체결 주문 관리`
- `자율 강화학습이 아님`

Assert prohibited unsupported terms are absent from the relevant diagrams:

- `헤지 실행`
- `포지션 전환`
- `신뢰도 태그`
- `시장 폭 악화`
- `항상 가동`

- [ ] **Step 2: Run tests and confirm content failures**

- [ ] **Step 3: Add all diagram specifications**

Encode the 13 images and their non-overlapping responsibilities exactly as approved in the design spec:

1. four-stage overview
2. O'Neil M and batch control
3. distribution-day state transitions
4. six base triggers
5. candidate reranking and context triggers
6. Market Pulse versus five-state regime
7. six analysis reports
8. CAN SLIM seven questions and implementation strength
9. entry decision layers
10. pyramiding and portfolio limits
11. exit priority
12. independent protection loops
13. journal, memory, and re-entry

- [ ] **Step 4: Run content tests**

- [ ] **Step 5: Commit**

Commit the audited diagram specifications separately from rendered binaries.

### Task 3: Render and validate all PNG assets

**Files:**
- Replace: `docs/images/architecture/full-pipeline-overview.png`
- Replace: `docs/images/architecture/market-pulse-batch-control-overview.png`
- Create: `docs/images/architecture/distribution-day-state-transitions.png`
- Create: `docs/images/architecture/screening-six-triggers-overview.png`
- Replace: `docs/images/architecture/candidate-screening-reranking-overview.png`
- Replace: `docs/images/architecture/trading-regime-entry-overview.png`
- Replace: `docs/images/architecture/screening-analysis-deep-dive.png`
- Create: `docs/images/architecture/can-slim-seven-questions.png`
- Replace: `docs/images/architecture/entry-gates-overview.png`
- Replace: `docs/images/architecture/pyramiding-portfolio-overview.png`
- Replace: `docs/images/architecture/trading-exit-overview.png`
- Create: `docs/images/architecture/position-protection-loops.png`
- Replace: `docs/images/architecture/feedback-reentry-overview.png`
- Modify: `tests/test_pipeline_architecture_pngs.py`

**Interfaces:**
- Consumes: `render_all()`
- Produces: 13 RGB PNG files at 1920×1080

- [ ] **Step 1: Add image-output tests**

Test filename set, PNG format, RGB/RGBA mode, exact dimensions, non-empty content, and a conservative minimum file size.

- [ ] **Step 2: Render the images**

Run:

```bash
python3 tools/generate_pipeline_architecture_pngs.py
```

- [ ] **Step 3: Run image-output tests**

- [ ] **Step 4: Build a temporary contact sheet**

Create a contact sheet under `/tmp` for visual inspection without adding it to Git.

- [ ] **Step 5: Inspect every diagram**

Check text clipping, hierarchy, contrast, flow, repeated content, and code claims. Correct the generator and rerender until all 13 pass.

- [ ] **Step 6: Commit**

Commit the final PNG assets.

### Task 4: Rebuild the architecture document around the four stages

**Files:**
- Modify: `docs/PIPELINE_ARCHITECTURE_ko.md`
- Modify: `tests/test_pipeline_architecture_pngs.py`

**Interfaces:**
- Consumes: 13 final PNG filenames
- Produces: one ordered Markdown narrative with an accessible text counterpart for every image

- [ ] **Step 1: Add document-link tests**

Assert every generated filename appears exactly once in the intended narrative section and every link target exists.

- [ ] **Step 2: Rewrite the document**

Use these headings:

- 전체 흐름
- 1단계 종목 스크리닝
- 2단계 종목 분석
- 3단계 매매
- 4단계 피드백

Under each image include:

- 쉬운 말 요약
- 오해 방지
- 코드 근거

Remove duplicated explanations and stale descriptions from the previous version.

- [ ] **Step 3: Run document-link tests**

- [ ] **Step 4: Apply Korean copy review**

Remove unnecessary English repetition, translation-like phrases, decorative wording, and unexplained jargon while preserving all code names and numbers.

- [ ] **Step 5: Commit**

Commit the Markdown rewrite and link tests.

### Task 5: Verify code claims and complete the branch

**Files:**
- Modify if evidence requires correction: `tools/generate_pipeline_architecture_pngs.py`
- Modify if evidence requires correction: `docs/PIPELINE_ARCHITECTURE_ko.md`

**Interfaces:**
- Consumes: repository source, prompts, tests, feature-status docs, rendered PNGs
- Produces: verified branch with no known architecture-document contradictions

- [ ] **Step 1: Run focused architecture and policy tests**

```bash
python3 -m pytest \
  tests/test_pipeline_architecture_pngs.py \
  tests/test_market_pulse.py \
  tests/test_regime_policy.py \
  tests/test_trading_agents_prompt_rules.py \
  prism-us/tests/test_trading_agents_prompt_rules.py \
  tests/test_issue_289_screening.py \
  tests/test_rs_rating.py \
  tests/test_pulse_pilot_reexposure.py \
  tests/test_trading_journal.py -q
```

- [ ] **Step 2: Run text and repository checks**

```bash
git diff --check
git status --short
```

- [ ] **Step 3: Re-open representative full-resolution images**

Inspect at minimum the overview, distribution-day, six-trigger, CAN SLIM,
entry-gate, exit, and feedback images at original resolution.

- [ ] **Step 4: Resolve every discovered mismatch**

Update the structured copy or Markdown only; do not alter trading behavior to make
the diagrams true.

- [ ] **Step 5: Commit final verification corrections**

Use a Lore commit that records test evidence and any remaining deployment-state uncertainty.
