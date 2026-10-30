"""tools/clickstack_query.py stays read-only and passes values as query parameters (2026-10-18)."""
import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clickstack_query", ROOT / "tools/clickstack_query.py")
cq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cq)


def test_program_carries_values_as_parameters_not_sql():
    nasty = "x'); DROP TABLE otel_logs; --"
    program = cq.build_program("SELECT 1 WHERE TraceId = {trace:String}", {"trace": nasty})
    tail = program.split(cq.REMOTE, 1)[1].strip()
    assert tail.startswith("run(json.loads(") and tail.endswith("))")
    payload = json.loads(ast.literal_eval(tail[len("run(json.loads("):-2]))
    assert payload["sql"] == "SELECT 1 WHERE TraceId = {trace:String}"
    assert payload["params"] == {"trace": nasty}
    assert payload["limits"]["readonly"] == "1"


@pytest.mark.parametrize("query", ["DROP TABLE otel_logs", "INSERT INTO otel_logs VALUES (1)", "  alter table x", ""])
def test_sql_rejects_non_read_queries(query):
    args = type("A", (), {"query": query, "local": False})()
    with pytest.raises(SystemExit):
        cq.cmd_sql(args)


def test_summarize_pulls_decision_and_fill_fields():
    body = {"attributes": {
        "decision_context": {"buy_score": 7, "min_score": 5.0, "gate_reason": ""},
        "fill_provenance": {"status": "REJECTED", "reason_code": "ORDER_NOT_ACCEPTED"},
        "reason": "가" * 300,
        "triggers": [{"rank": 1}],
    }}
    text = cq.summarize(body, width=50)
    assert "buy_score=7" in text and "min_score=5.0" in text and "status=REJECTED" in text
    assert "gate_reason" not in text and "triggers" not in text
    assert "가" * 50 + "…" in text
    assert json.dumps(cq.summarize({}, 50)) == '""'


def test_recent_events_separate_future_records(monkeypatch, capsys):
    captured = []
    def query(sql, params, *, local):
        captured.append((sql, params, local))
        return [{'event': 'btc.scenario.settlement_recorded', 'market': 'CRYPTO', 'n': 9,
                 'future': 1, 'last_kst': '2026-10-30 01:00:00'},
                {'event': 'fixture', 'market': '', 'n': 0, 'future': 2, 'last_kst': '1970-01-01'}]
    monkeypatch.setattr(cq, 'run_query', query)
    args = type('A', (), {'days': 3, 'prefix': 'btc', 'local': False})()
    cq.cmd_events(args)
    output = capsys.readouterr().out
    assert 'future=1' in output and 'future=2' in output and 'last -' in output
    assert 'countIf(Timestamp <= now() + INTERVAL 5 MINUTE)' in captured[0][0]
    assert captured[0][1] == {'days': 3, 'prefix': 'btc'}


def test_recent_ticker_excludes_far_future_events(monkeypatch):
    captured = []
    monkeypatch.setattr(cq, 'run_query', lambda sql, params, *, local: captured.append(sql) or [])
    args = type('A', (), {'ticker': 'BTCUSDT', 'days': 3, 'limit': 10, 'all': False, 'local': False})()
    cq.cmd_ticker(args)
    assert 'Timestamp <= now() + INTERVAL 5 MINUTE' in captured[0]


@pytest.mark.parametrize("target", ["-oProxyCommand=sh", "a b", ""])
def test_rejects_ssh_option_injection(monkeypatch, target):
    monkeypatch.setenv("PRISM_DB_SSH", target)
    with pytest.raises(SystemExit, match="invalid PRISM_DB_SSH"):
        cq.run_query("SELECT 1")
