# Trading Evolution Infographics Implementation Plan

**Goal:** Add evidence-based Korean and English trading-evolution infographics to
the multilingual README family, then publish the change through a merged PR.

**Design:** Use a shared four-stage timeline and two localized raster assets.
Keep the README prose concise, accessible, and explicit about measurement limits.

---

### Task 1: Generate the localized assets

**Files:**

- Create: `docs/images/trading-evolution-ko.png`
- Create: `docs/images/trading-evolution-en.png`

1. Generate the Korean infographic from the approved evidence and visual system.
2. Generate the English infographic with the same hierarchy and claims.
3. Inspect both images for legibility, factual consistency, and text corruption.
4. Regenerate or simplify labels when visual inspection finds defects.

### Task 2: Integrate the README sections

**Files:**

- Modify: `README.md`
- Modify: `README_ko.md`
- Modify: `README_es.md`
- Modify: `README_ja.md`
- Modify: `README_zh.md`

1. Insert each section after Trading Performance and before the US module.
2. Use Korean copy and the Korean image only in `README_ko.md`.
3. Use localized heading/introductory copy and the English image elsewhere.
4. Add the diagnostic-measure caveat in each README's language.

### Task 3: Verify and publish

1. Confirm both PNG files decode and report their dimensions.
2. Check every README image target exists and section ordering is consistent.
3. Review the diff and ensure unrelated untracked files remain excluded.
4. Commit with a Lore-format message, push the `codex/` branch, open a PR,
   inspect its checks, and merge it.
