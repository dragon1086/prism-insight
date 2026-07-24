"""Fail-closed contracts for copy-only legacy SQLite migration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from prism_core.storage.database import open_database, transaction
from prism_core.storage.migrations import DatabaseKind
from prism_core.storage.migrations import migrate_database


class LegacyMigrationError(RuntimeError):
    """Raised when legacy inspection or migration cannot prove safety."""


@contextmanager
def open_legacy_database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite source through an explicitly read-only URI."""
    source = Path(path).resolve(strict=True)
    connection = sqlite3.connect(
        f"{source.as_uri()}?mode=ro",
        uri=True,
        isolation_level=None,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


class ManifestDisposition(str, Enum):
    IMPORT = "IMPORT"
    DEFERRED_UNSUPPORTED = "DEFERRED_UNSUPPORTED"


@dataclass(frozen=True)
class TableManifest:
    source_table: str
    destination_kind: DatabaseKind
    destination_table: str | None
    transform_version: str
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...] = ()
    disposition: ManifestDisposition = ManifestDisposition.DEFERRED_UNSUPPORTED


@dataclass(frozen=True)
class SourceFingerprint:
    file_checksum: str
    size: int
    mtime_ns: int
    wal_present: bool
    wal_checksum: str | None
    wal_size: int | None
    wal_mtime_ns: int | None


