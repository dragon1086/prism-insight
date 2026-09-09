"""Only fake stdio MCP children; no model, production config or credentials."""
import json
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import tools.codex_probe_mcp_bridge as bridge_module
from tools.codex_probe_mcp_bridge import ReadMcpBridge

REAL_HOST_COMMAND = bridge_module._host_command


@pytest.mark.skipif(sys.platform != "linux" or not Path("/usr/bin/bwrap").is_file(), reason="real Linux bwrap runtime bind verification required")
def test_private_runtime_copy_remains_executable_over_hidden_tmp(tmp_path, monkeypatch):
    # Harmless ELF only, never Node/provider/model: execute a private `true` copy.
    source = Path("/usr/bin/true").resolve()
    specs = {name: dict(spec) for name, spec in bridge_module.HOST_PROVIDERS.items()}
    specs["perplexity"]["executable"] = str(source)
    specs["perplexity"]["executable_sha256"] = bridge_module.file_sha256(source)
    monkeypatch.setattr(bridge_module, "HOST_PROVIDERS", specs)
    host = tmp_path / "private-runtime"
    host.mkdir(mode=0o700)
    runtime = bridge_module.snapshot_node_runtime(host)
    command = REAL_HOST_COMMAND([str(runtime)])
    bind = command.index(str(runtime))
    assert command[bind - 1:bind + 2] == ["--ro-bind", str(runtime), "/tmp/prism-node-runtime"]
    assert bind > command.index("--tmpfs")
    assert command[-1] == "/tmp/prism-node-runtime"
    result = subprocess.run(command, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=5, close_fds=True)
    assert result.returncode == 0


@pytest.fixture(autouse=True)
def fake_namespace_command(monkeypatch):
    # Local fake children only. Production has no flag/fallback disabling bwrap.
    monkeypatch.setattr(bridge_module, "_host_command", lambda argv: list(argv))


def test_handler_cleanup_failure_is_sticky_and_raised_on_context_exit(tmp_path, monkeypatch, caplog):
    real_stop = bridge_module._stop_child
    failed = threading.Event()
    def fail_after_fixture_reap(process):
        real_stop(process)  # never leave a real fixture child behind
        failed.set()
        raise subprocess.TimeoutExpired("PRIVATE_CLEANUP_CANARY", 2)
    monkeypatch.setattr(bridge_module, "_stop_child", fail_after_fixture_reap)
    with pytest.raises(bridge_module.BridgeError, match="provider_cleanup_unconfirmed"):
        with start(tmp_path) as bridge:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(bridge.socket_path))
                with connection.makefile("rwb") as stream:
                    initialize(stream)
            assert failed.wait(3)
    assert not bridge.socket_path.exists()
    assert "PRIVATE_CLEANUP_CANARY" not in caplog.text


FAKE = r'''
import json,sys
seen=[]
for line in sys.stdin.buffer:
    request=json.loads(line)
    seen.append(request.get("method"))
    sys.stderr.write("PRIVATE_CANARY"*10000); sys.stderr.flush()
    print(json.dumps({"jsonrpc":"2.0","method":"notifications/message","params":{"message":"PRIVATE_CANARY"}}),flush=True)
    if "id" not in request: continue
    method=request["method"]
    if method=="initialize":
        result={"protocolVersion":"2024-11-05","serverInfo":{"name":"PRIVATE_CANARY","version":"PRIVATE_CANARY"},"instructions":"PRIVATE_CANARY","capabilities":{"resources":{},"tools":{}}}
    elif method=="tools/list":
        result={"tools":[{"name":name,"inputSchema":{"type":"object"}} for name in ["get_stock_info","write_query","read_file","get_historical_stock_prices"]]}
    elif method=="tools/call":
        if request["params"]["arguments"].get("ticker")=="ERROR":
            print(json.dumps({"jsonrpc":"2.0","id":request["id"],"error":{"code":-1,"message":"PRIVATE_CANARY"}}),flush=True);continue
        result={"content":[{"type":"text","text":json.dumps(seen)}]}
    else: result={}
    response=json.dumps({"jsonrpc":"2.0","id":request["id"],"result":result}).encode()+b"\n"
    sys.stdout.buffer.write(response[:5]);sys.stdout.buffer.flush()
    sys.stdout.buffer.write(response[5:]);sys.stdout.buffer.flush()
'''


def start(tmp_path, script=FAKE, **kwargs):
    return ReadMcpBridge(tmp_path.parent / "b.sock", server_name="yahoo_finance",
                         argv=[sys.executable, "-u", "-c", script], env={}, cwd=str(tmp_path), **kwargs)


def send(stream, method, identifier=1, params=None):
    request = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if identifier is not None:
        request["id"] = identifier
    stream.write(json.dumps(request).encode() + b"\n")
    stream.flush()


def receive(stream):
    return json.loads(stream.readline())


def initialize(stream):
    send(stream, "initialize", "client-private-id", {"protocolVersion": "2024-11-05"})
    response = receive(stream)
    assert response["id"] == "client-private-id"
    send(stream, "notifications/initialized", None)
    return response


def test_read_only_calls_filtering_and_no_secret_logs(tmp_path, caplog):
    caplog.set_level("INFO")
    with start(tmp_path) as bridge, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(bridge.socket_path))
        with client.makefile("rwb") as stream:
            init = initialize(stream)
            assert init["result"]["capabilities"] == {"tools": {}}
            assert "PRIVATE_CANARY" not in json.dumps(init)
            send(stream, "tools/list", 2)
            assert [t["name"] for t in receive(stream)["result"]["tools"]] == ["get_stock_info", "get_historical_stock_prices"]
            send(stream, "tools/call", 3, {"name": "write_query", "arguments": {"secret": "PRIVATE_CANARY"}})
            assert receive(stream)["error"]["message"] == "tool_denied"
            send(stream, "resources/read", 4, {"uri": "file:///PRIVATE_CANARY"})
            assert receive(stream)["error"]["message"] == "method_denied"
            send(stream, "tools/call", 5, {"name": "get_stock_info", "arguments": {"ticker": "AAPL"}})
            seen = json.loads(receive(stream)["result"]["content"][0]["text"])
            assert seen == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
            send(stream, "tools/call", 6, {"name": "get_stock_info", "arguments": {"ticker": "ERROR"}})
            error = receive(stream)
            assert error["error"]["message"] == "upstream_error"
            send(stream, "tools/call", 7, {"name": "get_stock_info", "arguments": {"ticker": "AAPL", "url": "http://private"}})
            assert receive(stream)["error"]["message"] == "invalid_arguments"
    assert "PRIVATE_CANARY" not in caplog.text and "client-private-id" not in caplog.text
    assert "requests=" in caplog.text


def test_sqlite_is_not_a_host_bridge_profile(tmp_path):
    with pytest.raises(ValueError):
        ReadMcpBridge(tmp_path / "bridge.sock", server_name="sqlite", argv=[sys.executable], env={}, cwd=str(tmp_path))


@pytest.fixture
def spawned(monkeypatch):
    processes = []
    real = subprocess.Popen

    def launch(*args, **kwargs):
        assert kwargs.get("start_new_session") is False
        assert kwargs.get("close_fds") is True
        process = real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(bridge_module.subprocess, "Popen", launch)
    return processes


def stopped(process):
    assert process.poll() is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


@pytest.mark.parametrize("outcome", ["disconnect", "request_timeout", "cancel"])
def test_disconnect_deadline_and_cancel_reap_direct_fixture_child(tmp_path, spawned, outcome, caplog):
    caplog.set_level("INFO")
    bridge = start(tmp_path, "import time; time.sleep(30)", request_timeout=.1 if outcome == "request_timeout" else 3)
    with bridge, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(bridge.socket_path))
        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        deadline = time.monotonic() + 2
        while not spawned:
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert os.getpgid(spawned[0].pid) == os.getpgrp()
        if outcome == "disconnect":
            client.close()
        elif outcome == "cancel":
            bridge.close()
        else:
            assert client.recv(1024) == b""
    stopped(spawned[0])
    if outcome == "request_timeout":
        assert "category=request_timeout" in caplog.text


def test_unrelated_same_group_sentinel_survives_close(tmp_path):
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        with start(tmp_path) as bridge, socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(bridge.socket_path))
            with client.makefile("rwb") as stream:
                initialize(stream)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=3)


def test_out_of_order_responses_are_matched_and_server_requests_dropped(tmp_path):
    script = r'''
import json,sys
pending=[]
for line in sys.stdin:
 r=json.loads(line)
 if "id" not in r:continue
 if r["method"]=="initialize":
  print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":"2024-11-05"}}),flush=True)
 else:
  pending.append(r)
  if len(pending)==2:
   print(json.dumps({"jsonrpc":"2.0","method":"sampling/createMessage","id":pending[0]["id"],"params":{"secret":"PRIVATE_CANARY"}}),flush=True)
   for item in reversed(pending):
    print(json.dumps({"jsonrpc":"2.0","id":item["id"],"result":{"content":[{"type":"text","text":"ok"}]}}),flush=True)
'''
    with start(tmp_path, script) as bridge, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(bridge.socket_path))
        with client.makefile("rwb") as stream:
            initialize(stream)
            send(stream, "tools/call", 25, {"name": "get_stock_info", "arguments": {"ticker": "AAPL"}})
            send(stream, "tools/call", "second-private-id", {"name": "get_stock_info", "arguments": {"ticker": "MSFT"}})
            assert [receive(stream)["id"], receive(stream)["id"]] == ["second-private-id", 25]


@pytest.mark.parametrize("side", ["client", "server"])
def test_line_limits_close_and_reap(tmp_path, monkeypatch, spawned, side, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(bridge_module, "MAX_REQUEST_LINE", 1024)
    monkeypatch.setattr(bridge_module, "MAX_RESPONSE_LINE", 1024)
    script = "import time;time.sleep(30)" if side == "client" else "import sys,time;sys.stdout.write('x'*4096);sys.stdout.flush();time.sleep(30)"
    with start(tmp_path, script) as bridge, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(bridge.socket_path))
        if side == "client":
            client.sendall(b"x" * 4096)
        assert client.recv(1024) == b""
    stopped(spawned[0])
    assert "category=line_limit" in caplog.text


@pytest.mark.parametrize("server,tool,args", [
    ("yahoo_finance", "get_stock_info", {"ticker": "http://127.0.0.1"}),
    ("yahoo_finance", "get_stock_info", {"ticker": "AAPL", "url": "http://169.254.169.254"}),
    ("yahoo_finance", "get_historical_stock_prices", {"ticker": "AAPL", "period": "max"}),
    ("yahoo_finance", "get_recommendations", {"ticker": "AAPL", "recommendation_type": "recommendations", "months_back": 100000}),
    ("yahoo_finance", "get_financial_statement", {"ticker": "AAPL", "financial_type": "__dict__"}),
    ("yahoo_finance", "get_holder_info", {"ticker": "AAPL", "holder_type": "session"}),
    ("yahoo_finance", "get_option_chain", {"ticker": "AAPL", "expiration_date": "2026-10-16", "option_type": "both"}),
    ("kospi_kosdaq", "get_stock_ohlcv", {"ticker": "005930", "fromdate": "20000101", "todate": "20260910"}),
    ("kospi_kosdaq", "get_stock_ohlcv", {"ticker": "005930", "fromdate": "20260910", "todate": "20260101"}),
    ("kospi_kosdaq", "get_ticker_name", {"ticker": "../../private"}),
    ("perplexity", "perplexity_ask", {"messages": [{"role": "user", "content": "x" * 16001}]}),
    ("perplexity", "perplexity_ask", {"messages": [{"role": "user", "content": "query"}], "api_base": "http://private"}),
])
def test_fixed_argument_schemas_reject_unbounded_or_network_path_overrides(server, tool, args):
    with pytest.raises(ValueError):
        bridge_module.validate_arguments(server, tool, args)


