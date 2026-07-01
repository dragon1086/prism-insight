"""
Layer 2 — cross-cycle stale-snapshot sell guard (US).

Regression test for the 2026-07-01 MU double-SELL incident:
  loop_a_hardstop stop-sold MU 23:50 (real close + published SELL), then the
  morning batch's update_holdings re-hit the same mechanical stop off its
  pipeline-start snapshot 23:54, its real sell no-op'd ("not found in
  portfolio") yet it STILL published a 2nd SELL 23:55 -> subscribers saw 2 sells.

The guard (us_stock_tracking_agent.update_holdings, top of `if should_sell:`)
re-reads the live us_stock_holdings row right before acting; if the position is
gone (already closed by another cycle) it aborts the ticker entirely — no order,
no signal publish, no journal. Its decision predicate is exactly:

    get_us_existing_position_for_ticker(cur, ticker, account_key).get("row_count", 0) == 0

These are pure-unit + temp-SQLite tests. They do NOT touch any live DB and mirror
the style of tests/test_issue_288_pyramiding.py.

Run:
    python3 tests/test_layer2_stale_snapshot_guard.py
"""

import importlib.util
import os
import re
import sqlite3
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_us_db_schema = _load_module(
    "us_db_schema_for_layer2_test",
    os.path.join(PROJECT_ROOT, "prism-us", "tracking", "db_schema.py"),
)
get_us_existing_position_for_ticker = _us_db_schema.get_us_existing_position_for_ticker
TABLE_US_STOCK_HOLDINGS = _us_db_schema.TABLE_US_STOCK_HOLDINGS

_AGENT_PATH = os.path.join(PROJECT_ROOT, "prism-us", "us_stock_tracking_agent.py")
_KR_AGENT_PATH = os.path.join(PROJECT_ROOT, "stock_tracking_agent.py")

_PASS = 0
_FAIL = 0


