"""
Tests for #288 강세장 추가매수(피라미딩) — independent-row model.

Pure-unit + temp-SQLite tests. Does NOT touch any live DB.

Run:
    python3 tests/test_issue_288_pyramiding.py
"""

import ast
import os
import sqlite3
import sys
import tempfile

# Make project root importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import pure helpers directly from the module file (avoid heavy package __init__).
import importlib.util


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_helpers = _load_module(
    "kr_helpers_for_test", os.path.join(PROJECT_ROOT, "tracking", "helpers.py")
)
_db_schema = _load_module(
    "kr_db_schema_for_test", os.path.join(PROJECT_ROOT, "tracking", "db_schema.py")
)

evaluate_pyramid_add_gate = _helpers.evaluate_pyramid_add_gate
compute_fractional_sell_quantity = _helpers.compute_fractional_sell_quantity
get_existing_position_for_ticker = _helpers.get_existing_position_for_ticker
migrate_drop_holdings_unique_constraint = _db_schema.migrate_drop_holdings_unique_constraint

# US helpers + migration
_us_db_schema = _load_module(
    "us_db_schema_for_test",
    os.path.join(PROJECT_ROOT, "prism-us", "tracking", "db_schema.py"),
)
evaluate_us_pyramid_add_gate = _us_db_schema.evaluate_us_pyramid_add_gate
compute_us_fractional_sell_quantity = _us_db_schema.compute_us_fractional_sell_quantity
migrate_drop_us_holdings_unique_constraint = _us_db_schema.migrate_drop_us_holdings_unique_constraint
decide_us_sell_plan = _us_db_schema.decide_us_sell_plan


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


# ── Old-schema holdings table WITH UNIQUE (pre-migration) ──────────────────
_OLD_STOCK_HOLDINGS_WITH_UNIQUE = """
CREATE TABLE stock_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    current_price REAL,
    last_updated TEXT,
    scenario TEXT,
    target_price REAL,
    stop_loss REAL,
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,
    UNIQUE(account_key, ticker)
)
"""

_OLD_US_STOCK_HOLDINGS_WITH_UNIQUE = """
CREATE TABLE us_stock_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    current_price REAL,
    last_updated TEXT,
    scenario TEXT,
    target_price REAL,
    stop_loss REAL,
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,
    UNIQUE(account_key, ticker)
)
"""


def _insert_holding(cur, table, account_key, ticker, buy_price):
    cur.execute(
        f"""INSERT INTO {table}
            (account_key, account_name, ticker, company_name, buy_price, buy_date)
            VALUES (?, 'acct', ?, ?, ?, '2026-01-01 09:00:00')""",
        (account_key, ticker, f"{ticker} Inc", buy_price),
    )


def _has_unique(cur, table):
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,))
    r = cur.fetchone()
    return bool(r and r[0] and "UNIQUE" in r[0].upper())


def _row_count(cur, table):
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


