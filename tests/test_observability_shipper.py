from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from observability.events import emit_event
from observability.shipper import (
    build_otlp_payload,
    load_checkpoint,
    run_once,
)


class _CollectorHandler(BaseHTTPRequestHandler):
    payloads: ClassVar[list] = []
    status = 200

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.__class__.payloads.append((self.path, json.loads(self.rfile.read(length))))
        self.send_response(self.__class__.status)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


@pytest.fixture
def collector():
    _CollectorHandler.payloads = []
    _CollectorHandler.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/logs"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_otlp_payload_exposes_queryable_prism_attributes(tmp_path):
    event = emit_event(
        "trigger.performance_feedback",
        service="prism-us-trading",
        market="US",
        ticker="AAPL",
        attributes={
            "trigger_type": "Gap Up Momentum Top",
            "mode": "shadow",
            "applied_adjust": 0,
        },
        spool_path=tmp_path / "events.jsonl",
    )
    payload = build_otlp_payload([event])
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    attributes = {item["key"]: next(iter(item["value"].values())) for item in record["attributes"]}

    assert record["traceId"] == event["trace_id"]
    assert attributes["event.name"] == "trigger.performance_feedback"
    assert attributes["prism.market"] == "US"
    assert attributes["prism.trigger_type"] == "Gap Up Momentum Top"
    assert attributes["prism.applied_adjust"] == "0"


def test_success_advances_checkpoint_and_does_not_resend(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    emit_event("pipeline.done", service="prism-test", spool_path=spool)

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
    ) == 2
    saved = load_checkpoint(checkpoint)
    assert saved is not None
    assert saved.offset == spool.stat().st_size
    assert len(_CollectorHandler.payloads) == 1
    assert _CollectorHandler.payloads[0][0] == "/v1/logs"

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
    ) == 0
    assert len(_CollectorHandler.payloads) == 1


def test_failed_delivery_does_not_advance_checkpoint(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.status = 503

    with pytest.raises(urllib.error.HTTPError):
        run_once(
            spool_path=spool,
            checkpoint_path=checkpoint,
            endpoint=collector,
            batch_size=100,
            timeout=2,
        )
    assert load_checkpoint(checkpoint) is None


def test_corrupt_line_is_skipped_without_blocking_later_events(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    spool.write_text("not-json\n", encoding="utf-8")
    emit_event("pipeline.done", service="prism-test", spool_path=spool)

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
    ) == 1
    assert load_checkpoint(checkpoint).offset == spool.stat().st_size
