# test_pdf_renderer.py
"""
Unit tests for the shared-browser PDF renderer (pdf_converter.PdfRenderer).

Fully mocked: NO real Playwright/Chromium launch. These lock the core contract —
ONE browser is reused across many renders (the whole point of the change) and a
fresh page is created+closed per file — without needing a browser in CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import pdf_converter


@pytest.mark.asyncio
async def test_render_markdown_page_uses_given_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_converter, "markdown_to_html", lambda *a, **k: "<html>hi</html>")
    page = AsyncMock()
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)

    md = tmp_path / "r.md"
    md.write_text("x", encoding="utf-8")
    pdf = tmp_path / "r.pdf"

    out = await pdf_converter._render_markdown_page(browser, str(md), str(pdf))

    assert out == str(pdf)
    browser.new_page.assert_awaited_once()       # one page per file
    page.goto.assert_awaited_once()
    page.pdf.assert_awaited_once()
    assert page.pdf.await_args.kwargs["path"] == str(pdf)
    page.close.assert_awaited_once()             # page is closed after render


@pytest.mark.asyncio
async def test_pdf_renderer_reuses_one_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_converter, "markdown_to_html", lambda *a, **k: "<html>hi</html>")
    page = AsyncMock()
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)

    r = pdf_converter.PdfRenderer()
    r._browser = browser  # inject; skip the real Chromium launch in __aenter__

    md = tmp_path / "a.md"
    md.write_text("a", encoding="utf-8")
    p1 = await r.render(str(md), str(tmp_path / "a.pdf"))
    p2 = await r.render(str(md), str(tmp_path / "b.pdf"))

    assert p1 == str(tmp_path / "a.pdf")
    assert p2 == str(tmp_path / "b.pdf")
    # The contract: TWO pages rendered through ONE shared browser.
    assert browser.new_page.await_count == 2
    browser.close.assert_not_awaited()  # browser stays open across renders


@pytest.mark.asyncio
async def test_render_returns_none_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_converter, "markdown_to_html", lambda *a, **k: "<html>x</html>")
    browser = AsyncMock()
    browser.new_page = AsyncMock(side_effect=RuntimeError("boom"))

    r = pdf_converter.PdfRenderer()
    r._browser = browser

    md = tmp_path / "a.md"
    md.write_text("a", encoding="utf-8")
    out = await r.render(str(md), str(tmp_path / "a.pdf"))
    assert out is None  # never raises to the caller — broadcast continues


@pytest.mark.asyncio
async def test_render_before_enter_raises(tmp_path):
    r = pdf_converter.PdfRenderer()  # not entered -> no browser
    md = tmp_path / "a.md"
    md.write_text("a", encoding="utf-8")
    with pytest.raises(RuntimeError):
        await r.render(str(md), str(tmp_path / "a.pdf"))