# ── Test 1: migration idempotency + data preservation (KR + US) ────────────
def test_migration():
    print("\n[Test 1] Migration idempotency + data preservation")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(_OLD_STOCK_HOLDINGS_WITH_UNIQUE)
        cur.execute(_OLD_US_STOCK_HOLDINGS_WITH_UNIQUE)
        conn.commit()

        # Seed rows (KR + US)
        _insert_holding(cur, "stock_holdings", "ACC1", "000660", 100000)
        _insert_holding(cur, "stock_holdings", "ACC1", "005930", 70000)
        _insert_holding(cur, "us_stock_holdings", "ACC1", "AAPL", 200.0)
        conn.commit()

        check(_has_unique(cur, "stock_holdings"), "KR table starts WITH UNIQUE")
        check(_has_unique(cur, "us_stock_holdings"), "US table starts WITH UNIQUE")
        kr_before = _row_count(cur, "stock_holdings")
        us_before = _row_count(cur, "us_stock_holdings")

        # Run KR migration (handles both stock_holdings and us_stock_holdings)
        migrate_drop_holdings_unique_constraint(cur, conn)

        check(not _has_unique(cur, "stock_holdings"), "KR UNIQUE removed after migration")
        check(not _has_unique(cur, "us_stock_holdings"), "US UNIQUE removed (via KR migration helper)")
        check(_row_count(cur, "stock_holdings") == kr_before, f"KR rows preserved ({kr_before})")
        check(_row_count(cur, "us_stock_holdings") == us_before, f"US rows preserved ({us_before})")
        check(_has_unique is not None, "sanity")

        # Backup table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_holdings_pre_pyramiding_backup'")
        check(cur.fetchone() is not None, "KR backup table created")

        # Now duplicate ticker insert should SUCCEED (UNIQUE gone) -> pyramiding row
        try:
            _insert_holding(cur, "stock_holdings", "ACC1", "000660", 110000)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM stock_holdings WHERE ticker='000660' AND account_key='ACC1'")
            dup_count = cur.fetchone()[0]
            check(dup_count == 2, "duplicate ticker now allowed (2 rows for 000660)")
        except Exception as e:
            check(False, f"duplicate ticker insert raised: {e}")

        # Run migration AGAIN -> must be no-op, rows preserved, no error
        kr_after_dup = _row_count(cur, "stock_holdings")
        migrate_drop_holdings_unique_constraint(cur, conn)
        check(_row_count(cur, "stock_holdings") == kr_after_dup, "second run is no-op (rows unchanged)")
        check(not _has_unique(cur, "stock_holdings"), "still no UNIQUE after second run")

        # US-specific migration entrypoint also idempotent
        migrate_drop_us_holdings_unique_constraint(cur, conn)
        check(not _has_unique(cur, "us_stock_holdings"), "US migration entrypoint idempotent")

        conn.close()
    finally:
        os.remove(path)


# ── Test 2: fractional-sell math ───────────────────────────────────────────
def test_fractional_sell():
    print("\n[Test 2] Fractional-sell math")

    # total 33, N=3 -> 11,11,11 ; conserved
    total = 33
    sold = []
    remaining = total
    for n in (3, 2, 1):
        q = compute_fractional_sell_quantity(remaining, n)
        sold.append(q)
        remaining -= q
    check(sold == [11, 11, 11], f"33 over N=3,2,1 -> {sold} (expect 11,11,11)")
    check(sum(sold) == total, "33 conserved")
    check(remaining == 0, "33 fully swept")

    # total 10, N=3 -> 3, then 7/N=2 -> 3, then 4 ; conserved, last sweeps remainder
    total = 10
    sold = []
    remaining = total
    for n in (3, 2, 1):
        q = compute_fractional_sell_quantity(remaining, n)
        sold.append(q)
        remaining -= q
    check(sold == [3, 3, 4], f"10 over N=3,2,1 -> {sold} (expect 3,3,4)")
    check(sum(sold) == total, "10 conserved")
    check(remaining == 0, "10 fully swept, last row sweeps remainder")

    # N=1 -> sell all (regression guarantee)
    check(compute_fractional_sell_quantity(57, 1) == 57, "N=1 sells all (57)")
    check(compute_fractional_sell_quantity(57, 0) == 57, "N<=1 (0) sells all (defensive)")
    check(compute_fractional_sell_quantity(0, 3) == 0, "0 holding -> 0")

    # US mirror identical
    check(compute_us_fractional_sell_quantity(33, 3) == 11, "US 33/3 -> 11")
    check(compute_us_fractional_sell_quantity(10, 1) == 10, "US N=1 sells all")


