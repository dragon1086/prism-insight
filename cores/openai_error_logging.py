"""
Helpers for extracting and logging OpenAI API error metadata.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

_STATUS_PATTERN = re.compile(r"Error code:\s*(\d{3})")
_CODE_PATTERN = re.compile(r"""['"]code['"]\s*:\s*['"]([^'"]+)['"]""")
_TYPE_PATTERN = re.compile(r"""['"]type['"]\s*:\s*['"]([^'"]+)['"]""")
_REQUEST_ID_KEYS = ("x-request-id", "x-client-request-id")
_OPENAI_CLASS_MARKERS = (
    "OpenAIError",
    "APIError",
    "APIStatusError",
    "BadRequestError",
    "RateLimitError",
    "AuthenticationError",
    "PermissionDeniedError",
)


def _iter_exception_chain(error: BaseException) -> Iterable[BaseException]:
    queue = [error]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen:
            continue

        seen.add(current_id)
        yield current

        for linked in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(linked, BaseException):
                queue.append(linked)

        for arg in getattr(current, "args", ()):
            if isinstance(arg, BaseException):
                queue.append(arg)


def _get_header_value(response: Any, *header_names: str) -> str | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    for name in header_names:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value:
            return str(value)

    return None


def extract_openai_error_details(error: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {
        "is_openai_error": False,
        "status_code": None,
        "code": None,
        "type": None,
        "request_id": None,
        "message": None,
    }

    for exc in _iter_exception_chain(error):
        class_name = type(exc).__name__
        if class_name in _OPENAI_CLASS_MARKERS or "openai" in type(exc).__module__.lower():
            details["is_openai_error"] = True

        if details["status_code"] is None:
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                details["status_code"] = status_code

        if details["request_id"] is None:
            request_id = getattr(exc, "request_id", None)
            if request_id:
                details["request_id"] = str(request_id)

        response = getattr(exc, "response", None)
        if details["request_id"] is None and response is not None:
            details["request_id"] = _get_header_value(response, *_REQUEST_ID_KEYS)
            details["is_openai_error"] = True

        body = getattr(exc, "body", None)
        error_payload = body.get("error", body) if isinstance(body, dict) else None

        if details["code"] is None:
            code = getattr(exc, "code", None)
            if code:
                details["code"] = str(code)
            elif isinstance(error_payload, dict) and error_payload.get("code"):
                details["code"] = str(error_payload["code"])

        if details["type"] is None:
            error_type = getattr(exc, "type", None)
            if error_type:
                details["type"] = str(error_type)
            elif isinstance(error_payload, dict) and error_payload.get("type"):
                details["type"] = str(error_payload["type"])

        if details["message"] is None:
            if isinstance(error_payload, dict) and error_payload.get("message"):
                details["message"] = str(error_payload["message"])
            else:
                message = str(exc).strip()
                if message:
                    details["message"] = message

        raw_text = str(exc)

        if details["status_code"] is None:
            match = _STATUS_PATTERN.search(raw_text)
            if match:
                details["status_code"] = int(match.group(1))

        if details["code"] is None:
            match = _CODE_PATTERN.search(raw_text)
            if match:
                details["code"] = match.group(1)

        if details["type"] is None:
            match = _TYPE_PATTERN.search(raw_text)
            if match:
                details["type"] = match.group(1)

        if details["status_code"] is not None or details["code"] or details["request_id"]:
            details["is_openai_error"] = True

    return details


def log_openai_error(logger: logging.Logger, error: BaseException, context: str) -> bool:
    details = extract_openai_error_details(error)
    status_code = details["status_code"]
    code = details["code"]

    should_log = details["is_openai_error"] or status_code in {400, 429} or code == "insufficient_quota"
    if not should_log:
        return False

    parts = [f"context={context}"]
    if status_code is not None:
        parts.append(f"status={status_code}")
    if code:
        parts.append(f"code={code}")
    if details["type"]:
        parts.append(f"type={details['type']}")
    parts.append(f"request_id={details['request_id'] or 'unavailable'}")

    logger.error("OpenAI API error metadata: %s", ", ".join(parts))
    return True
