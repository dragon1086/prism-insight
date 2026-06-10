#!/usr/bin/env python3
"""Regression tests: the published signal must always carry the `market` field.

Root cause of the US buy omission (APH/MU/UNH): the BUY convenience publishers set
``scenario["market"] = "US"``, but ``publish_signal`` only copied a whitelist of
scenario keys into the outgoing message — ``market`` was dropped. The subscriber
then defaulted to ``market="KR"`` and routed US buys to the domestic KIS API, where
the price lookup returned 0 KRW and quantity = amount / 0 raised ``division by zero``.

SELL signals were unaffected because they pass market via ``extra_data`` (merged
wholesale), which is exactly the asymmetry these tests lock down.

Run (root suite):
    .venv/bin/python -m pytest tests/test_publish_market_propagation.py -q
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# --------------------------------------------------------------------------- #
# GCP Pub/Sub publisher
# --------------------------------------------------------------------------- #
def _make_gcp_publisher():
    from messaging.gcp_pubsub_signal_publisher import SignalPublisher

    pub = SignalPublisher(project_id="test-project", topic_id="test-topic")
    mock_client = MagicMock()
    future = MagicMock()
    future.result = MagicMock(return_value="msg-id")
    mock_client.publish = MagicMock(return_value=future)
    pub._publisher = mock_client
    pub._topic_path = "projects/test/topics/test"
    return pub, mock_client


def _gcp_published_payload(mock_client) -> dict:
    # publish(topic_path, message_bytes) → second positional arg is the payload.
    message_bytes = mock_client.publish.call_args[0][1]
    return json.loads(message_bytes.decode("utf-8"))


@pytest.mark.asyncio
async def test_gcp_buy_propagates_us_market_from_scenario():
    pub, mock_client = _make_gcp_publisher()
    await pub.publish_signal(
        signal_type="BUY",
        ticker="APH",
        company_name="Amphenol Corporation",
        price=151.91,
        scenario={"market": "US", "buy_score": 8},
    )
    payload = _gcp_published_payload(mock_client)
    assert payload["market"] == "US"


@pytest.mark.asyncio
async def test_gcp_buy_defaults_kr_when_market_absent():
    pub, mock_client = _make_gcp_publisher()
    await pub.publish_signal(
        signal_type="BUY",
        ticker="005930",
        company_name="Samsung Electronics",
        price=82000,
        scenario={"buy_score": 8},
    )
    payload = _gcp_published_payload(mock_client)
    assert payload["market"] == "KR"


@pytest.mark.asyncio
async def test_gcp_sell_market_via_extra_data_wins():
    pub, mock_client = _make_gcp_publisher()
    await pub.publish_signal(
        signal_type="SELL",
        ticker="MU",
        company_name="Micron Technology, Inc.",
        price=862.04,
        scenario={"buy_score": 0},          # no market in scenario → would default KR
        extra_data={"market": "US"},        # sell carries market here
    )
    payload = _gcp_published_payload(mock_client)
    assert payload["market"] == "US"


# --------------------------------------------------------------------------- #
# Redis Streams publisher
# --------------------------------------------------------------------------- #
def _make_redis_publisher():
    from messaging.redis_signal_publisher import SignalPublisher as RedisSignalPublisher

    pub = RedisSignalPublisher.__new__(RedisSignalPublisher)
    # Minimal attributes used by publish_signal.
    pub.STREAM_NAME = "prism-trading-signals"
    mock_redis = MagicMock()
    mock_redis.xadd = MagicMock(return_value="1-0")
    pub._redis = mock_redis
    pub._is_connected = lambda: True
    return pub, mock_redis


def _redis_published_payload(mock_redis) -> dict:
    # xadd(stream, "*", {"data": json_str})
    fields = mock_redis.xadd.call_args[0][2]
    return json.loads(fields["data"])


@pytest.mark.asyncio
async def test_redis_buy_propagates_us_market_from_scenario():
    pub, mock_redis = _make_redis_publisher()
    await pub.publish_signal(
        signal_type="BUY",
        ticker="APH",
        company_name="Amphenol Corporation",
        price=151.91,
        scenario={"market": "US", "buy_score": 8},
    )
    payload = _redis_published_payload(mock_redis)
    assert payload["market"] == "US"


@pytest.mark.asyncio
async def test_redis_buy_defaults_kr_when_market_absent():
    pub, mock_redis = _make_redis_publisher()
    await pub.publish_signal(
        signal_type="BUY",
        ticker="005930",
        company_name="Samsung Electronics",
        price=82000,
        scenario={"buy_score": 8},
    )
    payload = _redis_published_payload(mock_redis)
    assert payload["market"] == "KR"
