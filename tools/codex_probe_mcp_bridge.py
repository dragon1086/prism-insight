"""Read-capability MCP bridge for a separately isolated model namespace.

Host API accepts argv/env/cwd ONLY from the trusted wrapper's vetted, pinned
profile. Tool names alone do not make an arbitrary server trustworthy. Allowed
tool results are intentional data transfer; this cannot sanitize a malicious
server's successful result. Never expose this constructor/config to the model.

Every host MCP child runs in a mandatory Linux bwrap PID namespace, with a
read-only host filesystem, its own /proc and /tmp, and host network. This trusted
provider CAN read host secrets; it is NOT a secret-filesystem sandbox. The model
never gets these mounts or the server environment. Namespace teardown kills
descendants without signaling the shared wrapper process group. There is no
pidfd/kernel fallback and no new session. Host cache/config compatibility and
actual Linux teardown must be verified by the integrating wrapper.

The staged client CLI is stdlib-only: --client /mounted/read-bridge.sock.
It cannot configure or launch host processes. No SQLite/resources/file methods.
"""
from __future__ import annotations

from collections import deque
from datetime import date
import json
import logging
import math
import os
import hashlib
import re
from pathlib import Path
import selectors
import socket
import socketserver
import stat
import subprocess  # nosec B404 - vetted argv and mandatory namespace launcher below
import sys
import threading
import time

TOOLS = {
    "kospi_kosdaq": frozenset({"get_stock_ohlcv", "get_stock_market_cap", "get_stock_trading_volume", "get_index_ohlcv", "get_ticker_name"}),
    "yahoo_finance": frozenset({"get_historical_stock_prices", "get_stock_info", "get_yahoo_finance_news", "get_stock_actions", "get_financial_statement", "get_holder_info", "get_option_expiration_dates", "get_option_chain", "get_recommendations"}),
    "perplexity": frozenset({"perplexity_ask"}),
}
MAX_REQUEST_LINE = 64 * 1024
MAX_RESPONSE_LINE = 2 * 1024 * 1024
MAX_QUEUED_BYTES = 4 * 1024 * 1024
MAX_SESSION_BYTES = 64 * 1024 * 1024
MAX_MESSAGES = 10000
MAX_PENDING = 16
_PRIVATE_TMPFS = "/tmp"  # nosec B108 - provider namespace tmpfs, not shared host temporary storage
_NODE_MOUNT = _PRIVATE_TMPFS + "/prism-node-runtime"
CLIENT_DRAIN_SECONDS = 5
logger = logging.getLogger(__name__)

# Wrapper-verified read-only installed provider metadata; never downloads.
# Updating these pins is a reviewed deployment change, not model configuration.
HOST_PROVIDERS = {
    "kospi_kosdaq": {
        "executable": "/root/.pyenv/versions/3.11.11/bin/python3.11",
        "executable_sha256": "df2b385fd7a80c006dc1a71fae17fdc52ec7e0cac2fa1ee41d67d44146e72029",
        "code_root": "/root/prism-insight/cores/market_data", "entrypoint": "mcp_server.py",
        "code_tree_sha256": "a1b36cec63d3e44b637be4289be73afeb91bc4c3204f22d1858a989eac48ddf9",
    },
    "yahoo_finance": {
        "executable": "/root/.pyenv/versions/3.11.11/bin/python3.11",
        "executable_sha256": "df2b385fd7a80c006dc1a71fae17fdc52ec7e0cac2fa1ee41d67d44146e72029",
        "code_root": "/root/.cache/uv/archive-v0/Vi7ju4L6NJFBDA_ymQ68T/yahoo_finance_mcp", "entrypoint": "server.py",
        "code_tree_sha256": "261d6d7b1fbc3e7f2a39b0fdc750ab55686a13d348e9b26246201962a8381c83",
    },
    "perplexity": {
        "executable": "/root/.nvm/versions/node/v22.14.0/bin/node",
        "executable_sha256": "1abce2374a485bddae3c27b17a3e3143e2780232026e627c4fe74ddde3f380a1",
        "code_root": "/root/.npm/_npx/ba10a73795e7449a/node_modules/@perplexity-ai/mcp-server", "entrypoint": "dist/index.js",
        "code_tree_sha256": "08c623fe61de890aa4a27883bde7a77dee8982f32d130d425d1d88fae5bc7a21",
    },
}
KR_PUBLIC_DIAGNOSTIC = "KR_PUBLIC_DIAGNOSTIC"
KR_PUBLIC_LIMITATIONS = (
    "market_data_source_order_deviation_no_kis",
    "fdr_market_cap_approximate_current_shares_not_exact_history",
    "fdr_prices_adjusted_flag_not_honored",
    "naver_flows_recent_about_10_sessions_only",
    "unsupported_capabilities_remain_unknown",
    "not_production_vendor_parity",
)