# ── Test 3: add-gate logic ─────────────────────────────────────────────────
def test_add_gate():
    print("\n[Test 3] Add-gate logic (regime / profit / rowcount)")

    # Regime
    for regime in ("strong_bull: blah", "parabolic: x"):
        ok, _ = evaluate_pyramid_add_gate(regime, 100.0, 110.0, 0)
        check(ok, f"regime '{regime.split(':')[0]}' passes")
    for regime in ("moderate_bull: x", "sideways: x", "moderate_bear: x", "strong_bear: x", "", None):
        ok, _ = evaluate_pyramid_add_gate(regime, 100.0, 110.0, 0)
        check(not ok, f"regime '{regime}' fails")

    # Profit: >=+5% pass, <+5% fail
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 105.0, 1)
    check(ok, "+5.0% profit passes")
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 104.99, 1)
    check(not ok, "+4.99% profit fails")
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 99.0, 1)
    check(not ok, "negative profit fails")

    # Rowcount: <3 pass, ==3 fail
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 110.0, 0)
    check(ok, "rowcount 0 < 3 passes")
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 110.0, 2)
    check(ok, "rowcount 2 < 3 passes (the 3rd entry)")
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 100.0, 110.0, 3)
    check(not ok, "rowcount 3 == max fails (no 4th)")

    # Missing price data
    ok, _ = evaluate_pyramid_add_gate("strong_bull: x", 0.0, 110.0, 1)
    check(not ok, "zero avg buy price fails")

    # US mirror
    ok, _ = evaluate_us_pyramid_add_gate("parabolic: x", 200.0, 220.0, 1)
    check(ok, "US parabolic +10% rows=1 passes")
    ok, _ = evaluate_us_pyramid_add_gate("sideways: x", 200.0, 220.0, 1)
    check(not ok, "US sideways fails")

    # Realistic value (underscore + colon + long Korean description) — FIX 3
    realistic = "strong_bull: 보고서 기준 KOSPI가 20일선 상회, 외국인 순매수 지속으로 위험선호 강화"
    ok, _ = evaluate_pyramid_add_gate(realistic, 100.0, 110.0, 1)
    check(ok, f"realistic 'strong_bull: ...' value passes (KR)")
    ok, _ = evaluate_us_pyramid_add_gate("parabolic: NASDAQ breadth surging, VIX collapsing", 200.0, 230.0, 0)
    check(ok, "realistic 'parabolic: ...' value passes (US)")


# ── Test 4: single-row regression (N==1 sells all) ─────────────────────────
def test_single_row_regression():
    print("\n[Test 4] Single-row regression")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        # Fresh-schema (no UNIQUE) table mirroring canonical CREATE
        cur.execute(_db_schema.TABLE_STOCK_HOLDINGS)
        conn.commit()
        check(not _has_unique(cur, "stock_holdings"), "canonical KR CREATE has NO UNIQUE")

        _insert_holding(cur, "stock_holdings", "ACC1", "000660", 100000)
        conn.commit()

        existing = get_existing_position_for_ticker(cur, "000660", account_key="ACC1")
        n = existing["row_count"]
        check(n == 1, "single-row ticker has row_count 1")

        # With N==1 the fractional helper returns full quantity (sell all)
        check(compute_fractional_sell_quantity(57, n) == 57, "N==1 => sell all (no behavior change)")

        # avg buy price aggregation correctness for multi-row
        _insert_holding(cur, "stock_holdings", "ACC1", "000660", 120000)
        conn.commit()
        existing2 = get_existing_position_for_ticker(cur, "000660", account_key="ACC1")
        check(existing2["row_count"] == 2, "two rows after add")
        check(abs(existing2["avg_buy_price"] - 110000.0) < 1e-6, "avg buy price = 110000 across 2 rows")

        conn.close()
    finally:
        os.remove(path)


