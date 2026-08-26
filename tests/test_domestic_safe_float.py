"""Unit tests for safe KIS response conversion helpers in domestic trading.

KIS API sometimes returns empty string '' instead of numeric values for
price/quantity fields. These helpers must never raise and must fall back
to the configured default.

The helpers are extracted from the module source instead of importing
trading.domestic_stock_trading, because that module requires the secret
trading/config/kis_devlp.yaml at import time (absent in CI and fresh
checkouts). Only the two helper function definitions are compiled, so the
test exercises the exact shipped code with zero module side effects.
"""

import ast
import pathlib
import types
from typing import Any  # noqa: F401  # injected into the extracted helper globals

_HELPERS = {"_safe_float", "_safe_int"}


def _load_helpers():
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "trading"
        / "domestic_stock_trading.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper_defs = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in _HELPERS
    ]
    assert len(helper_defs) == 2, f"expected 2 helpers, found {len(helper_defs)}"
    module = types.ModuleType("_domestic_helpers")
    module.Any = Any
    exec(compile(ast.Module(body=helper_defs, type_ignores=[]), "<helpers>", "exec"), module.__dict__)  # type: ignore[arg-type]
    return module._safe_float, module._safe_int


_safe_float, _safe_int = _load_helpers()


def test_safe_float_coerces_kis_response_fields():
    cases = [
        ("123.45", 123.45),
        ("", 0.0),
        (None, 0.0),
        (" 42.5 ", 42.5),
        ("abc", 0.0),
        (0, 0.0),
        (3, 3.0),
        ("-1.25", -1.25),
    ]
    for raw, expected in cases:
        assert _safe_float(raw) == expected


def test_safe_float_uses_custom_default():
    assert _safe_float("", default=-1.0) == -1.0


def test_safe_int_coerces_kis_response_fields():
    cases = [
        ("123", 123),
        ("", 0),
        (None, 0),
        (" 7 ", 7),
        ("12.0", 12),
        ("abc", 0),
        (-3, -3),
    ]
    for raw, expected in cases:
        assert _safe_int(raw) == expected


def test_safe_int_uses_custom_default():
    assert _safe_int("", default=-1) == -1