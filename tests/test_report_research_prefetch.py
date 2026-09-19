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
    assert all(source['published'] == 'UNKNOWN' for source in
               json.loads(packet["section_notes"]["news_analysis"])['sources'])


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


def test_question_led_search_keeps_network_budget(tmp_path):
    transport = Transport()
    packet = run(tmp_path, transport)
    query = transport.calls[0][2]["query"]
    assert "competition" in query and "annual report" in query
    assert "same-period" in packet["receipt"]["comparison_scope"]["questions"][1]
    assert len(transport.calls) == 3
    assert packet["receipt"]["comparison_scope"]["coverage"] == "UNVERIFIED_REQUIRES_SOURCE_REVIEW"
    assert not packet["news_usable"]


MEMORY_TABLE = ("Global DRAM Market Share by Revenue\n"
                "| Market Share | Q1 2025 | Q2 2025 |\n| --- | --- | --- |\n"
                "| Samsung | 40% | 38% |\n| SK Hynix | 35% | 37% |\n"
                "| Micron | 25% | 25% |")


@pytest.mark.parametrize("market,symbol,company", [
    ("US", "MU", "Micron Technology"), ("KR", "005930", "삼성전자"),
    ("KR", "000660", "SK하이닉스"),
])
def test_reviewed_memory_source_alias_and_complete_comparison(tmp_path, market, symbol, company):
    # Synthetic values test preservation, not the publisher's real market data.
    text = "Navigation text. " * 50 + "\n" + MEMORY_TABLE + "\nRounding may apply."
    transport = Transport(text)
    packet = asyncio.run(research.prefetch_report_research(
        market, symbol, "20250918", company, _transport=transport,
        _config=CONFIG, _cache_dir=tmp_path))
    assert transport.calls[1][2]["url"] == research.MEMORY_SOURCE
    assert len(transport.calls) == 3
    note = json.loads(packet["section_notes"]["news_analysis"])
    assert MEMORY_TABLE in note["sources"][0]["excerpt"]
    assert note["comparison_scope"]["business_scope"].startswith("DRAM and HBM separately")
    assert not packet["receipt"]["competitive_complete"]
    assert packet["receipt"]["injected_sources"] == len(note["sources"])
    assert all("| Samsung" not in packet["section_notes"][section]
               for section in ("company_status", "company_overview"))


def test_known_source_still_collected_when_search_unavailable(tmp_path):
    async def transport(server, tool, args):
        if server == "perplexity":
            raise ValueError("provider unavailable")
        return {"markdown": MEMORY_TABLE + "\nContext sentence. " * 30}
    packet = asyncio.run(research.prefetch_report_research(
        "US", "MU", "20250918", "Micron", _transport=transport,
        _config=CONFIG, _cache_dir=tmp_path))
    assert packet["receipt"]["usable_sources"] == 1
    assert packet["receipt"]["calls"] == 2
    assert "SEARCH_UNAVAILABLE" in packet["receipt"]["gaps"]


def test_comparison_table_outranks_repeated_generic_prose():
    text = "Revenue increased. " * 70 + "\n" + MEMORY_TABLE
    excerpt, omitted = research._bounded_excerpt(text, 700)
    assert MEMORY_TABLE in excerpt
    assert omitted


@pytest.mark.parametrize("scope", [{"US": ["MU"]}, {}, [], {"US": "EXM"},
                                   {"US": ["EXM"], "KR": [42]}])
def test_scoped_activation_rechecks_before_existing_cache(tmp_path, scope):
    transport = Transport()
    assert run(tmp_path, transport) is not None  # valid cache from unrestricted run
    calls = len(transport.calls)
    result = asyncio.run(research.prefetch_report_research(
        "US", "EXM", "20250918", "Example Corp", _transport=transport,
        _config={**CONFIG, "validated_symbols": scope}, _cache_dir=tmp_path))
    assert result is None
    assert len(transport.calls) == calls


def test_approved_symbol_can_reuse_identical_evidence_cache(tmp_path):
    transport = Transport()
    first = run(tmp_path, transport)
    result = asyncio.run(research.prefetch_report_research(
        "US", "EXM", "20250918", "Example Corp", _transport=transport,
        _config={**CONFIG, "validated_symbols": {"US": ["EXM"]}}, _cache_dir=tmp_path))
    assert result["evidence_id"] == first["evidence_id"]
    assert result["receipt"]["cache_hit"]
    assert len(transport.calls) == 3
