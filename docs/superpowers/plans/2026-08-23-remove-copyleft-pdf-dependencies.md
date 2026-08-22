# Copyleft PDF Dependency Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused `mdpdf`/PyMuPDF path and replace GPL-licensed `html2text` with deterministic standard-library normalization while preserving PDF report consumption.

**Architecture:** Keep `PyPDF2` as the PDF text extractor. Normalize its plain-text output locally instead of passing it through an HTML parser. Remove the unused fourth PDF renderer so production continues to use Playwright, with pdfkit and ReportLab retained as explicit alternatives.

**Tech Stack:** Python 3.10+, PyPDF2, pytest, GitHub Actions, db-server pyenv Python 3.11.11

**Spec:** Conversation-approved design from 2026-08-23: no new runtime dependency; `mdpdf`, PyMuPDF, and `html2text` must be absent after deployment.

## Global Constraints

- Do not add a replacement package for `html2text`.
- Preserve Unicode, URLs, Markdown punctuation, and paragraph boundaries in PDF-extracted text.
- Production PDF rendering must continue to use Playwright.
- Do not modify or overwrite unrelated local or db-server worktree changes.
- Re-run `pip-licenses --format=markdown` in the actual db-server pyenv environment after uninstalling obsolete packages.

---

### Task 1: Lock PDF text normalization behavior

**Files:**
- Create: `tests/test_pdf_text_normalization.py`
- Modify: none
- Test: `tests/test_pdf_text_normalization.py`

**Interfaces:**
- Consumes: `pdf_converter.normalize_pdf_text(text: str) -> str`
- Produces: Behavioral contract for Unicode-safe whitespace normalization

- [ ] **Step 1: Write the failing tests**

```python
from pdf_converter import normalize_pdf_text


def test_normalize_pdf_text_preserves_content_and_markdown_symbols():
    source = "# 삼성전자  \\r\\n목표가: 100,000원  \\r\\nhttps://example.com?a=1&b=2\\r\\n"
    assert normalize_pdf_text(source) == (
        "# 삼성전자\\n목표가: 100,000원\\nhttps://example.com?a=1&b=2\\n"
    )


def test_normalize_pdf_text_caps_blank_lines_and_handles_empty_input():
    assert normalize_pdf_text("첫 문단\\n\\n\\n\\n둘째 문단") == "첫 문단\\n\\n둘째 문단\\n"
    assert normalize_pdf_text("") == ""
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `python -m pytest tests/test_pdf_text_normalization.py -q`

Expected: collection fails because `normalize_pdf_text` does not exist.

- [ ] **Step 3: Do not implement production code in this task**

This task ends after the failing test proves the new behavioral boundary.

---

### Task 2: Remove copyleft PDF dependencies and implement normalization

**Files:**
- Modify: `pdf_converter.py`
- Modify: `requirements.txt`
- Test: `tests/test_pdf_text_normalization.py`

**Interfaces:**
- Consumes: `text: str` returned by `extract_text_from_pdf`
- Produces: `normalize_pdf_text(text: str) -> str`; backward-compatible `convert_to_markdown(text: str) -> str`

- [ ] **Step 1: Implement the standard-library normalizer**

```python
def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    normalized = "\\n".join(line.rstrip() for line in normalized.split("\\n"))
    normalized = re.sub(r"\\n{3,}", "\\n\\n", normalized).strip()
    return f"{normalized}\\n" if normalized else ""


def convert_to_markdown(text: str) -> str:
    return normalize_pdf_text(text)
```

- [ ] **Step 2: Remove `html2text`**

Remove the top-level import from `pdf_converter.py` and the `html2text` line from `requirements.txt`.

- [ ] **Step 3: Remove the unused mdpdf renderer**

Delete `markdown_to_pdf_mdpdf`, remove `mdpdf` from the documented method list and fallback chain, and remove `mdpdf` from `requirements.txt`. The fallback chain ends after ReportLab.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/test_pdf_text_normalization.py tests/test_pdf_renderer.py -q`

Expected: all tests pass.

---

### Task 3: Verify repository behavior and licensing surface

**Files:**
- Modify: none
- Test: targeted PDF and report contract tests

**Interfaces:**
- Consumes: the changed dependency declarations and PDF converter
- Produces: evidence that no runtime import requires the removed packages

- [ ] **Step 1: Run static checks**

Run: `python -m compileall pdf_converter.py telegram_summary_agent.py prism-us/us_telegram_summary_agent.py`

Expected: compilation succeeds.

- [ ] **Step 2: Run report/PDF tests**

Run: `python -m pytest tests/test_pdf_text_normalization.py tests/test_pdf_renderer.py tests/test_telegram_report_backend.py tests/test_report_generator_contract.py -q`

Expected: all tests pass.

- [ ] **Step 3: Confirm the removed packages have no tracked references**

Run: `rg -n "html2text|mdpdf|PyMuPDF|import fitz|import pymupdf" requirements.txt pdf_converter.py tests`

Expected: no runtime dependency or import references; only historical plan text may match.

---

### Task 4: Integrate and deploy to db-server

**Files:**
- Commit the approved code and tests on `codex/remove-copyleft-pdf-deps`
- Deploy to `/root/prism-insight` on db-server

**Interfaces:**
- Consumes: merged `main`
- Produces: db-server runtime without `mdpdf`, PyMuPDF, or `html2text`

- [ ] **Step 1: Commit and create a pull request**

Stage only `requirements.txt`, `pdf_converter.py`, `tests/test_pdf_text_normalization.py`, and this plan. Use a Lore-formatted commit message.

- [ ] **Step 2: Wait for CLA and CI checks, then merge**

Confirm Python matrix, dashboard, Codacy, and CLA checks pass before merge.

- [ ] **Step 3: Audit db-server before pull**

Fetch remote `main`; compare incoming paths with the dirty worktree. Stop if incoming changes overlap modified server files. Preserve all unrelated server changes.

- [ ] **Step 4: Fast-forward db-server and remove obsolete packages**

Run the server's deployment-safe fast-forward. Then run:

```bash
/root/.pyenv/versions/3.11.11/bin/python -m pip uninstall -y mdpdf PyMuPDF html2text
```

Do not install a replacement package.

- [ ] **Step 5: Verify server imports and license scan**

Verify `pdf_converter`, Telegram summary agents, and tracking agents import successfully. Run `pip-licenses --format=markdown` from a temporary tool installation and confirm `PyMuPDF` and `html2text` are absent.

- [ ] **Step 6: Preserve legal scan artifacts**

Save the post-removal `pip-licenses` report and `pip freeze` under `~/Downloads/prism-insight/legal/dependency-license-scan/2026-08-23-post-removal/` with SHA-256 checksums.
