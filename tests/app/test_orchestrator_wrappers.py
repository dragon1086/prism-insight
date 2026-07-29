from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPERS = (
    (
        ROOT / "stock_analysis_orchestrator.py",
        "StockAnalysisOrchestrator",
        {"run_trigger_batch", "generate_reports", "send_telegram_messages"},
    ),
    (
        ROOT / "prism-us" / "us_stock_analysis_orchestrator.py",
        "USStockAnalysisOrchestrator",
        {"run_trigger_batch", "generate_reports", "send_telegram_messages"},
    ),
)


def _class(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def _method(class_node: ast.ClassDef, name: str):
    return next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _attribute_calls(function) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


@pytest.mark.parametrize(("path", "class_name", "legacy_calls"), WRAPPERS)
def test_legacy_orchestrators_add_an_opt_in_thin_pipeline_without_replacing_defaults(
    path: Path,
    class_name: str,
    legacy_calls: set[str],
):
    class_node = _class(path, class_name)
    constructor = _method(class_node, "__init__")
    thin_method = _method(class_node, "run_application_pipeline")
    legacy_method = _method(class_node, "run_full_pipeline")

    assert [argument.arg for argument in constructor.args.args] == [
        "self",
        "telegram_config",
        "application_pipeline",
    ]
    assert isinstance(constructor.args.defaults[-1], ast.Constant)
    assert constructor.args.defaults[-1].value is None
    assert isinstance(thin_method, ast.AsyncFunctionDef)
    assert _attribute_calls(thin_method) == {"run"}
    assert legacy_calls <= _attribute_calls(legacy_method)
    assert "application_pipeline" not in {
        node.attr for node in ast.walk(legacy_method) if isinstance(node, ast.Attribute)
    }


def test_kr_candidate_wrapper_call_graph_has_no_external_effect_capabilities():
    trigger_tree = ast.parse((ROOT / "trigger_batch.py").read_text(encoding="utf-8"))
    read_only_functions = [
        node
        for node in trigger_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"discover_read_only_candidates", "select_final_tickers"}
    ]
    assert {node.name for node in read_only_functions} == {
        "discover_read_only_candidates",
        "select_final_tickers",
    }
    adapter_tree = ast.parse(
        (ROOT / "prism_app" / "kr_candidate_source.py").read_text(encoding="utf-8")
    )
    forbidden_calls = {
        "async_buy_stock",
        "async_sell_stock",
        "buy_stock",
        "cancel_order",
        "get_account_balance",
        "get_holdings",
        "replace_order",
        "sell_stock",
        "send_message",
        "send_telegram_messages",
        "track_stocks",
    }

    for function in read_only_functions:
        assert not forbidden_calls.intersection(_attribute_calls(function))
        assert not forbidden_calls.intersection(
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        )
    assert not forbidden_calls.intersection(
        node.func.attr
        for node in ast.walk(adapter_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )
