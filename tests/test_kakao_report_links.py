"""Expiring, opaque links to report PDFs.

This endpoint is public — Kakao cannot attach files, so the report reaches the
user as a URL anyone can request. The tests below are mostly about what must
*not* happen: no filename in the URL, no serving after expiry, no escaping the
artifact directory, no whole token in the logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web

from kakao_bot.adapters.http.report_server import ReportLinkServer
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import ApprovalStatus

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
ROOM = "room-1"
TOKEN = "opaque-token-value"


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "pdf_reports"
    root.mkdir()
    pdf = root / "005930_삼성전자_20260728_analysis.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return root, pdf


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        repo.discover_room(ROOM, discovered_at=NOW)
        repo.set_room_approval(ROOM, ApprovalStatus.APPROVED)
        yield repo


@pytest.fixture
def client(event_loop, aiohttp_client, repository, artifacts):
    root, _ = artifacts
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    return event_loop.run_until_complete(aiohttp_client(server.build_app()))


def link(repository, path, *, token=TOKEN, expires_at=LATER):
    repository.create_report_link(
        token,
        artifact_path=str(path),
        room_id=ROOM,
        now=NOW,
        expires_at=expires_at,
    )


class TestRepositoryLinks:
    def test_live_link_resolves(self, repository, artifacts):
        _, pdf = artifacts
        link(repository, pdf)

        assert repository.resolve_report_link(TOKEN, now=NOW) == str(pdf)

    def test_expired_link_does_not_resolve(self, repository, artifacts):
        _, pdf = artifacts
        link(repository, pdf, expires_at=NOW + timedelta(minutes=1))

        assert repository.resolve_report_link(TOKEN, now=LATER) is None

    def test_unknown_token_resolves_to_nothing(self, repository):
        assert repository.resolve_report_link("nope", now=NOW) is None

    def test_expiry_must_be_in_the_future(self, repository, artifacts):
        _, pdf = artifacts

        with pytest.raises(ValueError, match="future"):
            link(repository, pdf, expires_at=NOW)

    def test_purge_removes_only_expired_links(self, repository, artifacts):
        _, pdf = artifacts
        link(repository, pdf, token="live", expires_at=NOW + timedelta(days=1))
        link(repository, pdf, token="dead", expires_at=NOW + timedelta(minutes=1))

        removed = repository.purge_expired_report_links(now=LATER)

        assert removed == 1
        assert repository.resolve_report_link("live", now=LATER) == str(pdf)
        assert repository.resolve_report_link("dead", now=LATER) is None


@pytest.mark.asyncio
async def test_valid_token_serves_the_pdf(aiohttp_client, repository, artifacts):
    root, pdf = artifacts
    link(repository, pdf)
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert "inline" in response.headers["Content-Disposition"]
    assert await response.read() == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_expired_token_is_not_served(aiohttp_client, repository, artifacts):
    root, pdf = artifacts
    link(repository, pdf, expires_at=NOW + timedelta(minutes=1))
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: LATER)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 404


@pytest.mark.asyncio
async def test_unknown_token_is_not_served(aiohttp_client, repository, artifacts):
    root, _ = artifacts
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get("/kakao/reports/whatever")

    assert response.status == 404


@pytest.mark.asyncio
async def test_a_path_outside_the_artifact_root_is_refused(
    aiohttp_client, repository, artifacts, tmp_path
):
    """A stored path is re-validated; the row outlives the code that wrote it."""

    root, _ = artifacts
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF secret")
    link(repository, outside)
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 404


@pytest.mark.asyncio
async def test_traversal_out_of_the_root_is_refused(
    aiohttp_client, repository, artifacts, tmp_path
):
    root, _ = artifacts
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF secret")
    link(repository, root / ".." / "secret.pdf")
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 404


@pytest.mark.asyncio
async def test_non_pdf_artifacts_are_refused(
    aiohttp_client, repository, artifacts
):
    root, _ = artifacts
    env = root / "leaked.env"
    env.write_text("OPENAI_API_KEY=sk-should-never-be-served")
    link(repository, env)
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 404


@pytest.mark.asyncio
async def test_a_deleted_file_is_refused(aiohttp_client, repository, artifacts):
    root, pdf = artifacts
    link(repository, pdf)
    pdf.unlink()
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    response = await client.get(f"/kakao/reports/{TOKEN}")

    assert response.status == 404


@pytest.mark.asyncio
async def test_the_whole_token_never_reaches_the_log(
    aiohttp_client, repository, artifacts, caplog
):
    root, pdf = artifacts
    link(repository, pdf)
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    logger_name = "kakao_bot.adapters.http.report_server"
    with caplog.at_level(logging.INFO, logger=logger_name):
        await client.get(f"/kakao/reports/{TOKEN}")
        await client.get("/kakao/reports/another-secret-token")

    ours = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == logger_name
    )
    assert TOKEN not in ours
    assert "another-secret-token" not in ours
    assert "opaque" in ours, "a prefix should still be logged to correlate"


def test_the_runtime_disables_aiohttps_access_log():
    """aiohttp's access log writes the full request line, token and all.

    Caught in review when the assertion above initially failed on the access
    logger rather than on ours.
    """

    import inspect

    from kakao_bot.runtime import report_server_main

    source = inspect.getsource(report_server_main.main)
    assert "access_log=None" in source


@pytest.mark.asyncio
async def test_there_is_no_directory_listing(aiohttp_client, repository, artifacts):
    root, _ = artifacts
    server = ReportLinkServer(repository, artifact_root=root, clock=lambda: NOW)
    client = await aiohttp_client(server.build_app())

    for path in ("/kakao/reports/", "/kakao/reports", "/"):
        response = await client.get(path)
        assert response.status in (404, 405), path


@pytest.mark.asyncio
async def test_the_url_never_contains_the_filename(repository, artifacts):
    """The point of the token is that the artifact name stays private."""

    from kakao_bot.application.analysis_service import AnalysisService

    _, pdf = artifacts
    service = AnalysisService(
        repository,
        analysis=None,  # not used by _link_url
        public_base_url="https://example.test",
    )
    url = service._link_url("abc123")

    assert url == "https://example.test/kakao/reports/abc123"
    assert pdf.name not in url


def test_no_public_base_url_means_no_link(repository, artifacts):
    from kakao_bot.application.analysis_service import AnalysisService

    service = AnalysisService(repository, analysis=None)

    assert service._link_url("abc123") is None
