import copy
from datetime import datetime, timedelta, timezone
import json

import pytest

from tools import collect_trend_replay_data as collector


def packet(rows):
    return {"packet_schema_version": 3, "analysis_contract_version": "entry-quality-harness-v2",
            "packet_id": "a" * 24, "market": "US", "as_of": collector.CUTOFF, "analysis_rows": rows}


def row(ticker="SNDK", ref="b" * 16, closed=True):
    return {"ticker": ticker, "decision_ref": ref, "decided_at": "2026-09-08T19:02:00Z",
            "outcomes": {"strategy_entry_at": "2026-09-08T19:02:01Z" if closed else None,
                         "strategy_closed_at": "2026-09-09T13:08:04Z" if closed else None,
                         "actual_exclusion_reason": "FILL_NOT_CONFIRMED"}}


def raw(when):
    return {"provider_timestamp": when, "open": 100, "high": 102, "low": 99, "close": 101,
            "volume": 1000, "adj_close": 100.5, "dividends": 0.5, "stock_splits": 0, "capital_gains": None}


def response(rows):
    return {"status": "received", "raw_rows": rows, "exchange": "NMS",
            "retrieved_at": "2026-09-10T01:00:00Z", "yfinance_version": "fixture"}


def test_actual_calendar_early_close_and_holiday_exclusion():
    request = {"ticker": "SNDK", "market": "US", "interval": "1d",
               "start": "2025-11-26T00:00:00Z", "end": "2025-11-28T19:00:00Z"}
    data = collector.normalize(request, response([raw("2025-11-27T00:00:00-05:00"),
                                                  raw("2025-11-28T00:00:00-05:00")]))
    assert data["bars"][0]["bar_close_at"] == "2025-11-28T18:00:00Z"
    assert data["exclusions"] == {"NON_SESSION_DATE": 1}
    assert data["bars"][0]["dividends"] == 0.5


def test_incomplete_daily_and_minute_bar_excluded():
    request = {"ticker": "SNDK", "market": "US", "interval": "1d",
               "start": "2026-09-08T00:00:00Z", "end": collector.CUTOFF}
    data = collector.normalize(request, response([raw("2026-09-09T00:00:00-04:00")]))
    assert not data["bars"]
    assert data["exclusions"]["INCOMPLETE_AT_REQUEST_CUTOFF"] == 1
    request["interval"] = "5m"
    data = collector.normalize(request, response([raw("2026-09-09T12:15:00-04:00")]))
    assert not data["bars"]


def test_collection_preserves_packet_and_never_fabricates_observation_or_stop(tmp_path):
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet([row(), row("SPGI", "c" * 16)])))
    before = path.read_bytes()
    def fetch(request):
        if request["interval"] == "1d":
            start = datetime(2026, 4, 1, tzinfo=timezone(timedelta(hours=-4)))
            return response([raw((start + timedelta(days=i)).isoformat()) for i in range(161)])
        return response([raw("2026-09-09T09:00:00-04:00")])
    artifact = collector.collect([path], fetch)
    assert path.read_bytes() == before
    assert artifact["kind"] == "RECONSTRUCTED_REPLAY"
    assert all(r["eligible_60_bars"] for r in artifact["decisions"])
    assert all(r["last_completed_close_at"] < r["decided_at"] for r in artifact["decisions"])
    assert artifact["closed_trades"][0]["research_role"] == "DISCOVERY"
    assert artifact["closed_trades"][1]["research_role"] == "ALREADY_SEEN_NOT_HOLDOUT"
    assert all(r["original_stop_threshold"] is None for r in artifact["closed_trades"])
    assert artifact["closed_trades"][0]["intraday"]["1m"]["extended_bars"] == 1
    assert "observed_at" not in json.dumps(artifact)
    assert collector.verify(json.loads(json.dumps(artifact))) == artifact["artifact_sha256"]
    rebuilt = collector.collect([path], collector.saved_fetcher(artifact))
    assert rebuilt["datasets"] == artifact["datasets"]
    assert rebuilt["decisions"] == artifact["decisions"]
    assert rebuilt["closed_trades"] == artifact["closed_trades"]
    altered = copy.deepcopy(artifact)
    altered["data_cutoff"] = "changed"
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        collector.verify(altered)


def test_unknown_exchange_is_not_guessed():
    request = {"interval": "1d", "start": "2026-09-08T00:00:00Z", "end": collector.CUTOFF}
    received = response([raw("2026-09-08T00:00:00-04:00")])
    received["exchange"] = None
    assert collector.normalize(request, received)["bars"] == []


def test_partial_entry_bar_is_not_treated_as_post_entry_observation():
    request = {"interval": "5m", "start": "2026-09-08T19:02:03Z", "end": collector.CUTOFF}
    result = collector.normalize(request, response([raw("2026-09-08T15:00:00-04:00")]))
    assert result["bars"] == []
    assert result["exclusions"] == {"BAR_START_BEFORE_REQUEST_START": 1}


@pytest.mark.parametrize("change", [
    {"ticker": "ACCOUNT_SECRET:/bad"}, {"decision_ref": "raw-account"},
    {"decided_at": "2026-09-10T00:00:00Z"},
    {"outcomes": {"strategy_entry_at": None, "strategy_closed_at": collector.CUTOFF}},
])
def test_input_identity_and_chronology_fail_closed(tmp_path, change):
    record = row()
    record.update(change)
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet([record])))
    with pytest.raises(ValueError):
        collector.read_packets([path])


def test_empty_kr_packet_does_not_invent_candidates(tmp_path):
    source = packet([])
    source["market"] = "KR"
    path = tmp_path / "kr.json"
    path.write_text(json.dumps(source))
    artifact = collector.collect([path], lambda _: pytest.fail("no request allowed"))
    assert collector.summary(artifact)["decision_count"] == 0


def test_long_trade_minute_requests_bounded_to_five_days():
    record = {**row(), "market": "US", "strategy_entry_at": "2026-09-01T18:58:54Z",
              "strategy_closed_at": collector.CUTOFF}
    requests = collector.requests_for([record])
    assert len(requests) == 5  # one daily, two chunks each for 1m and 5m
    assert all(collector.stamp(r["end"]) - collector.stamp(r["start"]) <= timedelta(days=5)
               for r in requests if r["interval"] != "1d")
