"""Reference client DX and retry behavior."""

from __future__ import annotations

import pytest

from stance.client import StanceClient


class Response:
    def __init__(self, body: dict, error: Exception | None = None):
        self.body = body
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.body


class Session:
    def __init__(self):
        self.calls = []
        self.last_seq = 4
        self.fail_post_once = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return Response({"last_seq": self.last_seq})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.fail_post_once:
            self.fail_post_once = False
            return Response({}, TimeoutError("timeout"))
        seq = kwargs["json"]["seq"]
        return Response({"seq": seq, "next_seq": seq + 1, "admit": "accepted"})


def test_constructor_has_no_hidden_network_io_and_defaults_to_three_seconds():
    session = Session()
    client = StanceClient("https://stance.example", "stk_secret", session=session)
    assert session.calls == []
    assert client.timeout == 3.0


def test_first_send_recovers_seq_and_sends_only_protocol_fields():
    session = Session()
    client = StanceClient("https://stance.example", "stk_secret", session=session)
    result = client.set("005930", 0.2)

    assert result["seq"] == 5
    assert [call[0] for call in session.calls] == ["GET", "POST"]
    payload = session.calls[-1][2]["json"]
    assert payload == {
        "protocol": "stance/1", "seq": 5, "kind": "set",
        "symbol": "005930", "target_weight": "0.2",
    }


def test_seq_recovery_failure_is_explicit_not_silently_zero():
    class Broken(Session):
        def get(self, url, **kwargs):
            return Response({}, ConnectionError("offline"))

    client = StanceClient("https://stance.example", "stk_secret", session=Broken())
    with pytest.raises(RuntimeError, match="일련번호"):
        client.hold()


def test_retry_after_timeout_reuses_same_sequence():
    session = Session()
    session.last_seq = 0
    session.fail_post_once = True
    client = StanceClient("https://stance.example", "stk_secret", session=session)

    with pytest.raises(TimeoutError):
        client.hold("wait")
    client.hold("wait")

    sent = [call[2]["json"]["seq"] for call in session.calls if call[0] == "POST"]
    assert sent == [1, 1]


def test_known_seq_skips_recovery_call():
    session = Session()
    client = StanceClient("https://stance.example", "stk_secret", seq=9, session=session)
    client.hold()
    assert [call[0] for call in session.calls] == ["POST"]