def check(cond, msg):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS: {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL: {msg}")


def _insert_holding(cur, account_key, ticker, buy_price):
    cur.execute(
        """INSERT INTO us_stock_holdings
            (account_key, account_name, ticker, company_name, buy_price, buy_date)
            VALUES (?, 'acct', ?, ?, ?, '2026-07-01 09:00:00')""",
        (account_key, ticker, f"{ticker} Inc", buy_price),
    )


def _guard_would_skip(cur, ticker, account_key):
    """Exact predicate used by the Layer 2 guard in update_holdings."""
    return get_us_existing_position_for_ticker(
        cur, ticker, account_key=account_key
    ).get("row_count", 0) == 0


# ── Test 1: snapshot still valid -> guard proceeds; concurrent close -> guard skips ──
def test_guard_detects_concurrent_close():
    print("\n[Test 1] Guard detects a position closed by another cycle after snapshot")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(TABLE_US_STOCK_HOLDINGS)
        conn.commit()

        # Pipeline-start snapshot: MU is held (1 clean row).
        _insert_holding(cur, "ACC1", "MU", 1053.54)
        conn.commit()
        check(not _guard_would_skip(cur, "MU", "ACC1"),
              "MU present -> guard proceeds (row_count=1, no skip)")

        # Another cycle (loop_a_hardstop) closes MU between snapshot and the batch's turn.
        cur.execute("DELETE FROM us_stock_holdings WHERE ticker='MU' AND account_key='ACC1'")
        conn.commit()
        check(_guard_would_skip(cur, "MU", "ACC1"),
              "MU closed by another cycle -> guard SKIPS (row_count=0): no 2nd SELL")
        conn.close()
    finally:
        os.remove(path)


# ── Test 2: guard is account-scoped (does not skip a different account's position) ──
def test_guard_is_account_scoped():
    print("\n[Test 2] Guard is account-scoped")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(TABLE_US_STOCK_HOLDINGS)
        conn.commit()

        _insert_holding(cur, "ACC1", "MU", 1053.54)
        _insert_holding(cur, "ACC2", "MU", 1050.00)
        conn.commit()

        # Only ACC1's MU is closed by another cycle.
        cur.execute("DELETE FROM us_stock_holdings WHERE ticker='MU' AND account_key='ACC1'")
        conn.commit()
        check(_guard_would_skip(cur, "MU", "ACC1"), "ACC1 MU gone -> skip")
        check(not _guard_would_skip(cur, "MU", "ACC2"), "ACC2 MU still held -> proceed")
        conn.close()
    finally:
        os.remove(path)


# ── Test 3: pyramided (multi-row) position still counts as held ────────────────
def test_guard_multirow_still_held():
    print("\n[Test 3] Multi-row (pyramided) position is still 'held' -> guard proceeds")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(TABLE_US_STOCK_HOLDINGS)
        conn.commit()
        for bp in (1053.54, 1060.00):
            _insert_holding(cur, "ACC1", "MU", bp)
        conn.commit()
        check(not _guard_would_skip(cur, "MU", "ACC1"), "2 rows -> proceed")

        # Only one row removed (partial) -> still held.
        cur.execute("SELECT id FROM us_stock_holdings WHERE ticker='MU' ORDER BY id LIMIT 1")
        one_id = cur.fetchone()[0]
        cur.execute("DELETE FROM us_stock_holdings WHERE id=?", (one_id,))
        conn.commit()
        check(not _guard_would_skip(cur, "MU", "ACC1"), "1 row remains -> still proceed")

        # Last row removed -> now closed -> skip.
        cur.execute("DELETE FROM us_stock_holdings WHERE ticker='MU' AND account_key='ACC1'")
        conn.commit()
        check(_guard_would_skip(cur, "MU", "ACC1"), "all rows gone -> skip")
        conn.close()
    finally:
        os.remove(path)


# ── Test 4: the guard actually exists in the source with the abort semantics ───
def test_guard_present_in_source():
    print("\n[Test 4] Guard is wired into update_holdings source")
    with open(_AGENT_PATH, "r", encoding="utf-8") as fh:
        src = fh.read()
    check("[LAYER2][US]" in src, "LAYER2 guard log marker present")
    # The guard must `continue` (abort the ticker) on row_count == 0, and must sit
    # BEFORE the sell-signal publish so no ghost SELL is emitted.
    m = re.search(r"# ── Layer 2:.*?# ── end Layer 2 guard ──", src, re.DOTALL)
    check(m is not None, "Layer 2 guard block delimited in source")
    if m:
        block = m.group(0)
        check('row_count", 0) == 0' in block, "guard predicate: row_count == 0")
        check("continue" in block, "guard aborts ticker via continue (no order/publish/journal)")
    guard_idx = src.find("[LAYER2][US]")
    publish_idx = src.find("from messaging.redis_signal_publisher import publish_sell_signal")
    check(0 < guard_idx < publish_idx,
          "guard runs BEFORE the sell-signal publish (kills duplicate at source)")


# ── Test 5: sell_stock chokepoint guard wired into BOTH agents ────────────────
def test_sell_stock_chokepoint_guard():
    print("\n[Test 5] sell_stock chokepoint guard (KR + US) — covers loops too")
    for label, path, marker, insert_tbl in (
        ("KR", _KR_AGENT_PATH, "[SELL-GUARD][KR]", "INSERT INTO trading_history"),
        ("US", _AGENT_PATH, "[SELL-GUARD][US]", "INSERT INTO us_trading_history"),
    ):
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        sell_idx = src.find("async def sell_stock(")
        check(sell_idx > 0, f"{label}: sell_stock defined")
        guard_idx = src.find(marker)
        insert_idx = src.find(insert_tbl, sell_idx)
        check(guard_idx > sell_idx, f"{label}: guard marker inside sell_stock")
        # The guard MUST run BEFORE the trading_history INSERT, else a phantom
        # sale row is written for an already-closed position.
        check(0 < guard_idx < insert_idx,
              f"{label}: guard precedes trading_history INSERT (no phantom P&L row)")
        # And it must abort via `return False` so callers (orchestrator + loops)
        # skip the real order + signal publish.
        seg = src[guard_idx - 400:insert_idx]
        check("return False" in seg, f"{label}: guard aborts via return False")
        check('row_count", 0) == 0' in seg, f"{label}: guard predicate row_count == 0")


def _run():
    test_guard_detects_concurrent_close()
    test_guard_is_account_scoped()
    test_guard_multirow_still_held()
    test_guard_present_in_source()
    test_sell_stock_chokepoint_guard()
    print(f"\n===== RESULT: {_PASS} passed, {_FAIL} failed =====")
    return _FAIL


# pytest entrypoints
def test_layer2_guard_pytest():
    assert _run() == 0


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