class BridgeError(ValueError):
    pass


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted_source(path):
    path = Path(path)
    if not path.is_file() or path.resolve() != path or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise BridgeError("unsafe_provider_source")
    return path


def code_tree_sha256(root):
    root = Path(root)
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".js", ".mjs", ".cjs", ".json"} and "__pycache__" not in path.parts:
            records.append([str(path.relative_to(root)), file_sha256(_trusted_source(path))])
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()


def _node_bytes(path, destination=None):
    """Pinned Node is data here, never executable authority from its owner."""
    path = Path(path)
    if path.resolve() != path:
        raise BridgeError("unsafe_node_input")
    digest, total = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or not 4 <= info.st_size <= 256 * 1024 * 1024:
            raise BridgeError("unsafe_node_input")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if total == 0 and chunk[:4] != b"\x7fELF":
                raise BridgeError("invalid_node_elf")
            total += len(chunk)
            if total > 256 * 1024 * 1024:
                raise BridgeError("node_size_limit")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
    if digest.hexdigest() != HOST_PROVIDERS["perplexity"]["executable_sha256"]:
        raise BridgeError("node_pin_mismatch")


def _private_runtime_root(root):
    root = Path(root)
    if (not root.is_absolute() or ".." in root.parts or root.resolve() != root or not root.is_dir()
            or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077):
        raise BridgeError("unsafe_private_runtime")
    return root


def verified_node_runtime(root):
    path = _trusted_source(_private_runtime_root(root) / "node-runtime")
    if path.stat().st_nlink != 1 or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise BridgeError("unsafe_private_runtime")
    _node_bytes(path)
    return path


def snapshot_node_runtime(root):
    target = _private_runtime_root(root) / "node-runtime"
    # Exclusive destination prevents replacing an existing reviewed runtime.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            _node_bytes(HOST_PROVIDERS["perplexity"]["executable"], stream)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o700)
        return verified_node_runtime(root)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def verified_provider_pins(names=None, *, prepare_node_snapshot=False, host_runtime_root=None):
    names = tuple(HOST_PROVIDERS) if names is None else tuple(names)
    if not names or any(name not in HOST_PROVIDERS for name in names):
        raise BridgeError("server_denied")
    for name in names:
        spec = HOST_PROVIDERS[name]
        if name == "perplexity" and prepare_node_snapshot:
            _node_bytes(spec["executable"])
            executable = Path(spec["executable"])
        elif name == "perplexity" and host_runtime_root is not None:
            executable = verified_node_runtime(host_runtime_root)
        else:
            executable = _trusted_source(spec["executable"])
        _trusted_source(Path(spec["code_root"]) / spec["entrypoint"])
        if (not os.access(executable, os.X_OK) or file_sha256(executable) != spec["executable_sha256"]
                or code_tree_sha256(spec["code_root"]) != spec["code_tree_sha256"]):
            raise BridgeError("provider_pin_mismatch")
    return {name: dict(HOST_PROVIDERS[name]) for name in names}


