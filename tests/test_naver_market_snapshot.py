"""Tests for the emergency Naver market snapshot fallback.

All HTTP responses are fixtures.  CI must never depend on live Naver/KRX data.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from cores.naver_market_snapshot import (
    MarketSnapshotBundle,
    NaverSnapshotError,
    _parse_daily_xml,
    fetch_naver_snapshot_bundle,
)


TRADE_DATE = "20260722"


class _Response:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


def _stock(
    code: str,
    *,
    close: int,
    change: int,
    volume: int,
    amount: int,
    market_cap: int,
    market: str,
    traded_at: str = "2026-07-22T14:46:10+09:00",
):
    return {
        "stockType": "domestic",
        "stockEndType": "stock",
        "itemCode": code,
        "stockName": f"stock-{code}",
        "closePriceRaw": str(close),
        "compareToPreviousClosePriceRaw": str(change),
        "accumulatedTradingVolumeRaw": str(volume),
        "accumulatedTradingValueRaw": str(amount),
        "marketValueRaw": str(market_cap),
        "localTradedAt": traded_at,
        "stockExchangeType": {"name": market},
    }


def _daily_xml(code: str) -> str:
    rows = {
        "005930": [
            "20260721|69000|71000|68000|69000|900000",
            "20260722|70000|73000|69500|70000|1000000",
        ],
        "035720": [
            "20260721|51000|52000|50000|51000|1500000",
            "20260722|50500|51500|49500|50000|2000000",
        ],
    }[code]
    items = "".join(f'<item data="{row}" />' for row in rows)
    return f"<?xml version='1.0'?><protocol><chartdata>{items}</chartdata></protocol>"


class _FixtureGet:
    def __init__(self, *, stale=False, fail_detail_code=None):
        self.calls = Counter()
        traded_at = (
            "2026-07-21T14:46:10+09:00"
            if stale
            else "2026-07-22T14:46:10+09:00"
        )
        self.market_rows = {
            "KOSPI": [
                _stock(
                    "005930",
                    close=70000,
                    change=1000,
                    volume=1000000,
                    amount=20_000_000_000,
                    market_cap=500_000_000_000,
                    market="KOSPI",
                    traded_at=traded_at,
                ),
                _stock(
                    "000660",
                    close=50000,
                    change=1000,
                    volume=100000,
                    amount=5_000_000_000,
                    market_cap=400_000_000_000,
                    market="KOSPI",
                    traded_at=traded_at,
                ),
            ],
            "KOSDAQ": [
                _stock(
                    "035720",
                    close=50000,
                    change=-1000,
                    volume=2000000,
                    amount=15_000_000_000,
                    market_cap=600_000_000_000,
                    market="KOSDAQ",
                    traded_at=traded_at,
                )
            ],
        }
        self.fail_detail_code = fail_detail_code

    def __call__(self, url, *, params, headers, timeout):
        if "marketValue" in url:
            market = url.rsplit("/", 1)[-1]
            self.calls[f"bulk:{market}"] += 1
            rows = self.market_rows[market]
            return _Response(
                payload={
                    "stocks": rows,
                    "totalCount": len(rows),
                    "page": 1,
                    "pageSize": 100,
                }
            )

        code = params["symbol"]
        self.calls[f"detail:{code}"] += 1
        if code == self.fail_detail_code:
            return _Response(text="not xml")
        return _Response(text=_daily_xml(code))


def _fetch(fake_get, **kwargs):
    return fetch_naver_snapshot_bundle(
        TRADE_DATE,
        request_get=fake_get,
        min_stock_count=3,
        detail_min_amount=10_000_000_000,
        min_detail_coverage=0.98,
        max_workers=2,
        max_attempts=1,
        retry_wait_sec=0,
        **kwargs,
    )


def test_fetch_builds_current_previous_and_cap_contract():
    fake_get = _FixtureGet()

    bundle = _fetch(fake_get)

    assert isinstance(bundle, MarketSnapshotBundle)
    assert bundle.source == "naver"
    assert bundle.prev_date == "20260721"
    assert list(bundle.snapshot.columns) == [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Amount",
    ]
    assert bundle.snapshot.loc["005930", "High"] == 73000
    assert bundle.snapshot.loc["035720", "Low"] == 49500
    assert pd.isna(bundle.snapshot.loc["000660", "Open"])
    assert bundle.prev_snapshot.loc["005930", "Volume"] == 900000
    assert bundle.prev_snapshot.loc["000660", "Close"] == 49000
    assert bundle.cap_df.loc["035720", "시가총액"] == 600_000_000_000
    assert fake_get.calls["detail:005930"] == 1
    assert fake_get.calls["detail:035720"] == 1
    assert fake_get.calls["detail:000660"] == 0


def test_fetch_rejects_stale_bulk_trade_date():
    with pytest.raises(NaverSnapshotError, match="stale"):
        _fetch(_FixtureGet(stale=True))


def test_fetch_fails_closed_when_detail_coverage_is_too_low():
    with pytest.raises(NaverSnapshotError, match="coverage"):
        _fetch(_FixtureGet(fail_detail_code="035720"))


def test_fetch_rejects_bulk_rows_without_raw_unit_fields():
    fake_get = _FixtureGet()
    del fake_get.market_rows["KOSPI"][0]["accumulatedTradingValueRaw"]
    fake_get.market_rows["KOSPI"][0]["accumulatedTradingValue"] = "20,000"

    with pytest.raises(NaverSnapshotError, match="coverage"):
        _fetch(fake_get)


def test_daily_parser_never_resolves_external_xml_entities():
    malicious = b'''<?xml version="1.0"?>
<!DOCTYPE protocol [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<protocol><chartdata>
<item data="20260721|69000|71000|68000|69000|900000" />
<item data="20260722|&xxe;|73000|69500|70000|1000000" />
</chartdata></protocol>'''

    with pytest.raises(ValueError, match="invalid literal"):
        _parse_daily_xml(malicious, TRADE_DATE)


def test_trigger_batch_uses_naver_bundle_after_krx_failure(monkeypatch):
    import trigger_batch

    fake_get = _FixtureGet()
    naver_bundle = _fetch(fake_get)
    monkeypatch.setattr(
        trigger_batch,
        "get_snapshot",
        lambda _date: (_ for _ in ()).throw(RuntimeError("KRX down")),
    )
    monkeypatch.setattr(
        trigger_batch,
        "fetch_naver_snapshot_bundle",
        lambda _date, **_kwargs: naver_bundle,
    )

    result = trigger_batch.load_market_snapshot_bundle(TRADE_DATE)

    assert result is naver_bundle
    assert result.source == "naver"


def test_trigger_batch_keeps_krx_as_primary(monkeypatch):
    import trigger_batch

    snapshot = pd.DataFrame(
        {
            "Open": [10],
            "High": [12],
            "Low": [9],
            "Close": [11],
            "Volume": [100],
            "Amount": [1100],
        },
        index=["005930"],
    )
    previous = snapshot.copy()
    cap = pd.DataFrame({"시가총액": [1_000_000]}, index=["005930"])
    monkeypatch.setattr(trigger_batch, "get_snapshot", lambda _date: snapshot)
    monkeypatch.setattr(
        trigger_batch,
        "get_previous_snapshot",
        lambda _date: (previous, "20260721"),
    )
    monkeypatch.setattr(trigger_batch, "get_market_cap_df", lambda _date: cap)
    monkeypatch.setattr(
        trigger_batch,
        "fetch_naver_snapshot_bundle",
        lambda _date, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback called")),
    )

    result = trigger_batch.load_market_snapshot_bundle(TRADE_DATE)

    assert result.source == "krx"
    assert result.snapshot is snapshot
    assert result.prev_snapshot is previous
    assert result.cap_df is cap


def test_trigger_batch_raises_typed_error_when_both_sources_fail(monkeypatch):
    import trigger_batch

    monkeypatch.setattr(
        trigger_batch,
        "get_snapshot",
        lambda _date: (_ for _ in ()).throw(RuntimeError("KRX down")),
    )
    monkeypatch.setattr(
        trigger_batch,
        "fetch_naver_snapshot_bundle",
        lambda _date, **_kwargs: (_ for _ in ()).throw(RuntimeError("Naver down")),
    )

    with pytest.raises(trigger_batch.MarketSnapshotUnavailableError):
        trigger_batch.load_market_snapshot_bundle(TRADE_DATE)


def test_orchestrator_alerts_when_both_market_sources_fail(monkeypatch):
    import stock_analysis_orchestrator
    import telegram_config
    import trigger_batch

    monkeypatch.setattr(
        trigger_batch,
        "run_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            trigger_batch.MarketSnapshotUnavailableError("both sources down")
        ),
    )
    send_alert = AsyncMock()
    monkeypatch.setattr(telegram_config, "send_market_data_failure_alert", send_alert)

    orchestrator = stock_analysis_orchestrator.StockAnalysisOrchestrator.__new__(
        stock_analysis_orchestrator.StockAnalysisOrchestrator
    )
    orchestrator.telegram_config = object()

    result = asyncio.run(orchestrator.run_trigger_batch("afternoon"))

    assert result == []
    send_alert.assert_awaited_once_with(orchestrator.telegram_config, mode="afternoon")