@dataclass(frozen=True)
class TableInspection:
    source_table: str
    destination_kind: str
    destination_table: str | None
    transform_version: str
    disposition: str
    source_count: int
    transformed_count: int
    rejected_count: int
    source_checksum: str
    transformed_checksum: str | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class InspectionReport:
    manifest_version: str
    source: SourceFingerprint
    schema_checksum: str
    tables: tuple[TableInspection, ...]
    migration_ready: bool
    issues: tuple[str, ...] = ()

    def to_json(self) -> str:
        """Serialize metadata only; row values and source paths are intentionally absent."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class MigrationReport:
    inspection: InspectionReport
    destination_checksums: tuple[tuple[str, str], ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


MANIFEST_VERSION = "legacy-manifest-v1"
LEGACY_STATUS = "LEGACY_UNVALIDATED"
LEGACY_STRATEGY_ID = "LEGACY_UNVALIDATED"
LESSON_TRANSFORM_VERSION = "legacy-lessons-v1"

_INTUITION_REQUIRED = ("id", "category", "condition", "insight", "created_at")
_INTUITION_OPTIONAL = (
    "subcategory",
    "confidence",
    "supporting_trades",
    "success_rate",
    "source_journal_ids",
    "last_validated_at",
    "is_active",
    "scope",
    "market",
)
_PRINCIPLE_REQUIRED = ("id", "scope", "condition", "action", "created_at")
_PRINCIPLE_OPTIONAL = (
    "scope_context",
    "reason",
    "priority",
    "confidence",
    "supporting_trades",
    "source_journal_ids",
    "last_validated_at",
    "is_active",
    "market",
)


def _deferred(table: str, kind: DatabaseKind) -> TableManifest:
    return TableManifest(table, kind, None, "deferred-v1", ())


DEFAULT_MANIFEST = (
    TableManifest(
        "trading_intuitions",
        DatabaseKind.RESEARCH,
        "lessons",
        LESSON_TRANSFORM_VERSION,
        _INTUITION_REQUIRED,
        _INTUITION_OPTIONAL,
        ManifestDisposition.IMPORT,
    ),
    TableManifest(
        "trading_principles",
        DatabaseKind.RESEARCH,
        "lessons",
        LESSON_TRANSFORM_VERSION,
        _PRINCIPLE_REQUIRED,
        _PRINCIPLE_OPTIONAL,
        ManifestDisposition.IMPORT,
    ),
    _deferred("stock_holdings", DatabaseKind.PAPER),
    _deferred("us_stock_holdings", DatabaseKind.PAPER),
    _deferred("trading_history", DatabaseKind.RESEARCH),
    _deferred("us_trading_history", DatabaseKind.RESEARCH),
    _deferred("watchlist_history", DatabaseKind.RESEARCH),
    _deferred("us_watchlist_history", DatabaseKind.RESEARCH),
    _deferred("analysis_performance_tracker", DatabaseKind.RESEARCH),
    _deferred("us_analysis_performance_tracker", DatabaseKind.RESEARCH),
    _deferred("holding_decisions", DatabaseKind.RESEARCH),
    _deferred("us_holding_decisions", DatabaseKind.RESEARCH),
    _deferred("us_pending_orders", DatabaseKind.PAPER),
    _deferred("trading_journal", DatabaseKind.RESEARCH),
    _deferred("portfolio_adjustment_log", DatabaseKind.RESEARCH),
    _deferred("us_portfolio_adjustment_log", DatabaseKind.RESEARCH),
    _deferred("user_memories", DatabaseKind.RESEARCH),
    _deferred("user_preferences", DatabaseKind.RESEARCH),
    _deferred("jeoningu_trades", DatabaseKind.RESEARCH),
    _deferred("portfolio_broadcast_log", DatabaseKind.OPS),
)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    wal = Path(f"{path}-wal")
    wal_stat = wal.stat() if wal.exists() else None
    return SourceFingerprint(
        file_checksum=_sha256_file(path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        wal_present=wal_stat is not None,
        wal_checksum=_sha256_file(wal) if wal_stat is not None else None,
        wal_size=wal_stat.st_size if wal_stat is not None else None,
        wal_mtime_ns=wal_stat.st_mtime_ns if wal_stat is not None else None,
    )


@contextmanager
def _consistent_snapshot(
    source_path: str | Path,
) -> Iterator[tuple[Path, SourceFingerprint]]:
    source = Path(source_path).resolve(strict=True)
    before = _fingerprint(source)
    with tempfile.TemporaryDirectory(prefix="prism-legacy-snapshot-") as temp_dir:
        snapshot = Path(temp_dir) / "snapshot.sqlite"
        with open_legacy_database(source) as source_connection:
            destination = sqlite3.connect(snapshot)
            try:
                source_connection.backup(destination)
            finally:
                destination.close()
        try:
            yield snapshot, before
        finally:
            if _fingerprint(source) != before:
                raise LegacyMigrationError("legacy source changed during inspection")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _typed(value: Any) -> list[Any]:
    if value is None:
        return ["null"]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        return ["real", value.hex()]
    if type(value) is str:
        return ["text", unicodedata.normalize("NFC", value)]
    if type(value) is bytes:
        return ["blob", value.hex()]
    raise ValueError("unsupported SQLite value")


def _digest_rows(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = json.dumps(
            [_typed(value) for value in row],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def _lesson_id(source_table: str, source_id: int) -> str:
    identity = f"{LESSON_TRANSFORM_VERSION}\0{source_table}\0{source_id}".encode()
    return "legacy-lesson-" + hashlib.sha256(identity).hexdigest()


def _transform_lesson(
    manifest: TableManifest, columns: tuple[str, ...], row: tuple[Any, ...]
) -> tuple[str, str, str, str, str]:
    values = dict(zip(columns, row))
    source_id = values["id"]
    if type(source_id) is not int or source_id <= 0:
        raise ValueError("invalid source id")
    for name in manifest.required_columns:
        if name == "id":
            continue
        value = values[name]
        if type(value) is not str or not value.strip():
            raise ValueError("invalid required text")

    legacy_fields = {
        name: values[name]
        for name in columns
        if name not in {"id", "created_at", "is_active"}
    }
    for value in legacy_fields.values():
        _typed(value)
    payload = json.dumps(
        {
            "activation_allowed": False,
            "legacy_fields": legacy_fields,
            "score_adjustment": 0,
            "source_table": manifest.source_table,
            "source_transform": manifest.transform_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (
        _lesson_id(manifest.source_table, source_id),
        LEGACY_STRATEGY_ID,
        LEGACY_STATUS,
        payload,
        values["created_at"],
    )


def _schema_checksum(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return _digest_rows([tuple(row) for row in rows])


def _inspect_snapshot(
    snapshot: Path,
    fingerprint: SourceFingerprint,
    manifest: tuple[TableManifest, ...],
) -> tuple[InspectionReport, dict[str, list[tuple[Any, ...]]]]:
    uri = f"{snapshot.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    transformed: dict[str, list[tuple[Any, ...]]] = {}
    try:
        if connection.execute("PRAGMA user_version").fetchone() != (0,):
            raise LegacyMigrationError("unsupported legacy schema version")
        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        manifests = {entry.source_table: entry for entry in manifest}
        reports: list[TableInspection] = []
        unsupported_schema_objects = connection.execute(
            "SELECT 1 FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' AND "
            "(type = 'view' OR (type = 'table' AND UPPER(COALESCE(sql, '')) "
            "LIKE 'CREATE VIRTUAL TABLE%')) LIMIT 1"
        ).fetchone()
        global_issues: list[str] = (
            ["UNSUPPORTED_SCHEMA_OBJECT"] if unsupported_schema_objects else []
        )
        for table_name in sorted(set(manifests) | actual_tables):
            entry = manifests.get(table_name)
            if entry is None:
                entry = _deferred(table_name, DatabaseKind.RESEARCH)
                disposition = "UNKNOWN_TABLE"
            else:
                disposition = entry.disposition.value
            if table_name not in actual_tables:
                reports.append(
                    TableInspection(
                        table_name,
                        entry.destination_kind.value,
                        entry.destination_table,
                        entry.transform_version,
                        disposition,
                        0,
                        0,
                        0,
                        _digest_rows([]),
                        _digest_rows([])
                        if entry.disposition is ManifestDisposition.IMPORT
                        else None,
                    )
                )
                continue

            quoted = _quote_identifier(table_name)
            count = int(
                connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            )
            column_rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            columns = tuple(str(row[1]) for row in column_rows)
            order = "ORDER BY id COLLATE BINARY" if "id" in columns else "ORDER BY rowid"
            source_rows = [
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {quoted} {order}")
            ]
            source_checksum = _digest_rows(source_rows)
            issues: list[str] = []
            output_rows: list[tuple[Any, ...]] = []
            if entry.disposition is ManifestDisposition.IMPORT:
                allowed = set(entry.required_columns) | set(entry.optional_columns)
                if set(entry.required_columns) - set(columns):
                    issues.append("MISSING_REQUIRED_COLUMNS")
                if set(columns) - allowed:
                    issues.append("UNEXPECTED_COLUMNS")
                if not issues:
                    for row in source_rows:
                        try:
                            output_rows.append(_transform_lesson(entry, columns, row))
                        except (KeyError, TypeError, ValueError):
                            issues.append("INVALID_ROW")
                output_rows.sort(key=lambda row: row[0])
                rejected = count - len(output_rows)
                output_checksum = _digest_rows(output_rows)
                transformed[table_name] = output_rows
            else:
                rejected = count
                output_checksum = None
                if count:
                    issues.append("UNSUPPORTED_NONEMPTY_TABLE")
            if issues:
                global_issues.extend(
                    f"{table_name}:{issue}" for issue in sorted(set(issues))
                )
            reports.append(
                TableInspection(
                    table_name,
                    entry.destination_kind.value,
                    entry.destination_table,
                    entry.transform_version,
                    disposition,
                    count,
                    len(output_rows),
                    rejected,
                    source_checksum,
                    output_checksum,
                    tuple(sorted(set(issues))),
                )
            )
        report = InspectionReport(
            MANIFEST_VERSION,
            fingerprint,
            _schema_checksum(connection),
            tuple(reports),
            not global_issues,
            tuple(sorted(global_issues)),
        )
        return report, transformed
    finally:
        connection.close()


def inspect_legacy(
    source_path: str | Path,
    *,
    manifest: tuple[TableManifest, ...] = DEFAULT_MANIFEST,
) -> InspectionReport:
    """Return a deterministic metadata-only report from a private consistent snapshot."""
    with _consistent_snapshot(source_path) as (snapshot, fingerprint):
        report, _ = _inspect_snapshot(snapshot, fingerprint, manifest)
        return report


def _verify_lessons(
    database_path: Path, expected: dict[str, list[tuple[Any, ...]]]
) -> tuple[tuple[str, str], ...]:
    actual: dict[str, list[tuple[Any, ...]]] = {table: [] for table in expected}
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT lesson_id, strategy_id, status, payload_json, created_at "
            "FROM lessons ORDER BY lesson_id"
        ).fetchall()
    for row in rows:
        try:
            source_table = json.loads(row[3])["source_table"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LegacyMigrationError("destination lesson verification failed") from exc
        if source_table not in actual:
            raise LegacyMigrationError("destination contains an unexpected legacy lesson")
        actual[source_table].append(tuple(row))

    checksums: list[tuple[str, str]] = []
    for table_name, expected_rows in sorted(expected.items()):
        actual_rows = actual[table_name]
        if len(actual_rows) != len(expected_rows):
            raise LegacyMigrationError("destination lesson count mismatch")
        expected_checksum = _digest_rows(expected_rows)
        actual_checksum = _digest_rows(actual_rows)
        if actual_checksum != expected_checksum:
            raise LegacyMigrationError("destination lesson checksum mismatch")
        checksums.append((table_name, actual_checksum))
    return tuple(checksums)


def _checkpoint_and_remove_sidecars(stage: Path) -> None:
    for kind in DatabaseKind:
        database_path = stage / f"{kind.value}.sqlite"
        with closing(sqlite3.connect(database_path, isolation_level=None)) as connection:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result is None or result[0] != 0:
                raise LegacyMigrationError("destination WAL checkpoint failed")
        wal = Path(f"{database_path}-wal")
        shm = Path(f"{database_path}-shm")
        if wal.exists() and wal.stat().st_size != 0:
            raise LegacyMigrationError("destination WAL is not empty after checkpoint")
        wal.unlink(missing_ok=True)
        shm.unlink(missing_ok=True)


def migrate_legacy(
    source_path: str | Path,
    destination_directory: str | Path,
    *,
    manifest: tuple[TableManifest, ...] = DEFAULT_MANIFEST,
) -> MigrationReport:
    """Copy supported legacy rows into a new, atomically published DB bundle."""
    destination = Path(destination_directory)
    parent = destination.parent.resolve(strict=True)
    final = parent / destination.name
    lock = parent / f".{destination.name}.lock"
    stage: Path | None = None
    lock_created = False
    try:
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LegacyMigrationError("destination collision") from exc
        else:
            os.close(lock_fd)
            lock_created = True
        if final.exists() or final.is_symlink():
            raise LegacyMigrationError("destination collision")

        stage = Path(tempfile.mkdtemp(prefix=".prism-legacy-stage-", dir=parent))
        with _consistent_snapshot(source_path) as (snapshot, fingerprint):
            inspection, transformed = _inspect_snapshot(snapshot, fingerprint, manifest)
            if not inspection.migration_ready:
                raise LegacyMigrationError("legacy source is not migration-ready")

            for kind in DatabaseKind:
                with open_database(stage / f"{kind.value}.sqlite") as connection:
                    migrate_database(connection, kind)
                    if kind is DatabaseKind.RESEARCH:
                        rows = [
                            row
                            for table_rows in transformed.values()
                            for row in table_rows
                        ]
                        with transaction(connection):
                            connection.executemany(
                                "INSERT INTO lessons "
                                "(lesson_id, strategy_id, status, payload_json, created_at) "
                                "VALUES (?, ?, ?, ?, ?)",
                                rows,
                            )

            checksums = _verify_lessons(stage / "research.sqlite", transformed)
            _checkpoint_and_remove_sidecars(stage)
        if final.exists() or final.is_symlink():
            raise LegacyMigrationError("destination collision")
        stage.rename(final)
        return MigrationReport(inspection, checksums)
    except BaseException:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        raise
    finally:
        if lock_created:
            lock.unlink(missing_ok=True)