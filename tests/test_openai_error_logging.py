import logging
import sys
from pathlib import Path

import httpx
from openai import BadRequestError, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cores.openai_error_logging import extract_openai_error_details, log_openai_error


def _build_response(status_code: int, request_id: str) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status_code, headers={"x-request-id": request_id}, request=request)


def test_extract_openai_error_details_from_rate_limit_error():
    error = RateLimitError(
        "quota hit",
        response=_build_response(429, "req_quota_123"),
        body={"error": {"code": "insufficient_quota", "type": "insufficient_quota"}},
    )

    details = extract_openai_error_details(error)

    assert details["is_openai_error"] is True
    assert details["status_code"] == 429
    assert details["code"] == "insufficient_quota"
    assert details["type"] == "insufficient_quota"
    assert details["request_id"] == "req_quota_123"


def test_log_openai_error_uses_wrapped_request_id(caplog):
    cause = BadRequestError(
        "invalid request",
        response=_build_response(400, "req_bad_456"),
        body={"error": {"code": "invalid_request_error", "type": "invalid_request_error"}},
    )
    wrapped = RuntimeError("outer wrapper")
    wrapped.__cause__ = cause

    logger = logging.getLogger("test_openai_error_logging")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        logged = log_openai_error(logger, wrapped, "test context")

    assert logged is True
    assert "request_id=req_bad_456" in caplog.text
    assert "status=400" in caplog.text
    assert "code=invalid_request_error" in caplog.text