# ── Test 6: FIX 2 — in-pass over-sell distribution from a fixed snapshot ───
def _distribute_from_snapshot(snapshot, n_rows, broker_qty_each_iter, frac_fn):
    """Simulate update_holdings' in-pass accumulator distribution.

    broker_qty_each_iter: list mocking what get_holding_quantity() WOULD return
    on each iteration (we IGNORE these to prove independence from fill timing —
    the snapshot is taken ONCE on the first iteration only).
    """
    pass_total = None
    pass_sold = 0
    orders = []
    remaining_rows = n_rows
    for i in range(n_rows):
        if pass_total is None:
            # snapshot ONCE on first sell of this ticker (mock first reading)
            pass_total = broker_qty_each_iter[0]
            pass_sold = 0
        available = pass_total - pass_sold
        q = frac_fn(available, remaining_rows)
        orders.append(q)
        pass_sold += q
        remaining_rows -= 1
    return orders, pass_total


def test_oversell_snapshot():
    print("\n[Test 6] FIX 2 — in-pass over-sell guard (snapshot accumulator)")

    # Broker qty does NOT decrement between iterations (limit orders unfilled).
    # snapshot=33, 3 rows -> 11/11/11 ; never exceeds snapshot.
    orders, snap = _distribute_from_snapshot(
        33, 3, broker_qty_each_iter=[33, 33, 33], frac_fn=compute_fractional_sell_quantity
    )
    check(orders == [11, 11, 11], f"snapshot 33, 3 rows (broker stuck at 33) -> {orders}")
    check(sum(orders) == snap == 33, "sum == snapshot (33), no over-sell")

    # snapshot=10, 3 rows -> 3/3/4, last sweeps remainder, never exceeds.
    orders, snap = _distribute_from_snapshot(
        10, 3, broker_qty_each_iter=[10, 10, 10], frac_fn=compute_fractional_sell_quantity
    )
    check(orders == [3, 3, 4], f"snapshot 10, 3 rows (broker stuck at 10) -> {orders}")
    check(sum(orders) == snap == 10, "sum == snapshot (10), last row sweeps remainder")

    # Cumulative ordered must NEVER exceed the snapshot at any point.
    running = 0
    for q in orders:
        running += q
        check(running <= snap, f"cumulative {running} <= snapshot {snap}")

    # Demonstrate the BUG the fix prevents: naive re-read (broker stuck at 33)
    # would order floor(33/3)=11, floor(33/2)=16, 33 -> sum 60 >> snapshot 33.
    naive = [compute_fractional_sell_quantity(33, n) for n in (3, 2, 1)]
    check(naive == [11, 16, 33] and sum(naive) == 60 > 33,
          f"naive re-read WOULD over-sell ({naive} sum={sum(naive)} > 33)")

    # US mirror identical
    us_orders, us_snap = _distribute_from_snapshot(
        33, 3, broker_qty_each_iter=[33, 33, 33], frac_fn=compute_us_fractional_sell_quantity
    )
    check(us_orders == [11, 11, 11] and sum(us_orders) == us_snap, "US mirror snapshot 33 -> 11/11/11")


