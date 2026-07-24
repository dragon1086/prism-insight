"""SQLite connection policy and versioned migration support."""

from prism_core.storage.database import connect_database, open_database, transaction
from prism_core.storage.legacy_manifest import (
    DEFAULT_MANIFEST,
    InspectionReport,
    LegacyMigrationError,
    MigrationReport,
    inspect_legacy,
    migrate_legacy,
    open_legacy_database,
)
from prism_core.storage.migrations import DatabaseKind, MigrationError, migrate_database

__all__ = [
    "DatabaseKind",
    "DEFAULT_MANIFEST",
    "InspectionReport",
    "LegacyMigrationError",
    "MigrationError",
    "MigrationReport",
    "connect_database",
    "inspect_legacy",
    "migrate_legacy",
    "migrate_database",
    "open_database",
    "open_legacy_database",
    "transaction",
]
