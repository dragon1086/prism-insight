"""Agent-side loopback relay to ONE fixed Responses socket, not a host proxy.

The parent must mount the reviewed Responses service at /responses.sock inside
the strong agent namespace. This component alone does not establish OS isolation.
No destination/route comes from the model; the host service validates HTTP bodies.
"""
import math
import os
from pathlib import Path
import select
import socket
import socketserver
import stat
import threading
import time

RESPONSES_SOCKET = "/responses.sock"
MAX_BUFFER = 256 * 1024


class RelayRejected(ValueError):
    pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        upstream = None
        try:
            if server.stopping.is_set():
                return
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.settimeout(2)
            upstream.connect(RESPONSES_SOCKET)
            self.request.setblocking(False)
            upstream.setblocking(False)
            pair = (self.request, upstream)
            with server.lock:
                if server.stopping.is_set():
                    return
                server.pairs.add(pair)
            pending = {self.request: b"", upstream: b""}
            peer = {self.request: upstream, upstream: self.request}
            server_eof = False
            last_io = time.monotonic()
            while not server.stopping.is_set():
                if time.monotonic() - last_io >= server.idle_seconds:
                    return
                if server_eof and not pending[self.request]:
                    return
                readable = [] if server_eof else [s for s in pair if len(pending[peer[s]]) < MAX_BUFFER]
                writable = [s for s in pair if pending[s] and (s is self.request or not server_eof)]
                ready_read, ready_write, _ = select.select(readable, writable, [], .1)
                for current in ready_read:
                    try:
                        data = current.recv(min(65536, MAX_BUFFER - len(pending[peer[current]])))
                    except BlockingIOError:
                        continue
                    if not data:
                        if current is self.request:
                            return  # Client disconnect cancels; never replay a request.
                        server_eof = True
                        break
                    pending[peer[current]] += data
                    last_io = time.monotonic()
                for current in ready_write:
                    if server_eof and current is upstream:
                        continue
                    try:
                        sent = current.send(pending[current])
                    except BlockingIOError:
                        continue
                    if sent <= 0:
                        return
                    pending[current] = pending[current][sent:]
                    last_io = time.monotonic()
        except (OSError, ValueError):
            pass  # No bytes, URLs, headers, exception messages or credentials logged.
        finally:
            if upstream is not None:
                with server.lock:
                    server.pairs.discard((self.request, upstream))
                upstream.close()
            server.slots.release()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def process_request(self, request, client_address):
        # Reserve before spawning a worker, not after accepting an unbounded
        # number of handler threads under a connection burst.
        if self.stopping.is_set() or not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            self.shutdown_request(request)
            raise

    def handle_error(self, *_):
        pass


class ResponsesRelay:
    def __init__(self, *, idle_seconds=330, max_connections=4):
        if (isinstance(idle_seconds, bool) or not isinstance(idle_seconds, (int, float))
                or not math.isfinite(idle_seconds) or not .01 <= idle_seconds <= 600
                or type(max_connections) is not int or not 1 <= max_connections <= 4):
            raise RelayRejected("invalid_relay_budget")
        self.idle_seconds, self.max_connections = idle_seconds, max_connections
        self.server = self.thread = None

    def __enter__(self):
        if self.server is not None:
            raise RelayRejected("relay_already_started")
        path = Path(RESPONSES_SOCKET)
        try:
            info = path.stat()
            if path.is_symlink() or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RelayRejected("invalid_fixed_responses_socket")
        except OSError:
            raise RelayRejected("fixed_responses_socket_missing") from None
        server = _Server(("127.0.0.1", 0), _Handler)
        server.idle_seconds = self.idle_seconds
        server.stopping = threading.Event()
        server.lock = threading.Lock()
        server.pairs = set()
        server.slots = threading.BoundedSemaphore(self.max_connections)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            server.server_close()
            self.server = None
            raise
        return self

    @property
    def base_url(self):
        if self.server is None:
            raise RelayRejected("relay_not_started")
        return "http://127.0.0.1:" + str(self.server.server_address[1]) + "/v1"

    def close(self):
        if self.server is None:
            return
        self.server.stopping.set()
        self.server.shutdown()
        with self.server.lock:
            pairs = tuple(self.server.pairs)
        for pair in pairs:
            for connection in pair:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.server.server_close()
        self.thread.join(timeout=1)
        self.server = None

    def __exit__(self, *_):
        self.close()
