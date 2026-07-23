from pathlib import Path

from tools.audit_broker_boundaries import audit_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_current_repository_has_no_unapproved_direct_broker_calls():
    report = audit_repository(REPOSITORY_ROOT)

    assert report.direct_call_violations == ()
    assert report.approved_phase2_inventory == ()
    assert report.legacy_adapter_inventory
    inventory_paths = {issue.path for issue in report.legacy_dangerous_inventory}
    assert "examples/messaging/redis_subscriber_example.py" in inventory_paths
    assert "examples/messaging/gcp_pubsub_subscriber_example.py" in inventory_paths
    assert "tests/quick_test.py" in inventory_paths
    assert "tests/test_async_trading.py" in inventory_paths


def test_direct_broker_call_in_application_code_is_detected(tmp_path):
    app = tmp_path / "prism_app"
    app.mkdir()
    (app / "daily_pipeline.py").write_text(
        "async def submit(trader):\n"
        "    return await trader.async_buy_stock('005930')\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.direct_call_violations) == 1
    issue = report.direct_call_violations[0]
    assert issue.path == "prism_app/daily_pipeline.py"
    assert issue.line == 2
    assert issue.code == "DIRECT_BROKER_CALL"


def test_unapproved_phase2_adapter_candidate_is_not_exempt(tmp_path):
    adapter = tmp_path / "prism_core" / "brokers"
    adapter.mkdir(parents=True)
    (adapter / "kis_paper.py").write_text(
        "async def submit(trader):\n"
        "    return await trader.async_buy_stock('005930')\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.direct_call_violations) == 1
    assert report.approved_phase2_inventory == ()


def test_new_direct_broker_call_in_test_tree_requires_reviewed_inventory(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_external_broker.py").write_text(
        "async def test_submit(trader):\n"
        "    await trader.async_buy_stock('005930')\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.direct_call_violations) == 1
    assert report.test_broker_inventory == ()


def test_dynamic_import_getattr_and_bare_call_bypasses_are_detected(tmp_path):
    app = tmp_path / "prism_app"
    app.mkdir()
    (app / "bypass.py").write_text(
        "import importlib\n"
        "import builtins\n"
        "from importlib import import_module, import_module as im\n"
        "importlib.import_module('trading.domestic_stock_trading')\n"
        "import_module('trading.us_stock_trading')\n"
        "im('prism_core.execution_service')\n"
        "builtins.__import__('trading.domestic_stock_trading')\n"
        "async def submit(trader):\n"
        "    await getattr(trader, 'async_buy_stock')('005930')\n"
        "    await async_sell_stock('005930')\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.phase1_import_violations) == 4
    assert len(report.direct_call_violations) == 2


def test_transitional_execution_service_does_not_allow_new_direct_calls(tmp_path):
    boundary = tmp_path / "prism_core"
    boundary.mkdir()
    (boundary / "execution_service.py").write_text(
        "async def submit(trader):\n"
        "    await trader.async_buy_stock('005930')\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.direct_call_violations) == 1
    assert report.direct_call_violations[0].code == "DIRECT_BROKER_CALL"
