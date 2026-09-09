from copy import deepcopy
import json
import logging
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from prism_core import kr_legacy_adx_identity_v2 as core
from tools import run_kr_legacy_adx_identity_v2 as tool
from test_kr_legacy_adx_research import fixture, source_fixture


def dataset(symbol="005930.KS", kind="EQUITY"):
    record, mapping, schedule = fixture()
    data = mapping["dataset"]
    data.update(request={"ticker": symbol}, exclusions={},
                identity_metadata={"symbol": symbol, "instrumentType": kind,
                                   "exchangeName": "KSC" if symbol.endswith(".KS") else "KOE",
                                   "exchangeTimezoneName": "Asia/Seoul",
                                   "firstTradeDate": 946684800, "currency": "KRW"})
    for b in data["bars"]:
        b["provider_timestamp"] = b["session_date"] + "T00:00:00+09:00"
    data["raw_rows"] = deepcopy(data["bars"])
    return record, data, schedule


def test_same_code_equity_not_mutualfund_and_both_equity_still_ambiguous():
    _, equity, _ = dataset()
    _, fund, _ = dataset("005930.KQ", "MUTUALFUND")
    mapping = core.resolve_mapping("005930", [equity, fund])
    assert mapping["status"] == "OK"
    assert mapping["provider_symbol"] == "005930.KS"
    assert mapping["identity_checks"]["005930.KQ"] == ["PROVIDER_TYPE_NOT_EQUITY"]
    fund["identity_metadata"]["instrumentType"] = "EQUITY"
    assert core.resolve_mapping("005930", [equity, fund])["status"] == "AMBIGUOUS_EQUITY_IDENTITIES"


@pytest.mark.parametrize("key,value,reason", [
    ("instrumentType", None, "PROVIDER_TYPE_NOT_EQUITY"),
    ("symbol", "000001.KS", "PROVIDER_SYMBOL_MISMATCH"),
    ("exchangeName", "KOE", "PROVIDER_EXCHANGE_MISMATCH"),
    ("exchangeTimezoneName", None, "PROVIDER_TIMEZONE_UNVERIFIED"),
    ("firstTradeDate", None, "PROVIDER_FIRST_TRADE_DATE_UNKNOWN"),
    ("firstTradeDate", float("nan"), "PROVIDER_FIRST_TRADE_DATE_UNKNOWN"),
])
def test_unknown_or_mismatching_identity_never_eligible(key, value, reason):
    _, data, _ = dataset()
    data["identity_metadata"][key] = value
    assert reason in core.identity_reasons(data)
    assert core.resolve_mapping("005930", [data])["status"] == "PROVIDER_EQUITY_IDENTITY_UNVERIFIED"


def test_raw_timezone_must_agree_with_metadata():
    _, data, _ = dataset()
    data["raw_rows"][0]["provider_timestamp"] = "2026-01-01T00:00:00+00:00"
    assert "PROVIDER_BAR_TIMEZONE_MISMATCH" in core.identity_reasons(data)


def test_first_trade_date_after_entry_anchor_blocks_record():
    record, data, schedule = dataset()
    day = core.v1.session_date(record["recorded_entry_at"])
    anchor = core.v1.timestamp(schedule[day]["open"]).timestamp()
    data["identity_metadata"]["firstTradeDate"] = anchor + 1
    mapping = core.resolve_mapping("005930", [data])
    assert core.feature(record, mapping, schedule)["reasons"] == ["PROVIDER_FIRST_TRADE_AFTER_ENTRY_ANCHOR"]
    data["identity_metadata"]["firstTradeDate"] = anchor
    assert core.feature(record, mapping, schedule)["status"] == "OK"


def test_no_old_unidentified_rows_relabelled():
    record, mapping, schedule = fixture()
    mapping["dataset"]["request"] = {"ticker": "005930.KS"}
    assert core.feature(record, mapping, schedule)["status"] == "MISSING"
    source = source_fixture([record])
    with pytest.raises(ValueError, match="identity_v2"):
        core.build_study(source, {"kind": "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_V1"})


def test_worker_preserves_same_history_metadata_without_info_calls(monkeypatch):
    rows = []
    called = []
    metadata = {"symbol": "005930.KS", "instrumentType": "EQUITY", "exchangeName": "KSC",
                "exchangeTimezoneName": "Asia/Seoul", "firstTradeDate": 946684800, "currency": "KRW",
                "longName": "not allowed", "private": "CANARY"}
    class Ticker:
        def __init__(self, symbol):
            assert symbol == "005930.KS"
            self._price_history = SimpleNamespace(_history_metadata=metadata)
        def history(self, **kwargs):
            called.append(kwargs)
            return pd.DataFrame({"Open": [100], "High": [101], "Low": [99], "Close": [100],
                                 "Volume": [100], "Dividends": [0], "Stock Splits": [0]},
                                index=pd.DatetimeIndex(["2026-09-08"], tz="Asia/Seoul"))
        def get_history_metadata(self):
            pytest.fail("must not issue separate metadata request")
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker,
                        set_tz_cache_location=lambda _: None, __version__="test"))
    request = {"ticker": "005930.KS", "start": "2026-01-01T00:00:00Z", "end": core.v1.CUTOFF}
    prior_logging_disable = logging.root.manager.disable
    try:
        tool._fetch_child(request, SimpleNamespace(send=rows.append, close=lambda: None))
    finally:
        logging.disable(prior_logging_disable)
    assert len(called) == len(rows) == 1
    assert rows[0]["identity_metadata"]["symbol"] == "005930.KS"
    assert "CANARY" not in json.dumps(rows)
    assert "longName" not in rows[0]["identity_metadata"]
    assert rows[0]["raw_rows"][0]["provider_timestamp"].endswith("+09:00")
    assert called[0]["auto_adjust"] is False and called[0]["repair"] is False


def test_v2_separate_books_and_family8_and_hash_replay():
    record, data, schedule = dataset()
    source = source_fixture([record, {**record, "source_record_ref": "b", "legacy_book_ref": "book-b"}])
    collection = {"kind": "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_IDENTITY_V2",
                  "source_artifact_sha256": source["artifact_sha256"], "registration_sha256": "test",
                  "quarantined_v1_collection_sha256": "v1", "schedule": schedule,
                  "mapping": {"005930": core.resolve_mapping("005930", [data])}}
    collection["artifact_sha256"] = core.v1.digest(collection)
    result = core.build_study(source, collection)
    assert len(result["books"]) == 2
    assert result["registry"]["multiple_testing_family"] == 8
    assert result["v1_status"] == "PROVIDER_IDENTITY_UNVERIFIED_NOT_VALIDATED_SAMPLE"
    assert result["independent_holdout"] is False
    assert result == core.build_study(source, collection)
    assert "decision_ref" not in json.dumps(result)
    report = tool.korean_report(result)
    assert "99.375%" in report and "98.75%" not in report
    assert "MUTUALFUND" in report and "v1" in report


def test_family8_interval_no_narrower_than_frozen_family4():
    record, data, schedule = dataset()
    f = core.feature(record, core.resolve_mapping("005930", [data]), schedule)
    f["adx14"] = 10
    rows = [{"source_record_ref": str(i), "recorded_return_pct": i - 10,
             "feature": f, "entry_session_date": str(i)} for i in range(40)]
    old = core.v1.metrics(rows, "H1")["bonferroni_date_cluster_interval_pp"]
    new = core.metrics(rows, "H1")["bonferroni_date_cluster_interval_pp"]
    assert new[0] <= old[0] <= old[1] <= new[1]