def test_valid_bounded_market_and_perplexity_arguments():
    bridge_module.validate_arguments("kospi_kosdaq", "get_stock_ohlcv", {"ticker": 5930, "fromdate": 20260101, "todate": 20260910, "adjusted": True})
    bridge_module.validate_arguments("yahoo_finance", "get_historical_stock_prices", {"ticker": "^GSPC", "period": "1y", "interval": "1d"})
    bridge_module.validate_arguments("perplexity", "perplexity_ask", {"messages": [{"role": "user", "content": "bounded financial context"}], "search_context_size": "low"})
    bridge_module.validate_arguments("yahoo_finance", "get_financial_statement", {"ticker": "AAPL", "financial_type": "quarterly_cashflow"})
    bridge_module.validate_arguments("yahoo_finance", "get_holder_info", {"ticker": "AAPL", "holder_type": "insider_roster_holders"})
    bridge_module.validate_arguments("yahoo_finance", "get_option_chain", {"ticker": "AAPL", "expiration_date": "2026-10-16", "option_type": "calls"})
    bridge_module.validate_arguments("yahoo_finance", "get_recommendations", {"ticker": "AAPL", "recommendation_type": "upgrades_downgrades", "months_back": 120})


@pytest.mark.parametrize("recency", ["hour", "day", "week", "month", "year"])
def test_perplexity_verified_optional_search_filters(recency, monkeypatch):
    def no_lookup(*args, **kwargs):
        raise AssertionError("search filters must not become host DNS/fetch requests")

    monkeypatch.setattr(socket, "getaddrinfo", no_lookup)
    bridge_module.validate_arguments("perplexity", "perplexity_ask", {
        "messages": [{"role": "user", "content": "latest filing context"}],
        "search_recency_filter": recency, "search_domain_filter": ["SEC.gov", "-example.com"],
    })


@pytest.mark.parametrize("domains", [
    ["https://sec.gov"], ["sec.gov:443"], ["sec.gov/path"], ["localhost"], ["-localhost"],
    ["foo.local"], ["localhost.example.com"], ["metadata.google.internal"], ["private.corp"], ["foo.private"], ["foo.example"],
    ["127.0.0.1"], ["8.8.8.8"], ["::1"], ["127.1"], ["bücher.de"], ["a..com"],
    ["example.com."], ["--example.com"], ["a" * 64 + ".com"], ["sec.gov"] * 21, "sec.gov",
])
def test_perplexity_domain_filters_reject_nonpublic_shapes_and_oversize(domains):
    with pytest.raises(ValueError):
        bridge_module.validate_arguments("perplexity", "perplexity_ask", {
            "messages": [{"role": "user", "content": "bounded context"}], "search_domain_filter": domains,
        })


def test_perplexity_unknown_recency_is_rejected():
    with pytest.raises(ValueError):
        bridge_module.validate_arguments("perplexity", "perplexity_ask", {
            "messages": [{"role": "user", "content": "bounded context"}], "search_recency_filter": "all",
        })


def test_domain_error_payload_is_sanitized_even_without_mcp_error_flag():
    assert bridge_module._tool_error({"content": [{"type": "text", "text": '{"error":"PRIVATE_CANARY"}'}]})
    assert bridge_module._tool_error({"structuredContent": {"error": "PRIVATE_CANARY"}})
    assert not bridge_module._tool_error({"content": [{"type": "text", "text": '{"price": 100}'}]})


