import json

import pytest

from tools.probe_dart_filings import main

ARGS = ["--corp-code", "01343665", "--decision-at", "2026-09-18T15:30:00+09:00",
        "--start-date", "2025-01-01", "--scope", "consolidated"]


def test_no_live_never_calls_collector(capsys):
    async def forbidden(**kwargs):
        pytest.fail("network")

    assert main(ARGS, collector=forbidden) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "LIVE_ACK_REQUIRED"


def test_aware_cutoff_and_sanitized_receipt(tmp_path):
    seen = []

    async def collect(**kwargs):
        seen.append(kwargs)
        return {"status": "COMPLETE_WITHIN_QUERY", "filings": [
            {"receipt_id": "synthetic", "sections": [{"source_url": "https://dart.fss.or.kr/x",
             "sha256": "abc", "preview": "private body", "raw_html": "private body"}]}],
            "selection": {"primary_id": "synthetic", "latest_confirmed": False}}

    path = tmp_path / "receipt.json"
    assert main(ARGS + ["--live", "--out", str(path)], collector=collect) == 0
    result = json.loads(path.read_text())
    assert "private" not in path.read_text()
    assert result["selection"]["latest_confirmed"] is False
    assert result["probe_elapsed_seconds"] >= 0
    assert seen[0]["decision_at"].utcoffset().total_seconds() == 9 * 3600
    assert seen[0]["corp_code"] == "01343665"


def test_existing_output_not_overwritten_or_queried(tmp_path, capsys):
    path = tmp_path / "receipt.json"
    path.write_text("existing")

    async def forbidden(**kwargs):
        pytest.fail("network")

    assert main(ARGS + ["--live", "--out", str(path)], collector=forbidden) == 2
    assert path.read_text() == "existing"
    assert json.loads(capsys.readouterr().out)["reason"] == "OUTPUT_EXISTS"


@pytest.mark.parametrize("status,code", [("EMPTY", 0), ("PARTIAL", 2), ("FAILED", 2)])
def test_status_exit_code(status, code, capsys):
    async def collect(**kwargs):
        return {"status": status}

    assert main(ARGS + ["--live"], collector=collect) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_failure_is_static(capsys):
    async def collect(**kwargs):
        raise RuntimeError("secret")

    assert main(ARGS + ["--live"], collector=collect) == 2
    assert capsys.readouterr().out == '{"status": "FAILED", "reason": "PROBE_FAILED"}\n'


def test_naive_cutoff_fails_before_collection():
    args = ARGS.copy()
    args[3] = "2026-09-18T15:30:00"

    async def forbidden(**kwargs):
        pytest.fail("network")

    with pytest.raises(SystemExit):
        main(args + ["--live"], collector=forbidden)
