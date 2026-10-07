import asyncio
import json
from datetime import date

import pandas as pd
import pytest

from prism_core import corporate_filings as cf

TODAY = date(2026, 10, 8)


def _sec(filings, subjects=None):
    """Fake SEC fetch: ticker map, one submissions list, optional index headers."""
    subjects = subjects or {}

    def fetch(url):
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"ticker": "ACVA", "cik_str": 1637873}}).encode()
        if "/submissions/" in url:
            cols = {"form": [], "filingDate": [], "items": [], "accessionNumber": [], "primaryDocument": []}
            for form, filed, items, acc in filings:
                for key, value in zip(cols, (form, filed, items, acc, "doc.htm")):
                    cols[key].append(value)
            return json.dumps({"filings": {"recent": cols}}).encode()
        acc = url.split("/")[-2]
        return (f"<pre>SUBJECT COMPANY:\n COMPANY CONFORMED NAME: X\n CENTRAL INDEX KEY: {subjects[acc]:010d}\n"
                f"FILED BY:\n CENTRAL INDEX KEY: 0000900075</pre>").encode()
    return fetch


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    # The repo conftest turns the lookup off for hermetic prompt tests.
    monkeypatch.setenv("CORP_FILINGS_ENABLED", "true")
    cf._ticker_map_cache.clear()
    cf._role_cache.clear()


def test_us_lists_target_events_and_drops_noise():
    fetch = _sec([
        ("SC TO-T", "2026-09-17", "", "000119312526393827"),
        ("SC TO-I", "2026-09-01", "", "000119312526000001"),
        ("8-K", "2026-09-25", "8.01,9.01", "000119312526000002"),
        ("8-K", "2026-09-20", "3.01", "000119312526000003"),
        ("4", "2026-10-01", "", "000119312526000004"),
        ("SC 13E3", "2026-01-02", "", "000119312526000005"),  # older than the lookback
    ], subjects={"000119312526393827": 1637873})
    items = cf.us_event_filings("ACVA", TODAY, fetch=fetch)
    assert [(i.label, i.kind) for i in items] == [
        ("SC TO-T", "event"), ("SC TO-I", "not_event"), ("8-K (3.01)", "event")]


def test_us_bidder_side_tender_is_not_an_event():
    fetch = _sec([("SCHEDULE TO-T/A", "2026-10-01", "", "000119312526409871")],
                 subjects={"000119312526409871": 999})
    [item] = cf.us_event_filings("ACVA", TODAY, fetch=fetch)
    assert item.kind == "not_event" and "bidder" in item.note


def test_block_marks_pre_purchase_filings_and_states_rules():
    fetch = _sec([("SC TO-T", "2026-09-17", "", "000119312526393827")], subjects={"000119312526393827": 1637873})
    block = cf.official_event_block("US", "ACVA", buy_date="2026-09-20 10:00:00", today=TODAY,
                                    language="en", us_fetch=fetch)
    assert "### Official filing check" in block and "SC TO-T" in block
    assert "(filed before purchase)" in block and "Quote the filing date" in block
    empty = cf.official_event_block("US", "ACVA", today=TODAY, language="ko", purpose="buy",
                                    us_fetch=_sec([]))
    assert "공식 공시가 없습니다" in empty and "신규 진입하지 마십시오" in empty


def test_kr_disclosures_keep_events_and_mark_buybacks():
    frame = pd.DataFrame([
        {"published_at": "2026-10-07 16:00", "provider": "공시", "title": "공개매수신고서(XX홀딩스)"},
        {"published_at": "2026-10-06 09:00", "provider": "공시", "title": "자기주식 공개매수 결정"},
        {"published_at": "2026-10-05 09:00", "provider": "연합뉴스", "title": "상장폐지 우려 기사"},
        {"published_at": "2026-09-20 09:00", "provider": "공시", "title": "주요사항보고서(유상증자결정)"},
    ])
    items, oldest = cf.kr_event_disclosures("123456", TODAY, fetch=lambda *a: frame)
    assert oldest == "2026-09-20"
    assert [(i.label, i.kind) for i in items] == [
        ("공개매수신고서(XX홀딩스)", "event"), ("자기주식 공개매수 결정", "not_event")]


def test_failures_and_switch_never_raise(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("http_403")
    block = cf.official_event_block("KR", "123456", today=TODAY, kr_fetch=boom)
    assert "자동 조회에 실패했습니다" in block
    monkeypatch.setenv("CORP_FILINGS_ENABLED", "false")
    assert cf.official_event_block("US", "ACVA", today=TODAY, us_fetch=boom) == ""


def test_async_timeout_returns_the_failed_lookup_block(monkeypatch):
    def slow(*_a, **_k):
        import time
        time.sleep(1)
        return pd.DataFrame()
    block = asyncio.run(cf.official_event_block_async("KR", "123456", timeout=0.05, today=TODAY, kr_fetch=slow))
    assert "자동 조회에 실패했습니다" in block


def test_every_trading_prompt_reads_official_filings():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name, needles in {
        "stock_tracking_agent.py": ['"KR", ticker, language=self.language, purpose="buy"'],
        "stock_tracking_enhanced_agent.py": ['"KR", ticker, buy_date=buy_date, language=self.language, purpose="sell"'],
        "prism-us/us_stock_tracking_agent.py": ['"US", ticker, language="en", purpose="buy"',
                                                '"US", ticker, buy_date=buy_date, language="en", purpose="sell"'],
    }.items():
        source = (root / name).read_text(encoding="utf-8")
        for needle in needles:
            assert source.count(needle) == 1, (name, needle)
