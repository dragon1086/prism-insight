import sys
from pathlib import Path

import pytest

from tools.audit_broker_boundaries import audit_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_current_phase1_tree_has_no_broker_import_violations():
    report = audit_repository(REPOSITORY_ROOT)

    assert report.violations == ()
    assert report.phase1_import_violations == ()


def test_phase1_application_broker_import_is_detected(tmp_path):
    app = tmp_path / "prism_app"
    app.mkdir()
    (app / "daily_pipeline.py").write_text(
        "from trading.domestic_stock_trading import AsyncTradingContext\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.phase1_import_violations) == 1
    issue = report.phase1_import_violations[0]
    assert issue.path == "prism_app/daily_pipeline.py"
    assert issue.line == 1
    assert issue.code == "PHASE1_BROKER_IMPORT"


def test_unknown_top_level_production_package_broker_import_fails_closed(tmp_path):
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "pipeline.py").write_text(
        "from trading.domestic_stock_trading import AsyncTradingContext\n",
        encoding="utf-8",
    )

    report = audit_repository(tmp_path)

    assert len(report.phase1_import_violations) == 1
    assert report.phase1_import_violations[0].path == "worker/pipeline.py"


def test_unknown_top_level_parse_error_fails_closed(tmp_path):
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    report = audit_repository(tmp_path)

    assert len(report.parse_violations) == 1
    assert report.parse_violations[0].path == "worker/broken.py"


@pytest.mark.parametrize("root_kind", ["missing", "empty"])
def test_invalid_or_empty_audit_root_fails_closed(tmp_path, root_kind):
    root = tmp_path / root_kind
    if root_kind == "empty":
        root.mkdir()

    report = audit_repository(root)

    assert len(report.parse_violations) == 1
    assert report.parse_violations[0].code == "AUDIT_ROOT_ERROR"


@pytest.mark.parametrize(
    "source",
    [
        "from prism_core.execution_service import ExecutionService\n",
        "from prism_core import execution_service\n",
    ],
)
def test_phase1_application_cannot_import_transitional_execution_boundary(
    tmp_path, source
):
    app = tmp_path / "prism_app"
    app.mkdir()
    (app / "daily_pipeline.py").write_text(source, encoding="utf-8")

    report = audit_repository(tmp_path)

    assert len(report.phase1_import_violations) == 1
    assert report.phase1_import_violations[0].detail == (
        "prism_core.execution_service"
    )


def test_runtime_package_import_does_not_load_broker_modules():
    before = {
        name
        for name in sys.modules
        if name == "trading" or name.startswith("trading.")
    }

    import prism_core.runtime.effects  # noqa: F401
    import prism_core.runtime.settings  # noqa: F401

    after = {
        name
        for name in sys.modules
        if name == "trading" or name.startswith("trading.")
    }
    assert after == before