def host_provider_configs(pins, names, environ, *, kr_profile=None, host_runtime_root=None):
    if pins != HOST_PROVIDERS:
        raise BridgeError("unapproved_provider_manifest")
    if kr_profile not in {None, KR_PUBLIC_DIAGNOSTIC}:
        raise BridgeError("unapproved_kr_profile")
    if "kospi_kosdaq" in names and kr_profile != KR_PUBLIC_DIAGNOSTIC:
        raise BridgeError("kr_auth_boundary_pending")
    verified_provider_pins(names, host_runtime_root=host_runtime_root)
    result = {}
    for name in names:
        spec = HOST_PROVIDERS[name]
        # No inherited proxy, Python injection, broker keys or arbitrary env.
        env = {"PATH": "/usr/bin:/bin", "HOME": _PRIVATE_TMPFS, "TMPDIR": _PRIVATE_TMPFS,
               "XDG_CACHE_HOME": _PRIVATE_TMPFS + "/cache", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        if name == "perplexity":
            key = environ.get("PERPLEXITY_API_KEY")
            if not isinstance(key, str) or not key.strip() or len(key) > 16384:
                raise BridgeError("missing_provider_credential")
            env["PERPLEXITY_API_KEY"] = key
        else:
            env["PYTHONPATH"] = "/root/prism-insight" if name == "kospi_kosdaq" else str(Path(spec["code_root"]).parent)
        if name == "kospi_kosdaq":
            # Both are required: MCP main copies report order over market order.
            # dotenv override=False cannot replace these preexisting values.
            env["PRISM_MARKET_DATA_SOURCES"] = "fdr,naver"
            env["PRISM_REPORT_DATA_SOURCES"] = "fdr,naver"
        argv = [spec["executable"], str(Path(spec["code_root"]) / spec["entrypoint"])]
        if name == "perplexity" and host_runtime_root is not None:
            argv[0] = str(verified_node_runtime(host_runtime_root))
        if name != "perplexity":
            argv.insert(1, "-u")
        result[name] = {"argv": argv, "env": env, "cwd": _PRIVATE_TMPFS, "request_timeout": 120}
        if name == "kospi_kosdaq":
            result[name]["source_profile"] = KR_PUBLIC_DIAGNOSTIC
    return result


def client_source():
    """Stage only the tiny relay, never host provider paths/config/launch code."""
    import inspect
    return ('"""Isolated stdio-to-UNIX relay; no host configuration API."""\n'
            'import os, selectors, socket, sys, time\n'
            f'MAX_QUEUED_BYTES = {MAX_QUEUED_BYTES}\nCLIENT_DRAIN_SECONDS = {CLIENT_DRAIN_SECONDS}\n\n'
            + inspect.getsource(client) + '\n' + inspect.getsource(main)
            + '\nif __name__ == "__main__":\n    raise SystemExit(main())\n')


def _host_command(argv):
    bwrap = Path("/usr/bin/bwrap")
    if sys.platform != "linux" or not bwrap.is_file() or not os.access(bwrap, os.X_OK):
        raise BridgeError("pid_namespace_unavailable")
    if bwrap.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BridgeError("unsafe_bwrap")
    runtime_bind = []
    if Path(argv[0]).name == "node-runtime":
        runtime = verified_node_runtime(Path(argv[0]).parent)
        runtime_bind = ["--ro-bind", str(runtime), _NODE_MOUNT]
        argv = [_NODE_MOUNT, *argv[1:]]
    return [str(bwrap), "--unshare-pid", "--die-with-parent", "--cap-drop", "ALL",
            "--ro-bind", "/", "/", "--proc", "/proc", "--tmpfs", _PRIVATE_TMPFS, *runtime_bind,
            "--chdir", _PRIVATE_TMPFS, "--", *argv]


def _search_domain(value):
    """Lexical public-hostname filter, not a fetch target or DNS lookup.

    Perplexity receives this only as search scope at its fixed API endpoint.
    No claim is made that arbitrary public DNS names resolve to public IPs.
    """
    if not isinstance(value, str) or not value.isascii():
        return False
    host = value[1:] if value.startswith("-") else value
    if not 1 <= len(host) <= 253:
        return False
    labels = host.lower().split(".")
    private_suffixes = {"localhost", "local", "localdomain", "internal", "intranet", "lan", "home", "corp", "private", "test", "invalid", "example", "alt", "onion", "arpa"}
    if len(labels) < 2 or labels[-1] in private_suffixes or "localhost" in labels:
        return False
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", labels[-1]):
        return False  # Also excludes numeric IPv4/abbreviated IP suffixes.
    return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)


