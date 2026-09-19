"""Opt-in private credential supplier; no login, registration or runtime hooks.

Ambiguous refreshes fail closed until the operator replaces the actual grant.
The legacy refresh helper must not run concurrently: it does not share our lock.
Cancellation drains the owned transaction; process death or a lost response can
still lose a server-rotated token and require operator reauthorization.
"""

import asyncio
import fcntl
import hashlib
import json
import math
import os
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone

from prism_core.tradingview_transport import TVAccessGrant, _object_json, _private_logs

_IDENTITY = {"server_name": "tradingview", "server_url": "https://mcp.tradingview.com/mcp",
             "issuer": "https://www.tradingview.com"}
_ENDPOINT = "https://www.tradingview.com/mcp/oauth/token"
_STORE_LIMIT = 256 * 1024
_RESPONSE_LIMIT = 64 * 1024


class TradingViewCredentialError(Exception):
    """Only static AUTH_* codes cross the private transaction boundary."""


def _fail(code):
    raise TradingViewCredentialError(code) from None


def _token(value):
    return (type(value) is str and 0 < len(value) <= 8192
            and all(33 <= ord(char) <= 126 for char in value))


def _generation(entry):
    projection = {key: entry.get(key) for key in
                  (*_IDENTITY, "client_id", "access_token", "refresh_token")}
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _check(fd, directory=False):
    info = os.fstat(fd)
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not correct_type or info.st_uid != os.getuid() or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)):
        _fail("AUTH_STORE_UNSAFE")
    return info


def _read(directory, name, optional=False):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        if optional:
            return None
        raise
    try:
        _check(fd)
        chunks = bytearray()
        while len(chunks) <= _STORE_LIMIT:
            chunk = os.read(fd, min(65536, _STORE_LIMIT + 1 - len(chunks)))
            if not chunk:
                return bytes(chunks)
            chunks.extend(chunk)
        _fail("AUTH_STORE_INVALID")
    finally:
        os.close(fd)


def _json(raw):
    try:
        return _object_json(raw)
    except Exception:  # noqa: BLE001 - untrusted JSON may carry secret text
        _fail("AUTH_STORE_INVALID")


def _atomic_write(directory, name, value, expected=None):
    """Private same-directory replacement, including directory durability."""
    temporary = ".tv-" + secrets.token_hex(16)
    fd = None
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode()
        if len(raw) > _STORE_LIMIT:
            _fail("AUTH_STORE_INVALID")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                _fail("AUTH_STORE_WRITE_FAILED")
            view = view[written:]
        os.fsync(fd)
        if expected is not None and _read(directory, name) != expected:
            _fail("AUTH_STORE_CHANGED")
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _open_directory(path):
    directory = os.open(os.path.dirname(path) or ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _check(directory, directory=True)
        return directory
    except BaseException:
        os.close(directory)
        raise


def _open_lock(directory, name):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    except FileExistsError:
        fd = os.open(name, flags, dir_fd=directory)
    try:
        _check(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _try_lock(fd, directory, name):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    actual = _check(fd)
    if (current.st_ino, current.st_dev) != (actual.st_ino, actual.st_dev):
        _fail("AUTH_STORE_UNSAFE")
    return True


def _select(document):
    selected = [(key, value) for key, value in document.items()
                if type(value) is dict and all(value.get(k) == v for k, v in _IDENTITY.items())]
    if len(selected) != 1:
        _fail("AUTH_ENTRY_INVALID")
    key, entry = selected[0]
    expiry = entry.get("expires_at")
    if type(expiry) is not int or expiry <= 0 or not _token(entry.get("access_token")):
        _fail("AUTH_ENTRY_INVALID")
    try:
        expires_at = datetime.fromtimestamp(expiry / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        _fail("AUTH_ENTRY_INVALID")
    return key, entry, expires_at


async def _refresh(entry, factory, timeout):
    if factory is None:
        import httpx
        factory = httpx.AsyncClient

    async def request():
        async with factory(trust_env=False, follow_redirects=False, timeout=20) as client:  # noqa: SIM117
            async with client.stream("POST", _ENDPOINT, headers={"Accept-Encoding": "identity"}, data={
                    "grant_type": "refresh_token", "refresh_token": entry["refresh_token"],
                    "client_id": entry["client_id"]}) as response:
                if response.status_code != 200:
                    _fail("AUTH_REFRESH_FAILED")
                # HTTPX decodes before yielding bytes. Refuse compression before
                # consuming any body, so expansion cannot bypass the byte budget.
                if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                    _fail("AUTH_REFRESH_RESPONSE")
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    raw.extend(chunk)
                    if len(raw) > _RESPONSE_LIMIT:
                        _fail("AUTH_REFRESH_RESPONSE")
                try:
                    result = _object_json(bytes(raw))
                except Exception:  # noqa: BLE001 - suppress raw provider response
                    _fail("AUTH_REFRESH_RESPONSE")
                expiry = result.get("expires_in")
                if (result.get("token_type") != "Bearer" or not _token(result.get("access_token"))
                        or type(expiry) is not int or not 30 < expiry <= 86400
                        or ("refresh_token" in result and not _token(result["refresh_token"]))
                        or ("client_id" in result and not _token(result["client_id"]))):
                    _fail("AUTH_REFRESH_RESPONSE")
                return result

    try:
        return await asyncio.wait_for(request(), timeout=timeout)
    except TradingViewCredentialError:
        raise
    except Exception:  # noqa: BLE001 - HTTP errors may contain credentials
        raise TradingViewCredentialError("AUTH_REFRESH_FAILED") from None


def make_credential_supplier(path, *, http_client_factory=None, clock=None,
                             lock_timeout_seconds=5, refresh_timeout_seconds=20):
    """Construction performs no path access, imports, authentication or HTTP.

    Invocation is explicit opt-in. Limits may be shortened, not extended. The
    injected HTTP factory is for isolated testing, never endpoint discovery.
    """
    def now():
        value = clock() if clock else datetime.now(timezone.utc)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            _fail("AUTH_CLOCK_INVALID")
        return value.astimezone(timezone.utc)

    async def transaction(cancelled):
        directory = lock = None
        try:
            for limit, maximum in ((lock_timeout_seconds, 5), (refresh_timeout_seconds, 20)):
                if (type(limit) not in (int, float) or not math.isfinite(limit)
                        or not 0 < limit <= maximum):
                    _fail("AUTH_CONFIG_INVALID")
            filename = os.fspath(path)
            name = os.path.basename(filename)
            if type(filename) is not str or name in ("", ".", ".."):
                _fail("AUTH_CONFIG_INVALID")
            directory = await asyncio.to_thread(_open_directory, filename)
            lock_name = name + ".lock"
            state_name = name + ".refresh-state.json"
            lock = await asyncio.to_thread(_open_lock, directory, lock_name)
            deadline = time.monotonic() + lock_timeout_seconds
            while not await asyncio.to_thread(_try_lock, lock, directory, lock_name):
                if cancelled.is_set():
                    raise asyncio.CancelledError
                if time.monotonic() >= deadline:
                    _fail("AUTH_LOCK_TIMEOUT")
                await asyncio.sleep(min(0.025, max(0, deadline - time.monotonic())))
            if cancelled.is_set():
                raise asyncio.CancelledError
            original = await asyncio.to_thread(_read, directory, name)
            document = _json(original)
            key, entry, expires_at = _select(document)
            generation = _generation(entry)
            marker_raw = await asyncio.to_thread(_read, directory, state_name, True)
            if marker_raw is not None:
                marker = _json(marker_raw)
                if (set(marker) != {"version", "generation", "state"}
                        or type(marker["version"]) is not int or marker["version"] != 1
                        or marker["state"] != "IN_FLIGHT"
                        or type(marker["generation"]) is not str
                        or len(marker["generation"]) != 64
                        or any(c not in "0123456789abcdef" for c in marker["generation"])):
                    _fail("AUTH_STORE_INVALID")
                if marker["generation"] == generation:
                    _fail("AUTH_REFRESH_IN_FLIGHT")
            decision_time = now()
            if expires_at > decision_time + timedelta(seconds=600):
                return TVAccessGrant(entry["access_token"], expires_at)
            if not _token(entry.get("refresh_token")) or not _token(entry.get("client_id")):
                _fail("AUTH_ENTRY_INVALID")
            if cancelled.is_set():
                raise asyncio.CancelledError
            await asyncio.to_thread(_atomic_write, directory, state_name,
                                    {"version": 1, "generation": generation, "state": "IN_FLIGHT"})
            result = await _refresh(entry, http_client_factory, refresh_timeout_seconds)
            updated = dict(entry)
            updated["access_token"] = result["access_token"]
            if "refresh_token" in result:
                updated["refresh_token"] = result["refresh_token"]
            expires_at = decision_time + timedelta(seconds=result["expires_in"])
            updated["expires_at"] = int(expires_at.timestamp() * 1000)
            if _generation(updated) == generation:
                _fail("AUTH_REFRESH_RESPONSE")
            document[key] = updated
            await asyncio.to_thread(_atomic_write, directory, name, document, original)
            return TVAccessGrant(updated["access_token"], expires_at)
        except TradingViewCredentialError:
            raise
        except Exception:  # noqa: BLE001 - private filesystem/clock errors
            raise TradingViewCredentialError("AUTH_STORE_UNAVAILABLE") from None
        finally:
            if lock is not None:
                await asyncio.to_thread(os.close, lock)
            if directory is not None:
                await asyncio.to_thread(os.close, directory)

    async def supplier():
        with _private_logs():
            cancelled = asyncio.Event()
            task = asyncio.create_task(transaction(cancelled))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled.set()
                # A second caller cancellation must not abandon a writer/lock.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001 - retrieve owned task failure
                        break
                if not task.cancelled():
                    task.exception()  # Retrieve a drained failure without exposing it.
                raise

    return supplier
