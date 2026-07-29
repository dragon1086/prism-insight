import pytest

from prism_app import cli
from prism_app.cli import main


def test_phase1_cli_exposes_live_data_uat_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["live-data", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "read-only KR Phase 1 live-data UAT" in output
    assert "--symbol" in output
    assert "--benchmark" in output


def test_shadow_run_extends_the_existing_cli_without_secret_or_scheduler_flags(
    capsys,
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["shadow-run", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--evidence-json" in output
    assert "--research-db" in output
    assert "--ops-db" in output
    assert "--api-key" not in output
    assert "--token" not in output
    assert "--schedule" not in output
    assert "no account or broker capability" in output


def test_kr_daily_exposes_one_copyable_read_only_candidate_command(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["kr-daily", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out.lower()
    for option in (
        "--as-of",
        "--research-db",
        "--paper-db",
        "--ops-db",
        "--report-output",
        "--dashboard-output",
        "--stockeasy-snapshot",
    ):
        assert option in output
    assert "candidate" in output
    assert "no account or broker capability" in output
    assert "--symbol" not in output
    assert "--api-key" not in output
    assert "--token" not in output
    assert "--schedule" not in output


def test_kr_daily_routes_to_the_daily_product_entrypoint(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(cli.kr_daily_product, "main", lambda argv: observed.append(argv) or 7)

    assert main(["kr-daily", "--research-db", "research.sqlite"]) == 7
    assert observed == [["--research-db", "research.sqlite"]]


def test_us_shadow_run_is_exposed_without_account_or_scheduler_flags(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["shadow-run-us", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "fmp" in output
    assert "--symbol" in output
    assert "--benchmark" in output
    assert "--api-key" not in output
    assert "--schedule" not in output
    assert "no account or broker capability" in output


def test_phase1_cli_reads_persisted_shadow_into_existing_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    base = tmp_path / "existing.md"
    output = tmp_path / "combined.md"
    ops = tmp_path / "ops.sqlite"
    base.write_text("# 기존 PRISM 리포트\n\n기존 내용\n", encoding="utf-8")
    ops.write_bytes(b"placeholder")

    class Readback:
        markdown = (
            "<!-- PRISM_PHASE1_SHADOW_START -->\n"
            "## Phase 1 SHADOW\n"
            "<!-- PRISM_PHASE1_SHADOW_END -->\n"
        )

    observed: dict[str, object] = {}

    def fake_readback(path, *, job_key):
        observed.update(path=path, job_key=job_key)
        return Readback()

    monkeypatch.setattr(cli, "read_persisted_shadow", fake_readback)

    result = main(
        [
            "shadow-readback",
            "--ops-db",
            str(ops),
            "--job-key",
            "daily:KR:2026-07-26:daily-close",
            "--base-report",
            str(base),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert observed == {
        "path": ops,
        "job_key": "daily:KR:2026-07-26:daily-close",
    }
    combined = output.read_text(encoding="utf-8")
    assert combined.startswith("# 기존 PRISM 리포트")
    assert "Phase 1 SHADOW" in combined
