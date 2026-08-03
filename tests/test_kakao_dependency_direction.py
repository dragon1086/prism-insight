"""Guard the one-way dependency rule between Kakao and Prism.

Design §3.2 and §15: the Kakao bot is a separate application. Its domain,
application and port layers must not import Prism or Telegram modules —
only the adapters under `kakao_bot/adapters/prism` may, and nothing may
import the Telegram runtime at all.

This is asserted by reading source rather than by importing, so a violation
is reported as a file and line instead of an ImportError in an unrelated test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

KAKAO_ROOT = Path(__file__).resolve().parent.parent / "kakao_bot"

# Modules that belong to Prism or Telegram, not to the Kakao application.
FORBIDDEN_ROOTS = {
    "prism_core",
    "report_generator",
    "analysis_manager",
    "telegram_ai_bot",
    "pdf_converter",
    "stock_analysis_orchestrator",
}

# The seam: these packages exist precisely to depend on Prism.
ALLOWED_TO_IMPORT_PRISM = ("adapters/prism",)


def _python_files(relative: str) -> list[Path]:
    return sorted((KAKAO_ROOT / relative).rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("layer", ["domain", "application", "ports"])
def test_inner_layers_do_not_import_prism_or_telegram(layer):
    offenders = {
        str(path.relative_to(KAKAO_ROOT)): sorted(
            _imported_roots(path) & FORBIDDEN_ROOTS
        )
        for path in _python_files(layer)
        if _imported_roots(path) & FORBIDDEN_ROOTS
    }

    assert offenders == {}, (
        f"kakao_bot/{layer} must reach Prism through a port, not by importing it"
    )


def test_only_the_prism_adapter_package_imports_prism():
    offenders: dict[str, list[str]] = {}
    for path in KAKAO_ROOT.rglob("*.py"):
        relative = path.relative_to(KAKAO_ROOT).as_posix()
        if any(relative.startswith(allowed) for allowed in ALLOWED_TO_IMPORT_PRISM):
            continue
        forbidden = sorted(_imported_roots(path) & FORBIDDEN_ROOTS)
        if forbidden:
            offenders[relative] = forbidden

    assert offenders == {}, (
        "only kakao_bot/adapters/prism may import Prism modules"
    )


def test_nothing_in_kakao_imports_the_telegram_runtime():
    offenders = [
        path.relative_to(KAKAO_ROOT).as_posix()
        for path in KAKAO_ROOT.rglob("*.py")
        if "telegram_ai_bot" in _imported_roots(path)
    ]

    assert offenders == [], "Kakao must not import the Telegram runtime"


def test_the_prism_adapter_package_actually_uses_the_seam():
    """A guard that passes because nobody uses the seam would be worthless."""

    imports: set[str] = set()
    for path in _python_files("adapters/prism"):
        imports |= _imported_roots(path)

    assert "prism_core" in imports