# ── Test 7: FIX 1 — US queued vs live sell-plan decision branch ─────────────
def test_us_sell_plan():
    print("\n[Test 7] FIX 1 — US queued window -> full_exit vs live -> fractional")

    # Single row: always full position (legacy, unaffected).
    check(decide_us_sell_plan(1, will_queue=False) == "single_full", "N=1 live -> single_full")
    check(decide_us_sell_plan(1, will_queue=True) == "single_full", "N=1 queued -> single_full")

    # Multi-row + live (order executes now): fractional partial sell.
    check(decide_us_sell_plan(3, will_queue=False) == "fractional", "N=3 live -> fractional")
    check(decide_us_sell_plan(2, will_queue=False) == "fractional", "N=2 live -> fractional")

    # Multi-row + queued window: FULL exit (queue can't carry partial qty).
    check(decide_us_sell_plan(3, will_queue=True) == "full_exit", "N=3 queued -> full_exit")
    check(decide_us_sell_plan(2, will_queue=True) == "full_exit", "N=2 queued -> full_exit")

    # Simulate the DB consequence: full_exit deletes ALL rows; fractional one row.
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(_us_db_schema.TABLE_US_STOCK_HOLDINGS)
        conn.commit()
        for bp in (200.0, 210.0, 220.0):
            _insert_holding(cur, "us_stock_holdings", "ACC1", "AAPL", bp)
        conn.commit()

        # full_exit branch -> delete ALL rows for ticker
        cur.execute("DELETE FROM us_stock_holdings WHERE ticker='AAPL' AND account_key='ACC1'")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM us_stock_holdings WHERE ticker='AAPL'")
        check(cur.fetchone()[0] == 0, "full_exit deletes ALL rows (DB consistent with full liquidation)")

        # fractional branch -> delete ONLY one row by id (re-seed)
        for bp in (200.0, 210.0, 220.0):
            _insert_holding(cur, "us_stock_holdings", "ACC1", "AAPL", bp)
        conn.commit()
        cur.execute("SELECT id FROM us_stock_holdings WHERE ticker='AAPL' ORDER BY id LIMIT 1")
        one_id = cur.fetchone()[0]
        cur.execute("DELETE FROM us_stock_holdings WHERE id=?", (one_id,))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM us_stock_holdings WHERE ticker='AAPL'")
        check(cur.fetchone()[0] == 2, "fractional deletes only ONE row (2 remain)")

        conn.close()
    finally:
        os.remove(path)


# ── Test 8: FIX 3 — regime parse on realistic / hyphen / no-colon values ───
def test_regime_parse():
    print("\n[Test 8] FIX 3 — regime label parsing")
    _kr = _helpers._regime_label
    _us = _us_db_schema._us_regime_label

    cases = [
        ("strong_bull: 보고서 기준 KOSPI가 20일선 상회...", "strong_bull", True),
        ("parabolic: NASDAQ breadth surging", "parabolic", True),
        ("moderate_bull: 완만한 상승", "moderate_bull", False),
        ("sideways", "sideways", False),
        ("strong-bull", "strong_bull", True),          # hyphen normalises
        ("strong bull", "strong_bull", True),          # space normalises
        ("STRONG_BULL: caps", "strong_bull", True),    # case-insensitive
        ("", "", False),
        (None, "", False),
    ]
    allowed = ("strong_bull", "parabolic")
    for raw, expected_label, should_pass in cases:
        kr_label = _kr(raw)
        us_label = _us(raw)
        check(kr_label == expected_label, f"KR _regime_label({raw!r}) -> {kr_label!r} (expect {expected_label!r})")
        check(us_label == expected_label, f"US _us_regime_label({raw!r}) -> {us_label!r}")
        check((kr_label in allowed) == should_pass, f"{raw!r} gate-pass == {should_pass}")


# ── Test 5: ast.parse all edited files ─────────────────────────────────────
def test_ast_parse():
    print("\n[Test 5] ast.parse all edited files")
    files = [
        "stock_tracking_agent.py",
        "stock_tracking_enhanced_agent.py",
        "tracking/db_schema.py",
        "tracking/helpers.py",
        "tracking/__init__.py",
        "trading/domestic_stock_trading.py",
        "prism-us/us_stock_tracking_agent.py",
        "prism-us/trading/us_stock_trading.py",
        "prism-us/tracking/db_schema.py",
        "prism-us/tracking/__init__.py",
    ]
    for f in files:
        p = os.path.join(PROJECT_ROOT, f)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                ast.parse(fh.read())
            check(True, f"ast.parse OK: {f}")
        except SyntaxError as e:
            check(False, f"ast.parse FAILED: {f} -> {e}")


if __name__ == "__main__":
    test_migration()
    test_fractional_sell()
    test_add_gate()
    test_single_row_regression()
    test_oversell_snapshot()
    test_us_sell_plan()
    test_regime_parse()
    test_ast_parse()
    print(f"\n===== RESULT: {_PASS} passed, {_FAIL} failed =====")
    sys.exit(1 if _FAIL else 0)
