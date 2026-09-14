"""No credentials or broker calls: exercise the authenticated transport boundary."""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pandas.testing import assert_frame_equal

import archive_api
from cores import market_data
from cores.market_data import remote_api, remote_source
from cores.market_data.source import Unavailable, Unsupported


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(archive_api, "_API_KEY", "test-key")
    monkeypatch.setattr(remote_api, "_run_worker", remote_api.execute_market_data)
    return TestClient(archive_api.app)


def test_roundtrip():
    frame = pd.DataFrame({"종가": [100.25, float("nan")], "거래량": [1, 2]},
                         index=pd.date_range("2026-09-01", periods=2, tz="Asia/Seoul", name="날짜"))
    frame.columns.name = "fields"
    frame.attrs = {"source": "kis", "as_of": "2026-09-01T12:00:00+09:00",
                   "latest_only": True, "estimate_note": "추정치"}
    result = remote_source.decode_result(remote_source.encode_result(frame))
    assert_frame_equal(result, frame, check_freq=False)
    assert result.attrs == frame.attrs


@pytest.mark.parametrize("url", ["https://localhost:8765", "http://example.com",
    "http://localhost@evil.test", "http://localhost/x", "http://localhost?x=y"])
def test_reject_non_tunnel_urls(url):
    with pytest.raises(Unavailable):
        remote_source.RemoteKisSource(url, api_key="key")


