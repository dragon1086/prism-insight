import sqlite3
from pathlib import Path

import pytest

from prism_core.storage.database import connect_database, open_database, transaction


def test_connection_applies_sqlite_policy_to_real_file(tmp_path: Path):
    database_path = tmp_path / "research.sqlite"

    with open_database(database_path, busy_timeout_ms=4321) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
        assert connection.isolation_level is None

    assert database_path.is_file()


def test_explicit_transaction_rolls_back_every_write_on_error(tmp_path: Path):
    with open_database(tmp_path / "ops.sqlite") as connection:
        connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY)")

        with pytest.raises(RuntimeError, match="abort test transaction"):
            with transaction(connection):
                connection.execute("INSERT INTO events VALUES ('event-1')")
                raise RuntimeError("abort test transaction")

        assert connection.execute("SELECT * FROM events").fetchall() == []
        assert connection.in_transaction is False


@pytest.mark.parametrize("invalid_timeout", [True, -1, 1.5, "5000"])
def test_connection_rejects_invalid_busy_timeout_values(
    tmp_path: Path, invalid_timeout: object
):
    with pytest.raises(ValueError, match="non-negative integer"):
        connect_database(
            tmp_path / "invalid.sqlite", busy_timeout_ms=invalid_timeout  # type: ignore[arg-type]
        )


def test_open_database_closes_connection_after_context_exit(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
