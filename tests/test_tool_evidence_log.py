import json
from types import SimpleNamespace

from cores.llm import codex_oauth_fast_backend as backend
from prism_core import tool_evidence_log


def _stream(result):
    return "\n".join([
        json.dumps({"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "sec_edgar", "tool": "search_filings",
            "arguments": {"ticker": "NVDA"}, "status": "completed", "result": result}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}),
    ])


def test_parse_stream_keeps_the_tool_reply_text():
    _, _, calls = backend._parse_stream(_stream({"content": [
        {"type": "text", "text": "No SC TO-T or 25-NSE filed"}, {"type": "image"},
        {"type": "text", "text": "https://www.sec.gov/x"}]}))
    assert calls[0].result_text == "No SC TO-T or 25-NSE filed\nhttps://www.sec.gov/x"
    _, _, calls = backend._parse_stream(_stream({"content": [], "structured_content": {"rows": [1]}}))
    assert calls[0].result_text == '{"rows": [1]}'
    _, _, calls = backend._parse_stream(_stream(None))
    assert calls[0].result_text == ""


def test_record_writes_trimmed_redacted_lines_and_prunes_old_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_EVIDENCE_LOG_DIR", str(tmp_path))
    old = tmp_path / "us_20260801.jsonl"
    old.write_text("{}\n")
    import os
    os.utime(old, (1, 1))
    reply = ("filings https://www.sec.gov/a?apikey=SECRET123 and https://www.sec.gov/a?apikey=SECRET123 "
             "bot 123456789:" + "A" * 35 + " " + "x" * 5000)
    call = backend.CodexMcpCall(server="sec_edgar", tool="search_filings",
                                arguments={"q": "NVDA", "url": "https://x?token=abc"},
                                status="completed", error=None, result_text=reply)
    result = SimpleNamespace(mcp_calls=(call, call))
    assert tool_evidence_log.record(market="US", ticker="NVDA", decision="sell", result=result,
                                    model="gpt-6.1-sol") == 2
    assert not old.exists()
    files = list(tmp_path.glob("us_*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    row = rows[0]
    assert (row["ticker"], row["decision"], row["tool"], row["status"]) == ("NVDA", "sell", "search_filings", "completed")
    assert row["urls"] == ["https://www.sec.gov/a?apikey=REDACTED"]
    assert "SECRET123" not in files[0].read_text() and "abc" not in json.dumps(row["arguments"])
    assert "<redacted-token>" in row["excerpt"]
    assert len(row["excerpt"]) == tool_evidence_log.MAX_EXCERPT_CHARS and row["result_chars"] > 5000


def test_record_is_off_switchable_and_never_raises(tmp_path, monkeypatch):
    call = backend.CodexMcpCall(server="s", tool="t", arguments={}, status="completed", error=None)
    monkeypatch.setenv("TOOL_EVIDENCE_LOG_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("TOOL_EVIDENCE_LOG_ENABLED", "false")
    assert tool_evidence_log.record(market="KR", ticker="005930", decision="buy",
                                    result=SimpleNamespace(mcp_calls=(call,))) == 0
    monkeypatch.delenv("TOOL_EVIDENCE_LOG_ENABLED")
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("TOOL_EVIDENCE_LOG_DIR", str(blocker / "sub"))
    assert tool_evidence_log.record(market="KR", ticker="005930", decision="buy",
                                    result=SimpleNamespace(mcp_calls=(call,))) == 0
    assert tool_evidence_log.record(market="KR", ticker="005930", decision="buy",
                                    result=SimpleNamespace(mcp_calls=())) == 0


def test_every_live_codex_trading_call_records_tool_evidence():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    expected = {"stock_tracking_agent.py": ['market="KR", ticker=ticker, decision="buy"'],
                "stock_tracking_enhanced_agent.py": ['market="KR", ticker=ticker, decision="sell"'],
                "prism-us/us_stock_tracking_agent.py": ['market="US", ticker=ticker, decision="buy"',
                                                        'market="US", ticker=ticker, decision="sell"']}
    for name, needles in expected.items():
        source = (root / name).read_text(encoding="utf-8")
        for needle in needles:
            assert source.count(f"record_tool_evidence({needle}") == 1, (name, needle)
