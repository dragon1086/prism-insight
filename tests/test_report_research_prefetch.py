import asyncio
import json
from pathlib import Path

import pytest

from prism_core import report_research_prefetch as research
from tools.configure_report_research import configure

CONFIG = {"version": research.VERSION, "enabled": True}
TEXT = "Example Corp revenue increased with strong margins. " * 30


class Transport:
    def __init__(self, text=TEXT, publication="2025-01-01", fail=False):
        self.calls = []
        self.text = text
        self.publication = publication
        self.fail = fail

    async def __call__(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        if self.fail:
            raise RuntimeError("secret /private/path")
        if server == "perplexity":
            return {"results": [{"url": f"https://example.com/news/{i}"} for i in range(5)]}
        return {"success": True, "data": {"markdown": self.text,
                "metadata": {"publishedTime": self.publication}}}


def run(tmp_path, transport, **kwargs):
    return asyncio.run(research.prefetch_report_research(
        "US", "EXM", "20250918", "Example Corp", _transport=transport,
        _config=CONFIG, _cache_dir=tmp_path, **kwargs))


def test_off_no_config_read_or_transport(monkeypatch):
    monkeypatch.setenv("PRISM_REPORT_RESEARCH_ENABLED", "false")
    monkeypatch.setattr(Path, "read_text", lambda *a: pytest.fail("read"))
    assert asyncio.run(research.prefetch_report_research("US", "A", "20250101")) is None


def test_default_off(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISM_REPORT_RESEARCH_ENABLED", raising=False)
    monkeypatch.setattr(research, "CONFIG_PATH", tmp_path / "absent")
    assert asyncio.run(research.prefetch_report_research("US", "A", "20250101")) is None
    assert not list(tmp_path.iterdir())


def test_bounded_source_prefetch_cached(tmp_path):
    transport = Transport()
    packet = run(tmp_path, transport)
    assert len(transport.calls) == 3
    assert not packet["news_usable"]  # January data is not September news
    assert not packet["receipt"]["competitive_complete"]
    assert packet["receipt"]["usage"] == "UNKNOWN"
    assert sum(map(len, packet["section_notes"].values())) <= 6000
    cached = run(tmp_path, transport)
    assert cached["receipt"]["cache_hit"]
    assert cached["evidence_id"] == packet["evidence_id"]
    assert len(transport.calls) == 3
    assert "SOURCE_EXCERPT_NOT_FACT_VALIDATED" in packet["section_notes"]["news_analysis"]


@pytest.mark.parametrize("text,publication,expected", [
    ("자료를 요청 중입니다" * 80, "2025-01-01", "EMPTY_OR_BLOCKED"),
    (TEXT, "2099-01-01", "FUTURE_PUBLICATION"),
    (TEXT, "nonsense", "INVALID_PUBLICATION_DATE"),
    ("Other Company revenue increased. " * 30, "2025-01-01", "SUBJECT_NOT_FOUND"),
])
def test_bad_source_not_usable(tmp_path, text, publication, expected):
    packet = run(tmp_path, Transport(text, publication))
    assert not packet["news_usable"]
    assert expected in packet["receipt"]["gaps"]


def test_unknown_publication_explicit(tmp_path):
    packet = run(tmp_path, Transport(publication=None))
    assert '"published": "UNKNOWN"' in packet["section_notes"]["news_analysis"]


def test_failure_safe_negative_cache(tmp_path):
    transport = Transport(fail=True)
    packet = run(tmp_path, transport)
    assert not packet["news_usable"]
    assert "secret" not in json.dumps(packet)
    run(tmp_path, transport)
    assert len(transport.calls) == 1


def test_timeout(tmp_path):
    async def slow(*args):
        await asyncio.sleep(10)
    packet = asyncio.run(research.prefetch_report_research(
        "KR", "005930", "20250101", _transport=slow,
        _config={**CONFIG, "timeout_seconds": .01}, _cache_dir=tmp_path))
    assert packet["receipt"]["gaps"] == ["TIME_BUDGET_EXHAUSTED"]


def test_singleflight(tmp_path):
    transport = Transport()
    async def both():
        return await asyncio.gather(*[research.prefetch_report_research(
            "US", "EXM", "20250918", "Example Corp", _transport=transport,
            _config=CONFIG, _cache_dir=tmp_path) for _ in range(2)])
    a, b = asyncio.run(both())
    assert a["evidence_id"] == b["evidence_id"]
    assert len(transport.calls) == 3


@pytest.mark.parametrize("url", ["https://127.0.0.1/foo", "https://localhost/foo",
    "https://10.0.0.1/foo", "https://x.internal/foo", "https://user:pass@example.com/x",
    "http://example.com/a", "https://example.com/a?token=abc"])
def test_private_urls(url):
    assert research.public_url(url) is None


def test_pinned_search_text_parser():
    response = {"text": "Found 2 search results:\n\n1. **One**\n URL: https://example.com/a\n Text\n Date: 2025-01-01\n\n2. **Two**\n URL: https://example.com/b\n Text\n"}
    assert research._candidates(response) == [("https://example.com/a", "2025-01-01"), ("https://example.com/b", None)]


def test_config_nonsecret(tmp_path):
    path = tmp_path / "config.json"
    config = configure(path, True)
    assert json.loads(path.read_text()) == config
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        configure(path, True, 61)


def test_future_reference_no_requests(tmp_path):
    transport = Transport()
    assert asyncio.run(research.prefetch_report_research("US", "EXM", "20990101",
        _transport=transport, _config=CONFIG, _cache_dir=tmp_path)) is None
    assert not transport.calls


def test_partial_scrape_keeps_one_source(tmp_path):
    transport = Transport()
    async def partial(server, tool, arguments):
        if server == "firecrawl" and arguments["url"].endswith("/1"):
            raise ValueError("unavailable")
        return await transport(server, tool, arguments)
    packet = run(tmp_path, partial)
    assert not packet["news_usable"]
    assert packet["receipt"]["usable_sources"] == 1
    assert packet["receipt"]["calls"] == 3
    assert "SCRAPE_UNAVAILABLE" in packet["receipt"]["gaps"]


def test_context_budget_and_hashes(tmp_path):
    packet = run(tmp_path, Transport(TEXT * 200))
    notes = packet["section_notes"]
    assert len(notes["news_analysis"]) <= 3500
    assert len(notes["company_status"]) <= 1200
    assert len(notes["company_overview"]) <= 1200
    assert sum(map(len, notes.values())) <= 6000
    for source in packet["receipt"]["sources"]:
        assert len(source["source_sha256"]) == 64
        assert source["source_chars"] > source["excerpt_chars"]
        assert source["excerpt_truncated"]
        assert "excerpt" not in source


def test_market_cache_isolation(tmp_path):
    transport = Transport()
    run(tmp_path, transport)
    asyncio.run(research.prefetch_report_research("KR", "EXM", "20250918", "Example Corp",
        _transport=transport, _config=CONFIG, _cache_dir=tmp_path))
    assert len(transport.calls) == 6


def test_config_kill_switch_overrides_injected_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_REPORT_RESEARCH_ENABLED", "0")
    transport = Transport()
    assert run(tmp_path, transport) is None
    assert not transport.calls
    assert not list(tmp_path.iterdir())


def test_search_date_not_publication():
    source, gap = research._source({"markdown": TEXT}, "https://example.com/news/a",
        "2025-09-18", "2025-09-18", "Example Corp", "EXM")
    assert gap is None
    assert source["published"] == "UNKNOWN"
    assert source["search_date_hint"] == "2025-09-18"
    assert not source["recent_news"]


def test_recent_article_is_usable(tmp_path):
    packet = run(tmp_path, Transport(publication="2025-09-17"))
    assert not packet["news_usable"]
    assert packet["receipt"]["recent_news_present"]
    assert "SOURCE_METADATA_UNVERIFIED" in packet["section_notes"]["news_analysis"]


def test_numeric_table_rows_retained():
    source, _ = research._source({"markdown": "Example Corp\nRevenue\n| Period | Value |\n|2025Q1|123|\n" + "More company context. " * 30},
        "https://example.com/ir/a", None, "2025-09-18", "Example Corp", "EXM")
    assert "|2025Q1|123|" in source["excerpt"]


def test_korean_next_day_is_not_future(tmp_path, monkeypatch):
    from datetime import datetime as RealDatetime
    from datetime import timezone
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 17, 16, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(research, "datetime", Clock)
    packet = asyncio.run(research.prefetch_report_research("KR", "EXM", "20250918", "Example Corp",
        _transport=Transport(), _config=CONFIG, _cache_dir=tmp_path))
    assert packet is not None


def test_aggregated_summary_not_promoted():
    source, gap = research._source({"markdown": TEXT}, "https://quartr.com/companies/example_1",
        None, "2025-09-18", "Example Corp", "EXM")
    assert source is None
    assert gap == "AGGREGATED_SUMMARY_NOT_ORIGINAL"
