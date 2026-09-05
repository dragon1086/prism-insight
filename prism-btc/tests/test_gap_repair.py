import sqlite3

import pytest

from analysis.repair_execution_gaps import repair
from collector.store import CREATE_TABLE


def database():
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_TABLE)
    for t in [0,3600000]:
        conn.execute("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)",("30m",t,100,101,99,100,1,100,1))
    conn.commit()
    return conn


def test_only_missing_candle_inserted_and_idempotent():
    conn=database()
    def fetch(*a,**kw):
        return [["1800000","100","102","99","101","1","101"],
                ["0","999","999","999","999","1","999"]]
    assert repair(conn)["missing_before"] == 1
    assert repair(conn,fetch=fetch,apply=True)["inserted"] == 1
    assert repair(conn,fetch=fetch,apply=True)["inserted"] == 0
    assert conn.execute("SELECT open FROM klines WHERE open_time=0").fetchone()[0] == 100


def test_missing_exchange_data_does_not_change_database():
    conn=database()
    with pytest.raises(ValueError):
        repair(conn,fetch=lambda *a,**kw: [],apply=True)
    assert conn.execute("SELECT count(*) FROM klines").fetchone()[0] == 2


def test_stale_unconfirmed_row_refreshed_but_confirmed_history_preserved():
    conn=database()
    conn.execute("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)",("30m",1800000,99,100,98,99,1,99,0))
    conn.commit()
    result=repair(conn,fetch=lambda *a,**kw: [["1800000","100","102","99","101","1","101"]],apply=True)
    assert result["refreshed_unconfirmed"] == 1
    assert result["missing_after"] == 0
    assert conn.execute("SELECT close,confirmed FROM klines WHERE open_time=1800000").fetchone() == (101,1)
