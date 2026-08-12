from __future__ import annotations

import sqlite3

from stance.server import Ledger


def test_existing_database_gets_optional_profile_columns(tmp_path):
    path = tmp_path / "old-ledger.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE strategies (
          strategy_id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          handle TEXT NOT NULL,
          market TEXT NOT NULL,
          currency TEXT NOT NULL,
          cadence TEXT NOT NULL DEFAULT 'daily',
          api_key_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO strategies VALUES (?,?,?,?,?,?,?,?)",
        ("existing", "Existing", "@owner", "KRX", "KRW", "daily", "hash", "now"),
    )
    connection.commit()
    connection.close()

    ledger = Ledger(path)
    columns = {
        row["name"] for row in ledger.conn.execute("PRAGMA table_info(strategies)")
    }
    assert {"owner_name", "tagline", "description", "website_url", "source_url"} <= columns
    assert ledger.conn.execute(
        "SELECT display_name FROM strategies WHERE strategy_id='existing'"
    ).fetchone()[0] == "Existing"
    ledger.close()
