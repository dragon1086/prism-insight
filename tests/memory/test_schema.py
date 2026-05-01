"""Schema migrations: idempotent on empty DB and on V1 DB."""

import sqlite3

from tracking.memory.schema import bootstrap_fts, run_migrations, fts_insert


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_run_migrations_on_empty_db_is_idempotent(tmp_path):
    db = str(tmp_path / "x.sqlite")
    conn = sqlite3.connect(db)
    try:
        applied_first = run_migrations(conn)
        applied_again = run_migrations(conn)
    finally:
        conn.close()

    assert set(applied_first) == set(range(1, 9))
    assert applied_again == []


def test_new_columns_added(tmp_path):
    db = str(tmp_path / "y.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        cols = _columns(conn, "user_memories")
    finally:
        conn.close()

    for col in ("embedding", "embedding_model", "fact_extracted", "sentiment", "outcome"):
        assert col in cols


def test_user_facts_table_present(tmp_path):
    db = str(tmp_path / "z.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        cols = _columns(conn, "user_facts")
        assert {"fact", "category", "confidence", "embedding", "active"}.issubset(cols)
    finally:
        conn.close()


def test_runs_against_v1_seeded_db(tmp_path):
    """If a V1 DB already has user_memories with rows, migrations must not crash."""
    db = str(tmp_path / "v1.sqlite")
    conn = sqlite3.connect(db)
    try:
        # Seed V1 schema + a row.
        conn.executescript("""
            CREATE TABLE user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                ticker TEXT,
                ticker_name TEXT,
                market_type TEXT DEFAULT 'kr',
                importance_score REAL DEFAULT 0.5,
                compression_layer INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT,
                command_source TEXT,
                message_id INTEGER,
                tags TEXT
            );
            INSERT INTO user_memories (user_id, memory_type, content, ticker, ticker_name, created_at)
            VALUES (1, 'journal', '{"text":"old row"}', '005930', '삼성전자', '2024-01-01T00:00:00');
        """)
        conn.commit()

        # Run migrations.
        run_migrations(conn)
        cols = _columns(conn, "user_memories")
        assert "embedding" in cols
        # Bootstrap FTS pulls existing rows in.
        n = bootstrap_fts(conn)
        assert n == 1
        # Re-running both is safe.
        run_migrations(conn)
        n2 = bootstrap_fts(conn)
        assert n2 == 0
    finally:
        conn.close()


def test_fts_insert_indexes_row(tmp_path):
    db = str(tmp_path / "f.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        # Insert a fake row + FTS entry.
        conn.execute(
            "INSERT INTO user_memories (user_id, memory_type, content, ticker, ticker_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "journal", '{"text":"삼성전자 매수"}', "005930", "삼성전자", "2025-01-01T00:00:00"),
        )
        conn.commit()
        memory_id = conn.execute("SELECT id FROM user_memories LIMIT 1").fetchone()[0]
        fts_insert(conn, memory_id, "삼성전자 매수", "005930", "삼성전자")
        cur = conn.execute(
            "SELECT rowid FROM user_memories_fts WHERE user_memories_fts MATCH ?",
            ('"삼성전자"',),
        )
        ids = [r[0] for r in cur.fetchall()]
        assert memory_id in ids
    finally:
        conn.close()
