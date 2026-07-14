"""Regression tests for the asynchronous GCP Pub/Sub publisher."""

import asyncio
import json
import threading

from messaging.gcp_pubsub_signal_publisher import SignalPublisher


class _ImmediateResultFuture:
    def __init__(self):
        self.result_thread_id = None

    def result(self):
        self.result_thread_id = threading.get_ident()
        return "message-123"


class _FailingResultFuture:
    def __init__(self):
        self.result_thread_id = None

    def result(self):
        self.result_thread_id = threading.get_ident()
        raise RuntimeError("publish acknowledgement failed")


class _FakePublisher:
    def __init__(self, future):
        self.future = future
        self.topic_path = None
        self.message_bytes = None

    def publish(self, topic_path, message_bytes):
        self.topic_path = topic_path
        self.message_bytes = message_bytes
        return self.future


def _configured_publisher(future):
    fake_publisher = _FakePublisher(future)
    publisher = SignalPublisher(project_id="test-project", topic_id="signals")
    publisher._publisher = fake_publisher
    publisher._topic_path = "projects/test-project/topics/signals"
    return publisher, fake_publisher


def test_publish_signal_does_not_block_event_loop():
    future = _ImmediateResultFuture()
    publisher, fake_publisher = _configured_publisher(future)

    event_loop_thread_id = threading.get_ident()
    message_id = asyncio.run(
        publisher.publish_signal(
            signal_type="SELL",
            ticker="AAPL",
            company_name="Apple",
            price=210.0,
            extra_data={"market": "US", "sell_reason": "TEST"},
        )
    )

    assert message_id == "message-123"
    assert future.result_thread_id is not None
    assert future.result_thread_id != event_loop_thread_id
    assert fake_publisher.topic_path == "projects/test-project/topics/signals"

    payload = json.loads(fake_publisher.message_bytes.decode("utf-8"))
    assert payload["type"] == "SELL"
    assert payload["ticker"] == "AAPL"
    assert payload["market"] == "US"
    assert payload["sell_reason"] == "TEST"


def test_publish_signal_returns_none_when_acknowledgement_fails():
    future = _FailingResultFuture()
    publisher, _ = _configured_publisher(future)

    event_loop_thread_id = threading.get_ident()
    message_id = asyncio.run(
        publisher.publish_signal(
            signal_type="SELL",
            ticker="AAPL",
            company_name="Apple",
            price=210.0,
        )
    )

    assert message_id is None
    assert future.result_thread_id is not None
    assert future.result_thread_id != event_loop_thread_id