def validate_arguments(server_name, name, arguments):
    """Fixed read schemas/bounds, never URLs, filesystem paths or proxy kwargs.

    Yahoo keys/enums match wrapper-verified yahoo-finance-mcp 0.1.2 source.
    Safety deviations: historical windows <=2 years and months_back 1..120
    (the upstream recommendations implementation does not bound months_back).
    """
    if name not in TOOLS.get(server_name, ()) or not isinstance(arguments, dict):
        raise BridgeError("invalid_arguments")
    if server_name == "perplexity":
        if set(arguments) - {"messages", "search_context_size", "search_recency_filter", "search_domain_filter"}:
            raise BridgeError("invalid_arguments")
        messages = arguments.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
            raise BridgeError("invalid_arguments")
        total = 0
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] not in {"system", "user", "assistant"}
                    or not isinstance(message["content"], str) or not 1 <= len(message["content"]) <= 8000):
                raise BridgeError("invalid_arguments")
            total += len(message["content"])
        if total > 16000 or arguments.get("search_context_size", "medium") not in {"low", "medium", "high"}:
            raise BridgeError("invalid_arguments")
        if "search_recency_filter" in arguments and arguments["search_recency_filter"] not in {"hour", "day", "week", "month", "year"}:
            raise BridgeError("invalid_arguments")
        if "search_domain_filter" in arguments:
            domains = arguments["search_domain_filter"]
            if not isinstance(domains, list) or len(domains) > 20 or not all(_search_domain(value) for value in domains):
                raise BridgeError("invalid_arguments")
        return
    if server_name == "kospi_kosdaq":
        allowed = {"ticker"} if name == "get_ticker_name" else {"ticker", "fromdate", "todate"}
        allowed |= {"get_stock_ohlcv": {"adjusted"}, "get_stock_trading_volume": {"detail"}, "get_index_ohlcv": {"freq"}}.get(name, set())
        if set(arguments) - allowed:
            raise BridgeError("invalid_arguments")
        ticker = arguments.get("ticker")
        if isinstance(ticker, bool) or not isinstance(ticker, (str, int)) or not re.fullmatch(r"[0-9]{1,6}", str(ticker)):
            raise BridgeError("invalid_arguments")
        if any(key in arguments and type(arguments[key]) is not bool for key in ("adjusted", "detail")) or arguments.get("freq", "d") not in {"d", "m", "y"}:
            raise BridgeError("invalid_arguments")
        date_keys = ("fromdate", "todate") if name != "get_ticker_name" else ()
    else:
        allowed = {"ticker"}
        allowed |= {
            "get_historical_stock_prices": {"period", "interval"},
            "get_financial_statement": {"financial_type"},
            "get_holder_info": {"holder_type"}, "get_option_chain": {"expiration_date", "option_type"},
            "get_recommendations": {"recommendation_type", "months_back"},
        }.get(name, set())
        ticker = arguments.get("ticker")
        if set(arguments) - allowed or not isinstance(ticker, str) or not re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=-]{0,19}", ticker):
            raise BridgeError("invalid_arguments")
        required = {"get_financial_statement": {"financial_type"}, "get_holder_info": {"holder_type"},
                    "get_option_chain": {"expiration_date", "option_type"}, "get_recommendations": {"recommendation_type"}}.get(name, set())
        if not required <= arguments.keys():
            raise BridgeError("invalid_arguments")
        enums = {
            "financial_type": {"income_stmt", "quarterly_income_stmt", "balance_sheet", "quarterly_balance_sheet", "cashflow", "quarterly_cashflow"},
            "holder_type": {"major_holders", "institutional_holders", "mutualfund_holders", "insider_transactions", "insider_purchases", "insider_roster_holders"},
            "option_type": {"calls", "puts"},
            "recommendation_type": {"recommendations", "upgrades_downgrades"},
        }
        for key, choices in enums.items():
            if key in arguments and (not isinstance(arguments[key], str) or arguments[key] not in choices):
                raise BridgeError("invalid_arguments")
        if "months_back" in arguments and (type(arguments["months_back"]) is not int or not 1 <= arguments["months_back"] <= 120):
            raise BridgeError("invalid_arguments")
        if arguments.get("period", "1mo") not in {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"} or arguments.get("interval", "1d") not in {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}:
            raise BridgeError("invalid_arguments")
        date_keys = ("expiration_date",) if name == "get_option_chain" else ()
    dates = []
    for key in date_keys:
        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise BridgeError("invalid_arguments")
        raw = str(value).replace("-", "").replace("/", "")
        if not re.fullmatch(r"[0-9]{8}", raw):
            raise BridgeError("invalid_arguments")
        try:
            dates.append(date(int(raw[:4]), int(raw[4:6]), int(raw[6:])))
        except ValueError:
            raise BridgeError("invalid_arguments") from None
    if len(dates) == 2 and not 0 <= (dates[1] - dates[0]).days <= 732:
        raise BridgeError("invalid_arguments")


def _json_line(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode() + b"\n"


def _error(identifier, message):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32000, "message": message}}


