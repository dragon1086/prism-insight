"""Immutable secret-safe transport models for the dormant FMP adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from pydantic import SecretStr

from prism_core.data.contracts import DataQualityStatus


_SECRET_PARAMETER_NAMES = {
    "accesstoken",
    "apikey",
    "authorization",
    "token",
}


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be canonical JSON data") from exc


def _normalize_parameter_name(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


@dataclass(frozen=True, init=False)
class FMPApiKey:
    """Runtime-only FMP credential whose string forms are permanently redacted."""

    _secret: SecretStr = field(repr=False)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("FMP API key must not be empty")
        object.__setattr__(self, "_secret", SecretStr(value))

    def get_secret_value(self) -> str:
        """Reveal the key only to the injected transport boundary."""

        return self._secret.get_secret_value()

    def __repr__(self) -> str:
        return "FMPApiKey('**********')"

    __str__ = __repr__


@dataclass(frozen=True)
class FMPRequest:
    """Canonical non-secret request identity passed to an injected transport."""

    operation: str
    path: str
    params: Mapping[str, object]
    _canonical_identity: str = field(init=False, repr=False)
    _request_hash: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.operation or not self.path:
            raise ValueError("operation and path must not be empty")
        if not self.path.startswith("/"):
            raise ValueError("path must be relative and start with '/'")
        if any(
            _normalize_parameter_name(str(name)) in _SECRET_PARAMETER_NAMES
            for name in self.params
        ):
            raise ValueError("request params must not contain secret parameters")
        encoded_params = _canonical_json(dict(self.params))
        object.__setattr__(self, "params", json.loads(encoded_params))
        identity = _canonical_json(
            {
                "operation": self.operation,
                "params": json.loads(encoded_params),
                "path": self.path,
            }
        ).decode("utf-8")
        object.__setattr__(self, "_canonical_identity", identity)
        object.__setattr__(self, "_request_hash", hashlib.sha256(identity.encode()).hexdigest())

    @property
    def canonical_identity(self) -> str:
        return self._canonical_identity

    @property
    def request_hash(self) -> str:
        return self._request_hash


@dataclass(frozen=True)
class FMPPagination:
    """Strict synthetic page contract used by the injected transport boundary."""

    page: int
    total_pages: int
    has_more: bool
    next_page: int | None

    @classmethod
    def from_payload(cls, value: object) -> FMPPagination:
        if not isinstance(value, Mapping):
            raise ValueError("pagination must be an object")
        required = {"page", "totalPages", "hasMore", "nextPage"}
        if set(value) != required:
            raise ValueError("pagination fields must match the strict contract")
        page = value["page"]
        total_pages = value["totalPages"]
        has_more = value["hasMore"]
        next_page = value["nextPage"]
        if type(page) is not int or page < 1:
            raise ValueError("pagination page must be a positive integer")
        if type(total_pages) is not int or total_pages < 1:
            raise ValueError("pagination totalPages must be a positive integer")
        if type(has_more) is not bool:
            raise ValueError("pagination hasMore must be a boolean")
        if next_page is not None and (type(next_page) is not int or next_page < 1):
            raise ValueError("pagination nextPage must be null or a positive integer")
        return cls(
            page=page,
            total_pages=total_pages,
            has_more=has_more,
            next_page=next_page,
        )


@dataclass(frozen=True, init=False)
class FMPResponseEnvelope:
    """Immutable raw FMP response metadata and canonical payload hash."""

    status_code: int
    source_record_id: str
    revision: int
    observed_at: datetime
    available_at: datetime
    quality: DataQualityStatus = DataQualityStatus.FRESH
    _payload_json: str = field(repr=False)
    _source_hash: str = field(repr=False)

    def __init__(
        self,
        *,
        status_code: int,
        source_record_id: str,
        revision: int,
        observed_at: datetime,
        available_at: datetime,
        payload: object,
        quality: DataQualityStatus = DataQualityStatus.FRESH,
    ) -> None:
        if not 100 <= status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if not source_record_id:
            raise ValueError("source_record_id must not be empty")
        if revision < 0:
            raise ValueError("revision must be non-negative")
        for field_name, value in (
            ("observed_at", observed_at),
            ("available_at", available_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if observed_at > available_at:
            raise ValueError("observed_at must be at or before available_at")
        encoded = _canonical_json(payload)
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "source_record_id", source_record_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "quality", quality)
        object.__setattr__(self, "_payload_json", encoded.decode("utf-8"))
        object.__setattr__(self, "_source_hash", hashlib.sha256(encoded).hexdigest())

    @property
    def payload(self) -> object:
        """Return a detached copy so callers cannot mutate retained raw evidence."""

        return json.loads(self._payload_json)

    @property
    def source_hash(self) -> str:
        return self._source_hash
