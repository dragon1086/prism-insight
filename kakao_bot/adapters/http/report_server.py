"""Serve report PDFs behind opaque, expiring links.

Kakao cannot attach files — the REST API has no attachment field — so a report
reaches the user as a `webLink` button pointing here. That makes this endpoint
public, and public means the only thing standing between the internet and the
report directory is this module. Hence design §10:

- the URL carries a random token, never a filename
- the token maps to a path in the database, and only while it is unexpired
- the resolved path must land inside the artifact root and end in .pdf
- there is no directory listing and no path traversal
- access logs record a token prefix, never the whole token

Nothing here trusts the database row either: a path that once passed
validation is re-checked on every request, because the row outlives the code
that wrote it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from kakao_bot.ports.repositories import KakaoRepository

logger = logging.getLogger(__name__)

ROUTE = "/kakao/reports/{token}"
_PDF_SUFFIX = ".pdf"
_TOKEN_LOG_PREFIX = 6


class ReportLinkServer:
    def __init__(
        self,
        repository: KakaoRepository,
        *,
        artifact_root: str | Path,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._artifact_root = Path(artifact_root).resolve()
        self._clock = clock

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(ROUTE, self.handle)
        return app

    async def handle(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info.get("token", "")
        artifact = self._repository.resolve_report_link(token, now=self._clock())
        if artifact is None:
            # One answer for unknown, expired and malformed tokens, so probing
            # cannot tell them apart.
            logger.info("Report link rejected (token=%s…)", token[:_TOKEN_LOG_PREFIX])
            raise web.HTTPNotFound(text="링크가 만료되었거나 존재하지 않습니다.")

        path = self._safe_path(artifact)
        if path is None:
            logger.error(
                "Report link resolved outside the artifact root (token=%s…)",
                token[:_TOKEN_LOG_PREFIX],
            )
            raise web.HTTPNotFound(text="링크가 만료되었거나 존재하지 않습니다.")

        logger.info(
            "Report link served (token=%s…, bytes=%d)",
            token[:_TOKEN_LOG_PREFIX],
            path.stat().st_size,
        )
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'inline; filename="{path.name}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    def _safe_path(self, artifact: str) -> Path | None:
        """Re-validate a stored path before opening it."""

        try:
            candidate = Path(artifact).resolve()
        except OSError:
            return None

        if candidate.suffix.lower() != _PDF_SUFFIX:
            return None
        if not candidate.is_relative_to(self._artifact_root):
            return None
        if not candidate.is_file():
            return None
        return candidate