def _protocol_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _tool_error(result):
    if result.get("isError") is True:
        return True
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured.get("error"):
        return True
    # The production KR read server returns caught exceptions as {"error": ...}
    # inside an otherwise successful MCP result. Do not expose those details.
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    value = json.loads(item["text"])
                except (ValueError, RecursionError):
                    continue
                if isinstance(value, dict) and value.get("error"):
                    return True
    return False


class _Lines:
    def __init__(self, limit):
        self.limit = limit
        self.buffer = bytearray()

    def feed(self, data):
        scan = len(self.buffer)
        self.buffer.extend(data)
        consumed = 0
        while True:
            end = self.buffer.find(b"\n", scan)
            if end < 0:
                break
            if end - consumed > self.limit:
                raise BridgeError("line_limit")
            yield bytes(self.buffer[consumed:end])
            consumed = scan = end + 1
        if consumed:
            del self.buffer[:consumed]
        if len(self.buffer) > self.limit:
            raise BridgeError("line_limit")


class _Queue:
    def __init__(self):
        self.parts = deque()
        self.size = 0

    def put(self, value):
        data = _json_line(value)
        if self.size + len(data) > MAX_QUEUED_BYTES:
            raise BridgeError("queue_limit")
        self.parts.append(memoryview(data))
        self.size += len(data)

    def write(self, writer):
        try:
            count = writer(self.parts[0])
        except BlockingIOError:
            return
        if not count:
            raise BridgeError("peer_closed")
        self.size -= count
        if count == len(self.parts[0]):
            self.parts.popleft()
        else:
            self.parts[0] = self.parts[0][count:]


