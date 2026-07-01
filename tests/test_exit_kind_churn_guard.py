"""
exit_kind churn guard (Option A) — re-entry cooldown keyed on exit REASON, not
just realised P&L sign.

Incident 2026-07-01 (MU, US): a mechanical stop-loss tagged the position out at a
marginal +0.39% PROFIT, then the orchestrator re-bought it 4 min later. The loss-
only cooldown did not fire because profit_rate > 0. This test locks in the fix:
a stop / trend_exit close is churn-risk regardless of P&L sign, while a genuine
profit-taking (ai/target) re-entry is still allowed. Pre-migration rows (no
exit_kind column, or NULL) fall back to the legacy sign-based behaviour.

Pure-unit + temp-SQLite; no live DB. Mirrors tests/test_issue_288_pyramiding.py style.

Run:
    python3 tests/test_exit_kind_churn_guard.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_spec = importlib.util.spec_from_file_location(
    "reentry_cooldown_for_exitkind_test",
    os.path.join(PROJECT_ROOT, "reentry_cooldown.py"),
)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

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


def _mkdb(with_exit_kind=True):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    cols = "account_key TEXT, ticker TEXT, sell_date TEXT, profit_rate REAL"
    if with_exit_kind:
        cols += ", exit_kind TEXT"
    conn.execute(f"CREATE TABLE us_trading_history ({cols})")
    conn.commit()
    return path, conn


def _recent(hours_ago=0.1):
    return (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_classifier():
    print("\n[Test 1] classify_exit_kind")
    check(rc.classify_exit_kind("손절 조건 도달 (손절가: 1000원)") == "stop", "KR 손절 -> stop")
    check(rc.classify_exit_kind("Stop-loss condition reached (stop-loss: $10)") == "stop", "US stop -> stop")
    check(rc.classify_exit_kind("TIER1_STOPLOSS: price<=stop") == "stop", "TIER1 -> stop")
    check(rc.classify_exit_kind("TIER1.5_MA50 close below 50d") == "trend_exit", "TIER1.5 -> trend_exit")
    check(rc.classify_exit_kind("목표가 달성 (목표가: 1200원)") == "target", "목표가 -> target")
    check(rc.classify_exit_kind("AI judgment: weakening") == "ai", "unknown -> ai")
    check(rc.classify_exit_kind("whatever", explicit="trend_exit") == "trend_exit", "explicit hint wins")


def test_stop_at_profit_blocks():
    print("\n[Test 2] stop/trend exit at a PROFIT still blocks re-entry (the MU fix)")
    for kind in ("stop", "trend_exit"):
        path, conn = _mkdb(True)
        conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?,?)",
                     ("A", "MU", _recent(0.1), 0.4, kind))
        conn.commit(); conn.close()
        v = rc.reentry_block("US", "MU", db_path=path)
        check(v is not None and v["risk_exit"] and v["window_hours"] == rc.COOLDOWN_LOSS_HOURS,
              f"{kind} +0.4% profit -> BLOCK (loss window)")
        os.remove(path)


def test_profit_taking_allowed():
    print("\n[Test 3] genuine profit-taking (ai/target) re-entry still allowed")
    for kind in ("ai", "target"):
        path, conn = _mkdb(True)
        conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?,?)",
                     ("A", "MU", _recent(0.1), 5.0, kind))
        conn.commit(); conn.close()
        check(rc.reentry_block("US", "MU", db_path=path) is None,
              f"{kind} +5% win -> ALLOW (COOLDOWN_HOURS window)")
        os.remove(path)


def test_backward_compatible():
    print("\n[Test 4] backward compatibility (NULL / pre-migration column)")
    # NULL exit_kind, loss -> block (legacy sign)
    path, conn = _mkdb(True)
    conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?,?)",
                 ("A", "MU", _recent(0.1), -3.0, None))
    conn.commit(); conn.close()
    check(rc.reentry_block("US", "MU", db_path=path) is not None, "NULL exit_kind + loss -> BLOCK")
    os.remove(path)
    # NULL exit_kind, win -> allow
    path, conn = _mkdb(True)
    conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?,?)",
                 ("A", "MU", _recent(0.1), 0.4, None))
    conn.commit(); conn.close()
    check(rc.reentry_block("US", "MU", db_path=path) is None, "NULL exit_kind + profit -> ALLOW")
    os.remove(path)
    # No exit_kind column at all (pre-migration) -> fallback still works
    path, conn = _mkdb(False)
    conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?)", ("A", "MU", _recent(0.1), -3.0))
    conn.commit(); conn.close()
    check(rc.reentry_block("US", "MU", db_path=path) is not None, "no column + loss -> BLOCK (fallback)")
    os.remove(path)


def test_recent_risk_exit():
    print("\n[Test 5] recent_risk_exit (journal churn guard source)")
    # stop at profit -> risk exit dict; recent_loss (loss-only) -> None
    path, conn = _mkdb(True)
    conn.execute("INSERT INTO us_trading_history VALUES (?,?,?,?,?)",
                 ("A", "MU", _recent(1.0), 0.4, "stop"))
    conn.commit(); conn.close()
    os.environ["REENTRY_COOLDOWN_DB"] = path
    try:
        check(rc.recent_risk_exit("MU", "US") is not None, "recent_risk_exit fires on stop@profit")
        check(rc.recent_loss("MU", "US") is None, "legacy recent_loss stays loss-only (None on profit)")
    finally:
        del os.environ["REENTRY_COOLDOWN_DB"]
    os.remove(path)


def _run():
    test_classifier()
    test_stop_at_profit_blocks()
    test_profit_taking_allowed()
    test_backward_compatible()
    test_recent_risk_exit()
    print(f"\n===== RESULT: {_PASS} passed, {_FAIL} failed =====")
    return _FAIL


def test_exit_kind_churn_guard_pytest():
    assert _run() == 0


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
