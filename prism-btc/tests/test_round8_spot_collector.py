import sqlite3

from analysis import round8_spot_collector as collector


def _kline(open_time: int, close_time: int) -> list:
    return [
        open_time,
        "100.0",
        "110.0",
        "90.0",
        "105.0",
        "12.0",
        close_time,
        "1260.0",
    ]


def _insert_row(
    conn: sqlite3.Connection,
    *,
    open_time: int,
    confirmed: int,
) -> None:
    conn.execute(
        "INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "BTCUSDT",
            collector.INTERVAL,
            open_time,
            100.0,
            110.0,
            90.0,
            105.0,
            12.0,
            1260.0,
            confirmed,
            1,
        ),
    )
    conn.commit()


def test_fetch_cursor_ignores_newer_unconfirmed_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(collector.DDL)
    _insert_row(conn, open_time=1_000, confirmed=1)
    _insert_row(conn, open_time=2_000, confirmed=0)
    _insert_row(conn, open_time=3_000, confirmed=0)

    assert collector.get_fetch_cursor(conn, "BTCUSDT") == 1_001


def test_main_refetches_and_promotes_existing_unconfirmed_bar(tmp_path, monkeypatch):
    db_path = tmp_path / "btc_spot.db"
    conn = sqlite3.connect(db_path)
    conn.execute(collector.DDL)
    _insert_row(conn, open_time=1_000, confirmed=1)
    _insert_row(conn, open_time=2_000, confirmed=0)
    conn.close()

    requested_cursors = []

    def fake_fetch(symbol: str, start_ms: int) -> list:
        requested_cursors.append((symbol, start_ms))
        return [
            _kline(2_000, 2_999),
            _kline(3_000, 20_000),
        ]

    monkeypatch.setattr(collector, "DB", db_path)
    monkeypatch.setattr(collector, "SYMBOLS", ("BTCUSDT",))
    monkeypatch.setattr(collector, "fetch", fake_fetch)
    monkeypatch.setattr(collector.time, "time", lambda: 10.0)

    collector.main()

    assert requested_cursors == [("BTCUSDT", 1_001)]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT open_time, confirmed FROM spot_klines ORDER BY open_time"
    ).fetchall()
    conn.close()
    assert rows == [(1_000, 1), (2_000, 1), (3_000, 0)]