def _stop_child(process):
    """Kill/reap only the namespace supervisor; never killpg/shared sentinels."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


class _Session:
    def __init__(self, connection, bridge):
        self.connection, self.bridge = connection, bridge
        self.to_client, self.to_server = _Queue(), _Queue()
        self.pending = {}
        self.next_id = 1
        self.initialized = self.ready = False
        self.requests = self.rejected = self.dropped = self.stderr_bytes = self.total_bytes = 0

    def client_line(self, line):
        self.requests += 1
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            self.to_client.put(_error(None, "invalid_json"))
            self.rejected += 1
            return
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            self.to_client.put(_error(None, "invalid_request"))
            self.rejected += 1
            return
        method, identifier = request.get("method"), request.get("id")
        has_id = "id" in request
        valid_id = type(identifier) is int and abs(identifier) <= 2**53 or isinstance(identifier, str) and len(identifier) <= 128
        if has_id and not valid_id:
            self.to_client.put(_error(None, "invalid_id"))
            self.rejected += 1
            return

        def reject(reason):
            self.rejected += 1
            if has_id:
                self.to_client.put(_error(identifier, reason))

        if method == "notifications/initialized" and not has_id:
            if self.initialized and not self.ready:
                self.ready = True
                self.to_server.put({"jsonrpc": "2.0", "method": method})
            else:
                reject("invalid_initialization")
            return
        if method not in ("initialize", "ping", "tools/list", "tools/call"):
            reject("method_denied")
            return
        if not has_id:
            reject("request_id_required")
            return
        if len(self.pending) >= MAX_PENDING or any(type(p["id"]) is type(identifier) and p["id"] == identifier for p in self.pending.values()):
            reject("pending_limit_or_duplicate_id")
            return
        params = request.get("params", {})
        if not isinstance(params, dict):
            reject("invalid_params")
            return
        if method == "initialize":
            version = params.get("protocolVersion")
            if self.initialized or any(p["method"] == "initialize" for p in self.pending.values()) or not _protocol_version(version):
                reject("invalid_initialization")
                return
            params = {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "isolated-read-bridge", "version": "1"}}
        elif method == "ping":
            params = {}
        elif not self.ready:
            reject("not_initialized")
            return
        elif method == "tools/list":
            cursor = params.get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 1024):
                reject("invalid_cursor")
                return
            params = {} if cursor is None else {"cursor": cursor}
        else:
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or name not in TOOLS[self.bridge.server_name]:
                reject("tool_denied")
                return
            if not isinstance(arguments, dict):
                reject("invalid_arguments")
                return
            try:
                validate_arguments(self.bridge.server_name, name, arguments)
            except (BridgeError, TypeError):
                reject("invalid_arguments")
                return
            params = {"name": name, "arguments": arguments}
        internal = self.next_id
        self.next_id += 1
        self.pending[internal] = {"id": identifier, "method": method, "at": time.monotonic()}
        self.to_server.put({"jsonrpc": "2.0", "id": internal, "method": method, "params": params})

    def server_line(self, line):
        try:
            response = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            self.dropped += 1
            return
        if (not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or "method" in response
                or type(response.get("id")) is not int or response["id"] not in self.pending):
            self.dropped += 1
            return  # Never forward server requests, notifications or unmatched IDs.
        pending = self.pending.pop(response["id"])
        identifier = pending["id"]
        result = response.get("result")
        if "error" in response or not isinstance(result, dict):
            self.to_client.put(_error(identifier, "upstream_error"))
            return
        if pending["method"] == "initialize":
            version = result.get("protocolVersion")
            if not _protocol_version(version):
                self.to_client.put(_error(identifier, "upstream_error"))
                return
            self.initialized = True
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": self.bridge.server_name, "version": "read-bridge-v1"}}
        elif pending["method"] == "ping":
            result = {}
        elif pending["method"] == "tools/list":
            advertised = result.get("tools")
            if not isinstance(advertised, list):
                self.to_client.put(_error(identifier, "upstream_error"))
                return
            filtered = []
            for tool in advertised:
                if (isinstance(tool, dict) and isinstance(tool.get("name"), str)
                        and tool["name"] in TOOLS[self.bridge.server_name] and isinstance(tool.get("inputSchema"), dict)):
                    filtered.append({key: tool[key] for key in ("name", "description", "inputSchema", "annotations") if key in tool})
            cursor = result.get("nextCursor")
            result = {"tools": filtered}
            if isinstance(cursor, str) and len(cursor) <= 1024:
                result["nextCursor"] = cursor
        elif _tool_error(result):
            result = {"isError": True, "content": [{"type": "text", "text": "upstream_tool_error"}]}
        else:
            content = result.get("content", [])
            safe = {"content": [{"type": "text", "text": item["text"]} for item in content
                                 if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)]} if isinstance(content, list) else {"content": []}
            if isinstance(result.get("structuredContent"), dict):
                safe["structuredContent"] = result["structuredContent"]
            result = safe
        if pending["method"] == "tools/call" and self.bridge.source_profile == KR_PUBLIC_DIAGNOSTIC:
            result.setdefault("content", []).insert(0, {
                "type": "text", "text": "KR_PUBLIC_DIAGNOSTIC: fdr,naver public sources; not production vendor parity. "
                "Market cap uses current shares and is approximate, not exact historical facts. Naver flows cover about 10 recent sessions; unsupported data stays unknown."})
            result.setdefault("structuredContent", {})["__probe_source"] = {
                "profile": KR_PUBLIC_DIAGNOSTIC, "limitations": list(KR_PUBLIC_LIMITATIONS)}
        self.to_client.put({"jsonrpc": "2.0", "id": identifier, "result": result})

    def run(self):
        process = None
        category = "closed"
        started = last_io = time.monotonic()
        try:
            if self.bridge.parent_alive is not None and not self.bridge.parent_alive():
                self.bridge.stopping.set()
                raise BridgeError("parent_lease_lost")
            # Only trusted host registrations provide argv; model RPC cannot
            # select it. _host_command enforces the owned namespace/runtime.
            process = subprocess.Popen(  # nosec B603  # nosemgrep
                _host_command(self.bridge.argv), env=self.bridge.env, cwd=self.bridge.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=False, close_fds=True,
            )
            if self.bridge.parent_alive is not None and not self.bridge.parent_alive():
                self.bridge.stopping.set()
                raise BridgeError("parent_lease_lost")
            for pipe in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
            self.connection.setblocking(False)
            client_lines, server_lines = _Lines(MAX_REQUEST_LINE), _Lines(MAX_RESPONSE_LINE)
            upstream_closed = False
            with selectors.DefaultSelector() as selector:
                selector.register(self.connection, selectors.EVENT_READ, "client")
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while not self.bridge.stopping.is_set():
                    now = time.monotonic()
                    if now - started > self.bridge.session_timeout or now - last_io > self.bridge.idle_timeout:
                        raise BridgeError("session_timeout")
                    if any(now - p["at"] > self.bridge.request_timeout for p in self.pending.values()):
                        raise BridgeError("request_timeout")
                    if self.total_bytes > MAX_SESSION_BYTES or self.requests + self.dropped > MAX_MESSAGES:
                        raise BridgeError("session_budget")
                    if upstream_closed and not self.to_client.size:
                        break
                    selector.modify(self.connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if self.to_client.size else 0), "client")
                    try:
                        selector.get_key(process.stdin)
                        if not self.to_server.size:
                            selector.unregister(process.stdin)
                    except KeyError:
                        if self.to_server.size and not upstream_closed:
                            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                    for key, mask in selector.select(.05):
                        if key.data == "client":
                            if mask & selectors.EVENT_READ:
                                data = self.connection.recv(65536)
                                if not data:
                                    return
                                self.total_bytes += len(data)
                                last_io = time.monotonic()
                                for line in client_lines.feed(data):
                                    self.client_line(line)
                            if mask & selectors.EVENT_WRITE and self.to_client.size:
                                self.to_client.write(self.connection.send)
                        elif key.data == "stdin":
                            self.to_server.write(lambda value: os.write(process.stdin.fileno(), value))
                        else:
                            data = os.read(key.fileobj.fileno(), 65536)
                            if not data:
                                selector.unregister(key.fileobj)
                                if key.data == "stdout":
                                    upstream_closed = True
                                continue
                            self.total_bytes += len(data)
                            last_io = time.monotonic()
                            if key.data == "stderr":
                                self.stderr_bytes += len(data)  # Drain, never decode/log.
                            else:
                                for line in server_lines.feed(data):
                                    self.server_line(line)
                if self.bridge.stopping.is_set():
                    category = "cancelled"
        except BridgeError as error:
            code = str(error)
            category = code if code in {"line_limit", "queue_limit", "peer_closed", "session_timeout", "request_timeout", "session_budget", "pid_namespace_unavailable", "unsafe_bwrap"} else "boundary_error"
        except (OSError, ValueError, TypeError):
            category = "io_error"
        finally:
            if process is not None:
                try:
                    _stop_child(process)
                except BaseException:
                    # ThreadingServer swallows handler exceptions. Preserve
                    # this evidence until wrapper ExitStack can fail closed.
                    self.bridge.cleanup_failed.set()
                    raise
            logger.info("[READ_MCP_BRIDGE] category=%s requests=%d rejected=%d dropped=%d stderr_bytes=%d elapsed_ms=%d rc=%s",
                        category, self.requests, self.rejected, self.dropped, self.stderr_bytes,
                        int((time.monotonic() - started) * 1000), process.returncode if process is not None else None)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.bridge.slots.acquire(blocking=False):
            return
        try:
            _Session(self.request, self.server.bridge).run()
        finally:
            self.server.bridge.slots.release()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def handle_error(self, request, client_address):
        logger.warning("[READ_MCP_BRIDGE] category=handler_error")


class ReadMcpBridge:
    def __init__(self, socket_path, *, server_name, argv, env, cwd,
                 request_timeout=120, idle_timeout=120, session_timeout=600, max_connections=2, source_profile=None, parent_alive=None):
        if server_name not in TOOLS:
            raise BridgeError("server_denied")
        if parent_alive is not None and not callable(parent_alive):
            raise BridgeError("invalid_parent_guard")
        self.parent_alive = parent_alive
        if source_profile not in {None, KR_PUBLIC_DIAGNOSTIC} or (source_profile is not None and server_name != "kospi_kosdaq"):
            raise BridgeError("invalid_source_profile")
        if source_profile == KR_PUBLIC_DIAGNOSTIC and (not isinstance(env, dict) or any(env.get(key) != "fdr,naver" for key in ("PRISM_MARKET_DATA_SOURCES", "PRISM_REPORT_DATA_SOURCES"))):
            raise BridgeError("invalid_public_source_order")
        if (not isinstance(argv, (list, tuple)) or not argv or len(argv) > 128
                or any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not os.path.isabs(argv[0])):
            raise BridgeError("invalid_vetted_argv")
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in env.items()) or not os.path.isabs(cwd):
            raise BridgeError("invalid_vetted_environment")
        for timeout in (request_timeout, idle_timeout, session_timeout):
            if isinstance(timeout, bool) or not math.isfinite(timeout) or not .01 <= timeout <= 3600:
                raise BridgeError("invalid_deadline")
        if type(max_connections) is not int or not 1 <= max_connections <= 8:
            raise BridgeError("invalid_connection_limit")
        self.socket_path = Path(socket_path).absolute()
        self.server_name, self.argv, self.env, self.cwd = server_name, tuple(argv), dict(env), str(cwd)
        self.source_profile = source_profile
        self.request_timeout, self.idle_timeout, self.session_timeout = request_timeout, idle_timeout, session_timeout
        self.stopping = threading.Event()
        self.cleanup_failed = threading.Event()
        self.slots = threading.BoundedSemaphore(max_connections)
        self.server = self.thread = None

    def __enter__(self):
        _host_command(self.argv)  # Fail closed before accepting model connections.
        parent = self.socket_path.parent
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH) or self.socket_path.exists():
            raise BridgeError("unsafe_socket_path")
        self.server = _Server(str(self.socket_path), _Handler)
        self.server.bridge = self
        os.chmod(self.socket_path, 0o600)
        self.socket_inode = self.socket_path.stat().st_ino
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stopping.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()  # Joins handlers after bounded namespace reap.
            self.thread.join(timeout=1)
            self.server = None
            try:
                if self.socket_path.lstat().st_ino == self.socket_inode and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass
        if self.cleanup_failed.is_set():
            raise BridgeError("provider_cleanup_unconfirmed")

    def __exit__(self, *_):
        self.close()


def client(socket_path):
    """Staged model-side relay only; bounded raw buffers, no host launch API."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(socket_path)
        connection.setblocking(False)
        incoming = outgoing = b""
        upstream_eof = False
        drain_deadline = None
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin.buffer, selectors.EVENT_READ, "stdin")
            selector.register(connection, selectors.EVENT_READ, "socket")
            os.set_blocking(sys.stdout.fileno(), False)
            last_io = time.monotonic()
            while time.monotonic() - last_io < 600:
                if upstream_eof:
                    if not incoming:
                        return 0
                    if time.monotonic() >= drain_deadline:
                        return 2  # Consumer remained blocked; not clean delivery.
                else:
                    selector.modify(connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if outgoing else 0), "socket")
                try:
                    selector.get_key(sys.stdout.buffer)
                    if not incoming:
                        selector.unregister(sys.stdout.buffer)
                except KeyError:
                    if incoming:
                        selector.register(sys.stdout.buffer, selectors.EVENT_WRITE, "stdout")
                for key, mask in selector.select(.1):
                    if key.data == "stdin":
                        data = os.read(sys.stdin.fileno(), 65536)
                        if not data:
                            return 0  # Caller disconnect still cancels immediately.
                        if not upstream_eof:
                            outgoing += data
                    elif key.data == "stdout":
                        try:
                            incoming = incoming[os.write(sys.stdout.fileno(), incoming):]
                        except BlockingIOError:
                            continue
                    else:
                        if mask & selectors.EVENT_READ:
                            data = connection.recv(65536)
                            if not data:
                                upstream_eof = True
                                drain_deadline = time.monotonic() + CLIENT_DRAIN_SECONDS
                                selector.unregister(connection)
                                outgoing = b""
                                continue  # Flush the already received response first.
                            incoming += data
                        if mask & selectors.EVENT_WRITE and outgoing:
                            outgoing = outgoing[connection.send(outgoing):]
                    last_io = time.monotonic()
                    if len(incoming) + len(outgoing) > MAX_QUEUED_BYTES:
                        return 2
    return 2


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 2 or args[0] != "--client":
        return 2
    try:
        return client(args[1])
    except (OSError, ValueError):
        return 2  # No exception text or arbitrary IDs/data on stdout/stderr.


if __name__ == "__main__":
    raise SystemExit(main())