def test_authentication(client):
    body = {"capability": "ticker_name", "ticker": "000660"}
    assert client.post("/market-data", json=body).status_code in {401, 403}
    assert client.post("/market-data", json=body,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_request_size_bound(client):
    response = client.post("/market-data", content=b" " * 4097,
                           headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 413


@pytest.mark.parametrize("changes", [
    {"capability": "_trading"}, {"ticker": "../../secret"}, {"start": "20200101"},
    {"start": "20269999"}, {"start": "20260915"}, {"adjusted": "yes"}, {"url": "http://evil"},
])
def test_invalid_requests(client, changes):
    body = {"capability": "price_history", "ticker": "000660", "start": "20260901", "end": "20260914", **changes}
    assert client.post("/market-data", json=body,
                       headers={"Authorization": "Bearer test-key"}).status_code == 422


def test_endpoint_dispatch_and_safe_errors(client, monkeypatch):
    class Fake:
        def ticker_name(self, ticker):
            assert ticker == "000660"
            return "SK하이닉스"

        def price_history(self, *args, **kwargs):
            raise RuntimeError("/private/key.yaml token=secret")

    monkeypatch.setattr(remote_api, "_source", Fake())
    headers = {"Authorization": "Bearer test-key"}
    result = client.post("/market-data", headers=headers,
                         json={"capability": "ticker_name", "ticker": "000660"})
    assert result.json() == {"kind": "name", "value": "SK하이닉스"}
    result = client.post("/market-data", headers=headers, json={"capability": "price_history",
                         "ticker": "000660", "start": "20260901", "end": "20260914"})
    assert result.status_code == 503
    assert result.json() == {"detail": "Market data unavailable"}
    with remote_api._lock:
        assert client.post("/market-data", headers=headers,
                           json={"capability": "ticker_name", "ticker": "000660"}).status_code == 503


def test_remote_only_and_fail_closed(monkeypatch):
    monkeypatch.setenv("PRISM_MARKET_DATA_REMOTE_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("ARCHIVE_API_KEY", "test-key")
    monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "krx,fdr,kis")
    monkeypatch.setattr(market_data, "_chain", None)
    monkeypatch.setattr(market_data, "KisSource", lambda: pytest.fail("Local KIS must not initialize"))
    assert market_data.default_chain().names == ["kis-remote"]

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("/private/path")

    monkeypatch.setattr(httpx.Client, "stream", timeout)
    with pytest.raises(Unavailable, match="Remote KIS market data unavailable") as exc:
        market_data.default_chain().fetch("ticker_name", "000660")
    assert "/private" not in str(exc.value)


def test_client_auth_and_bounds(monkeypatch):
    original = httpx.Client

    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        assert json.loads(request.content)["capability"] == "ticker_name"
        return httpx.Response(200, json={"kind": "name", "value": "SK하이닉스"})

    def factory(**kwargs):
        assert kwargs == {"timeout": 35.0, "follow_redirects": False, "trust_env": False}
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    source = remote_source.RemoteKisSource("http://127.0.0.1:8765", api_key="test-key")
    assert source.ticker_name("000660") == "SK하이닉스"


def test_unsupported_safe(client, monkeypatch):
    class Fake:
        def ticker_name(self, ticker):
            raise Unsupported("/private/path")
    monkeypatch.setattr(remote_api, "_source", Fake())
    result = client.post("/market-data", headers={"Authorization": "Bearer test-key"},
                         json={"capability": "ticker_name", "ticker": "000660"})
    assert result.status_code == 422
    assert "/private" not in result.text


def test_mcp_child_environment(monkeypatch):
    from mcp import StdioServerParameters

    from cores.llm.config_loader import load_mcp_registry
    monkeypatch.setenv("PRISM_MARKET_DATA_REMOTE_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("ARCHIVE_API_KEY", "synthetic-test-key")
    spec = load_mcp_registry(Path(__file__).parents[1] / "cores/llm/mcp_servers.yaml").get("kospi_kosdaq")
    params = StdioServerParameters(command=spec.command, args=list(spec.args), env=dict(spec.env))
    assert params.env["PRISM_MARKET_DATA_REMOTE_URL"] == "http://127.0.0.1:8765"
    assert params.env["ARCHIVE_API_KEY"] == "synthetic-test-key"


@pytest.mark.parametrize("method,args,kwargs", [
    ("price_history", ("000660", "20260901", "20260914"), {"adjusted": False}),
    ("index_history", ("1001", "20260901", "20260914"), {}),
    ("market_cap_history", ("000660", "20260901", "20260914"), {}),
    ("investor_flows", ("000660", "20260901", "20260914"), {}),
    ("fundamentals", ("000660", "20260901", "20260914"), {}),
    ("intraday_investor_estimate", ("000660",), {"as_of": pd.Timestamp("2026-09-14", tz="Asia/Seoul")}),
    ("ticker_name", ("000660",), {}),
])
def test_all_capabilities_roundtrip(client, monkeypatch, method, args, kwargs):
    frame = pd.DataFrame({"value": [1]}, index=pd.DatetimeIndex(["2026-09-14"]))
    frame.attrs = {"source": "kis", "latest_only": True}
    expected = "SK하이닉스" if method == "ticker_name" else frame

    class Fake:
        pass

    def answer(self, *received, **options):
        assert received == args
        assert options == kwargs
        return expected

    setattr(Fake, method, answer)
    monkeypatch.setattr(remote_api, "_source", Fake())
    original = httpx.Client

    def handle(request):
        result = client.post("/market-data", content=request.content, headers=dict(request.headers))
        return httpx.Response(result.status_code, json=result.json())

    monkeypatch.setattr(httpx, "Client", lambda **options: original(transport=httpx.MockTransport(handle), **options))
    source = remote_source.RemoteKisSource("http://127.0.0.1:8765", api_key="test-key")
    result = getattr(source, method)(*args, **kwargs)
    if isinstance(expected, str):
        assert result == expected
    else:
        assert_frame_equal(result, expected)
        assert result.attrs == expected.attrs


def test_worker_timeout_kills_reaps_and_releases_lock(monkeypatch):
    real_run, real_popen = subprocess.run, subprocess.Popen
    children = []

    def tracked_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def slow_worker(argv, **kwargs):
        assert argv == [sys.executable, "-m", "cores.market_data.remote_worker"]
        assert kwargs["timeout"] == 25
        kwargs["timeout"] = 0.1
        return real_run([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(subprocess, "run", slow_worker)
    request = remote_api.MarketDataRequest(capability="ticker_name", ticker="000660")
    with pytest.raises(HTTPException) as exc:
        remote_api.fetch_market_data(request)
    assert exc.value.status_code == 503
    assert children[0].poll() is not None
    assert not remote_api._lock.locked()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, '{"kind":"name","value":"SK"}'))
    assert remote_api.fetch_market_data(request)["value"] == "SK"


def test_real_worker_invalid_input_never_needs_credentials():
    result = subprocess.run([sys.executable, "-m", "cores.market_data.remote_worker"],
                            input='{"capability":"_trading"}', text=True,
                            capture_output=True, timeout=10, check=True,
                            cwd=Path(__file__).parents[1])
    assert json.loads(result.stdout) == {"error": "unavailable"}