def test_staged_client_cli_relays_protocol_only(tmp_path):
    with start(tmp_path) as bridge:
        relay = subprocess.Popen([sys.executable, str(Path(bridge_module.__file__)), "--client", str(bridge.socket_path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={})
        try:
            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
            relay.stdin.write(json.dumps(request).encode() + b"\n")
            relay.stdin.flush()
            import select
            assert select.select([relay.stdout], [], [], 3)[0]
            response = json.loads(relay.stdout.readline())
            assert response["id"] == 1 and response["result"]["capabilities"] == {"tools": {}}
            relay.stdin.close()
            assert relay.wait(timeout=3) == 0
            assert relay.stderr.read() == b""
        finally:
            if relay.poll() is None:
                relay.kill()
                relay.wait(timeout=3)
            relay.stdout.close()
            relay.stderr.close()


@pytest.mark.parametrize("finish", ["drain", "cancel", "deadline"])
def test_staged_client_drains_final_response_after_upstream_eof_with_stdout_backpressure(tmp_path, finish):
    import select
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"text": "한국어" * 60000}}, ensure_ascii=False).encode() + b"\n"
    address = tmp_path.parent / f"e{('drain', 'cancel', 'deadline').index(finish)}.sock"
    sent = threading.Event()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(address))
        listener.listen(1)
        listener.settimeout(3)

        def serve():
            with listener.accept()[0] as peer:
                peer.recv(1024)
                peer.sendall(payload)
            sent.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        command = [sys.executable, str(Path(bridge_module.__file__)), "--client", str(address)]
        if finish == "deadline":
            command = [sys.executable, "-c", f"import sys;sys.path.insert(0,{str(Path(bridge_module.__file__).parents[1])!r});from tools import codex_probe_mcp_bridge as bridge;bridge.CLIENT_DRAIN_SECONDS=.2;raise SystemExit(bridge.main(['--client',{str(address)!r}]))"]
        relay = subprocess.Popen(command,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={})
        try:
            relay.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
            relay.stdin.flush()
            # Keep stdin open but do not consume stdout until the socket sender
            # has finished. Most response bytes must remain in relay memory.
            assert sent.wait(3)
            time.sleep(.1)
            if finish == "cancel":
                relay.stdin.close()
                assert relay.wait(timeout=3) == 0
                return
            if finish == "deadline":
                assert relay.wait(timeout=3) == 2
                return
            received = bytearray()
            deadline = time.monotonic() + 3
            while len(received) < len(payload) and time.monotonic() < deadline:
                if select.select([relay.stdout], [], [], .1)[0]:
                    data = os.read(relay.stdout.fileno(), 65536)
                    if not data:
                        break
                    received.extend(data)
            assert len(received) == len(payload)
            assert hashlib.sha256(received).digest() == hashlib.sha256(payload).digest()
            assert relay.wait(timeout=3) == 0
            assert relay.stderr.read() == b""
        finally:
            relay.stdin.close()
            if relay.poll() is None:
                relay.kill()
                relay.wait(timeout=3)
            relay.stdout.close()
            relay.stderr.close()
            thread.join(timeout=3)
            address.unlink(missing_ok=True)


def test_upstream_environment_is_explicit_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_INHERITED", "PRIVATE_CANARY")
    script = FAKE.replace("json.dumps(seen)", "json.dumps('PRIVATE_INHERITED' in __import__('os').environ)")
    with start(tmp_path, script) as bridge, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(bridge.socket_path))
        with client.makefile("rwb") as stream:
            initialize(stream)
            send(stream, "tools/call", 2, {"name": "get_stock_info", "arguments": {"ticker": "AAPL"}})
            assert receive(stream)["result"]["content"][0]["text"] == "false"


def test_incremental_lines_handle_partial_multibyte_without_rescanning():
    parser = bridge_module._Lines(1024)
    data = '{"text":"한국어"}\n{"number":2}\n'.encode()
    lines = []
    for byte in data:
        lines.extend(parser.feed(bytes([byte])))
    assert [json.loads(line) for line in lines] == [{"text": "한국어"}, {"number": 2}]
    assert parser.buffer == b""


