from __future__ import annotations

import sqlite3

from collector import funding, open_interest


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {"timestamp": "2000", "openInterest": "106.0",
                     "singleOpenInterest": "53.0"},
                    {"timestamp": "1000", "openInterest": "100.0",
                     "singleOpenInterest": "50.0"},
                ]
            },
        }


def test_fetch_open_interest_uses_official_hourly_contract(monkeypatch) -> None:
    seen = {}

    def fake_get(url, *, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return _Response()

    monkeypatch.setattr(open_interest.requests, "get", fake_get)
    rows = open_interest.fetch_open_interest_page()
    assert seen["url"].endswith("/v5/market/open-interest")
    assert seen["params"] == {
        "category": "linear", "symbol": "BTCUSDT",
        "intervalTime": "1h", "limit": 200,
    }
    assert rows[0] == (2000, 106.0, 53.0)


def test_upsert_open_interest_is_idempotent(tmp_path) -> None:
    path = tmp_path / "oi.db"
    assert open_interest.upsert_open_interest_rows(
        path, [(1000, 100.0, 50.0), (2000, 106.0, 53.0)]
    ) == 2
    assert open_interest.upsert_open_interest_rows(
        path, [(2000, 108.0, 54.0)]
    ) == 1
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT timestamp, open_interest, single_open_interest "
        "FROM open_interest ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert rows == [(1000, 100.0, 50.0), (2000, 108.0, 54.0)]


class _FundingResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "retCode": 0,
            "result": {"list": [
                {"fundingRateTimestamp": "2000", "fundingRate": "0.0001"},
                {"fundingRateTimestamp": "1000", "fundingRate": "-0.0002"},
            ]},
        }


def test_funding_collector_fetches_and_upserts_latest_history(
    tmp_path, monkeypatch
) -> None:
    seen = {}

    def fake_get(url, *, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return _FundingResponse()

    monkeypatch.setattr(funding.requests, "get", fake_get)
    rows = funding.fetch_funding_page()
    assert seen["url"].endswith("/v5/market/funding/history")
    assert seen["params"] == {
        "category": "linear", "symbol": "BTCUSDT", "limit": 200,
    }
    path = tmp_path / "market.db"
    assert funding.upsert_funding_rows(path, rows) == 2
    assert funding.upsert_funding_rows(path, [(2000, 0.0003)]) == 1
    conn = sqlite3.connect(path)
    stored = conn.execute(
        "SELECT funding_time, rate FROM funding ORDER BY funding_time"
    ).fetchall()
    conn.close()
    assert stored == [(1000, -0.0002), (2000, 0.0003)]
