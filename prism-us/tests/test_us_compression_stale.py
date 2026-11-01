"""US stale cleanup must not deactivate a principle created inside the stale window (KR parity)."""
import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "tracking" / "compression.py"
_spec = importlib.util.spec_from_file_location("us_compression_under_test", _PATH)
compression = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compression)


def test_new_unvalidated_us_principle_survives_stale_cleanup():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE trading_principles (id INTEGER PRIMARY KEY, confidence REAL, supporting_trades INTEGER,
            created_at TEXT, last_validated_at TEXT, is_active INTEGER, market TEXT);
        CREATE TABLE trading_intuitions (id INTEGER PRIMARY KEY, confidence REAL, success_rate REAL,
            created_at TEXT, last_validated_at TEXT, is_active INTEGER, market TEXT);
        CREATE TABLE trading_journal (id INTEGER PRIMARY KEY, compression_layer INTEGER, trade_date TEXT, market TEXT);
    """)
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
    cur.executemany(
        "INSERT INTO trading_principles VALUES (?,?,?,?,?,?,?)",
        [(1, 0.5, 1, fresh, None, 1, "US"), (2, 0.5, 1, old, None, 1, "US")],
    )
    conn.commit()

    compression.USCompressionManager(cur, conn).cleanup_stale_data(stale_days=90)

    active = dict(cur.execute("SELECT id, is_active FROM trading_principles").fetchall())
    assert active == {1: 1, 2: 0}
