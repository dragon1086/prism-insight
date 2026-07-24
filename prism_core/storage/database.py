"""Fail-closed SQLite connection and explicit transaction helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 5_000


def connect_database(
    path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open a configured connection that the caller is responsible for closing."""
    if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be a non-negative integer")

    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms:d}")
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            raise RuntimeError("SQLite connection did not enter WAL journal mode")
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def open_database(
    path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Iterator[sqlite3.Connection]:
    """Open and reliably close a configured SQLite connection."""
    connection = connect_database(path, busy_timeout_ms=busy_timeout_ms)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one short, non-nested write transaction with an eager write lock."""
    if connection.in_transaction:
        raise RuntimeError("nested transactions are not supported")

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
