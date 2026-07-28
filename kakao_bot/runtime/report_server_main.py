"""Runtime for the public report-link endpoint.

Runs on its own so the process that faces the internet holds nothing but a
read-only view of the report directory — it never touches the Gateway, the
outbox, or the Kakao token.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from kakao_bot.adapters.http.report_server import ReportLinkServer
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8402
DEFAULT_ARTIFACT_ROOT = "pdf_reports"


def build_app() -> web.Application:
    database_path = Path(
        os.getenv("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite")
    ).expanduser()
    artifact_root = Path(
        os.getenv("KAKAO_REPORT_ARTIFACT_ROOT", DEFAULT_ARTIFACT_ROOT)
    ).expanduser()

    repository = SQLiteKakaoRepository(database_path)
    server = ReportLinkServer(repository, artifact_root=artifact_root)

    app = server.build_app()

    async def _purge_on_start(_: web.Application) -> None:
        removed = repository.purge_expired_report_links(
            now=datetime.now(timezone.utc)
        )
        if removed:
            logger.info("Purged %d expired report links", removed)

    async def _close(_: web.Application) -> None:
        repository.close()

    app.on_startup.append(_purge_on_start)
    app.on_cleanup.append(_close)
    logger.info(
        "Report links serving from %s (artifacts=%s)",
        database_path,
        artifact_root,
    )
    return app


def main() -> None:
    logging.basicConfig(
        level=os.getenv("KAKAO_REPORT_SERVER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    web.run_app(
        build_app(),
        host=os.getenv("KAKAO_REPORT_SERVER_HOST", DEFAULT_HOST),
        port=int(os.getenv("KAKAO_REPORT_SERVER_PORT", DEFAULT_PORT)),
        print=None,
        # aiohttp's access log writes the full request line, which would put
        # the whole token on disk — the one thing design §10 forbids. The
        # handler logs a prefix instead, which is enough to correlate.
        access_log=None,
    )


if __name__ == "__main__":
    main()
