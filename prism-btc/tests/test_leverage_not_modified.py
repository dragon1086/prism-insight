"""Bybit 110043 (leverage not modified) is the desired state, not an error event."""
from types import SimpleNamespace

import pytest
from live import demo, swing, tracking


@pytest.fixture
def conn():
    connection = tracking.get_connection(":memory:")
    tracking.ensure_schema(connection)
    yield connection
    connection.close()


def adapter(kind, conn, session):
    if kind == "swing":
        return swing.ExchangeBackend(conn, session)
    obj = demo.DemoAdapter.__new__(demo.DemoAdapter)
    obj.conn, obj.sess, obj.mode = conn, session, "demo"
    return obj


def _pybit_error(code):
    exceptions = pytest.importorskip("pybit.exceptions")
    return exceptions.InvalidRequestError(
        request="POST /v5/position/set-leverage", message="leverage not modified",
        status_code=code, time="00:00:00", resp_headers={})


def _errors(conn):
    return [row[0] for row in conn.execute("SELECT message FROM btc_events WHERE level='error'")]


@pytest.mark.parametrize("kind", ["demo", "swing"])
@pytest.mark.parametrize("raised", [False, True])
def test_leverage_not_modified_is_idempotent_success(conn, monkeypatch, kind, raised):
    calls = []

    def set_leverage(**kwargs):
        calls.append(kwargs)
        if raised:
            raise _pybit_error(110043)
        return {"retCode": 110043, "retMsg": "leverage not modified"}

    monkeypatch.setattr(demo.time, "sleep", lambda *a: pytest.fail("no retry for 110043"))
    obj = adapter(kind, conn, SimpleNamespace(set_leverage=set_leverage))
    assert obj._call("set_leverage", category="linear", symbol="BTCUSDT",
                     buyLeverage="3", sellLeverage="3") is not None
    assert len(calls) == 1
    assert _errors(conn) == []


@pytest.mark.parametrize("kind", ["demo", "swing"])
def test_other_leverage_failures_still_logged(conn, monkeypatch, kind):
    def set_leverage(**kwargs):
        raise _pybit_error(110013)

    monkeypatch.setattr(demo.time, "sleep", lambda *a: None)
    obj = adapter(kind, conn, SimpleNamespace(set_leverage=set_leverage))
    assert obj._call("set_leverage", category="linear", symbol="BTCUSDT",
                     buyLeverage="3", sellLeverage="3") is None
    assert len(_errors(conn)) == 1


@pytest.mark.parametrize("kind", ["demo", "swing"])
def test_110043_from_other_methods_is_not_swallowed(conn, monkeypatch, kind):
    def place_order(**kwargs):
        raise _pybit_error(110043)

    obj = adapter(kind, conn, SimpleNamespace(place_order=place_order))
    assert obj._call("place_order", category="linear", symbol="BTCUSDT") is None
    assert len(_errors(conn)) == 1
