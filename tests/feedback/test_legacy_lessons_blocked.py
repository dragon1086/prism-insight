from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

from prism_core.feedback.lessons import LessonStatus
from prism_core.feedback.retrieval import retrieve_evaluation_lessons
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId
from tests.feedback.test_repository import AS_OF, strategy_version


def journal_manager(connection: sqlite3.Connection):
    cores_stub = types.ModuleType("cores")
    cores_stub.openai_error_logging = types.ModuleType("cores.openai_error_logging")
    cores_stub.openai_error_logging.log_openai_error = lambda *args, **kwargs: None
    cores_stub.utils = types.ModuleType("cores.utils")
    cores_stub.utils.parse_llm_json = lambda *args, **kwargs: None
    sys.modules.setdefault("cores", cores_stub)
    sys.modules.setdefault("cores.openai_error_logging", cores_stub.openai_error_logging)
    sys.modules.setdefault("cores.utils", cores_stub.utils)
    spec = importlib.util.spec_from_file_location(
        "task19_tracking_journal",
        Path(__file__).resolve().parents[2] / "tracking" / "journal.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.JournalManager(connection.cursor(), connection)


def test_legacy_journal_export_is_select_only_unvalidated_and_inert() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE trading_principles (
            id INTEGER PRIMARY KEY, condition TEXT, action TEXT, reason TEXT,
            scope TEXT, is_active INTEGER
        );
        CREATE TABLE trading_intuitions (
            id INTEGER PRIMARY KEY, category TEXT, condition TEXT, insight TEXT,
            is_active INTEGER
        );
        INSERT INTO trading_principles VALUES
            (1, 'legacy condition', 'legacy action', 'legacy reason', 'universal', 1);
        INSERT INTO trading_intuitions VALUES
            (2, 'risk', 'legacy signal', 'legacy intuition', 1);
        """
    )
    before = connection.total_changes

    exported = journal_manager(connection).export_legacy_unvalidated_lessons()

    assert connection.total_changes == before
    assert {item.status for item in exported} == {LessonStatus.LEGACY_UNVALIDATED.value}
    assert all(item.activation_allowed is False for item in exported)
    assert all(item.score_adjustment == 0 for item in exported)


def test_target_retrieval_never_surfaces_legacy_lessons(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        connection.execute(
            "INSERT INTO lessons "
            "(lesson_id, strategy_id, status, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-1", "SWING_V1", "LEGACY_UNVALIDATED",
                '{"activation_allowed":false,"score_adjustment":0}',
                AS_OF.isoformat(timespec="microseconds"),
            ),
        )

        result = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            as_of=AS_OF,
        )

        assert result.lessons == ()