def test_connection_budget_does_not_launch_another_host_child(tmp_path, spawned):
    with start(tmp_path, "import time;time.sleep(30)", max_connections=1) as bridge:
        with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
            first.settimeout(3)
            second.settimeout(3)
            first.connect(str(bridge.socket_path))
            deadline = time.monotonic() + 2
            while not spawned:
                assert time.monotonic() < deadline
                time.sleep(.01)
            second.connect(str(bridge.socket_path))
            assert second.recv(1024) == b""
            assert len(spawned) == 1
    stopped(spawned[0])


def test_mandatory_namespace_command_has_no_shared_group_kill_or_environment_inheritance(monkeypatch):
    monkeypatch.setattr(bridge_module.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_file", lambda p: True)
    monkeypatch.setattr(Path, "stat", lambda p: type("Stat", (), {"st_mode": 0o755})())
    monkeypatch.setattr(os, "access", lambda *a: True)
    command = REAL_HOST_COMMAND(["/fixed/trusted/provider", "--fixed"])
    assert command[:4] == ["/usr/bin/bwrap", "--unshare-pid", "--die-with-parent", "--cap-drop"]
    assert command[-3:] == ["--", "/fixed/trusted/provider", "--fixed"]
    assert ["--chdir", "/tmp"] == command[command.index("--chdir"):command.index("--chdir") + 2]
    monkeypatch.setattr(bridge_module.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="pid_namespace_unavailable"):
        REAL_HOST_COMMAND(["/fixed/trusted/provider"])


@pytest.mark.skipif(sys.platform != "linux" or not Path("/usr/bin/bwrap").is_file(), reason="real Linux bwrap namespace verification required")
@pytest.mark.parametrize("outcome", ["disconnect", "request_timeout", "cancel"])
def test_real_linux_namespace_tears_down_descendants_without_touching_sentinel(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(bridge_module, "_host_command", REAL_HOST_COMMAND)
    script = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']);\n" + FAKE
    script = script.replace('elif method=="tools/call":\n', 'elif method=="tools/call":\n        if request["params"]["arguments"].get("ticker")=="HANG": __import__("time").sleep(30)\n')
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    tracked = []
    real_popen = subprocess.Popen

    def record_popen(*a, **kw):
        process = real_popen(*a, **kw)
        tracked.append(process)
        return process

    monkeypatch.setattr(bridge_module.subprocess, "Popen", record_popen)
    try:
        with start(tmp_path, script) as bridge, socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(bridge.socket_path))
            with client.makefile("rwb") as stream:
                initialize(stream)
                parentage = {}
                for path in Path("/proc").glob("[0-9]*/stat"):
                    try:
                        fields = path.read_text().rpartition(") ")[2].split()
                        parentage[int(path.parent.name)] = int(fields[1])
                    except (OSError, ValueError, IndexError):
                        pass
                descendants = {tracked[0].pid}
                for _ in range(8):
                    descendants.update(pid for pid, parent in parentage.items() if parent in descendants)
                assert len(descendants) >= 3
                if outcome == "request_timeout":
                    bridge.request_timeout = .1
                    send(stream, "tools/call", 2, {"name": "get_stock_info", "arguments": {"ticker": "HANG"}})
                    assert stream.readline() == b""
                elif outcome == "cancel":
                    bridge.close()
        stopped(tracked[0])
        deadline = time.monotonic() + 3
        for pid in descendants:
            path = Path(f"/proc/{pid}/stat")
            while path.exists():
                try:
                    if path.read_text().rpartition(") ")[2].split()[0] == "Z":
                        break
                except FileNotFoundError:
                    break
                assert time.monotonic() < deadline
                time.sleep(.02)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=3)
