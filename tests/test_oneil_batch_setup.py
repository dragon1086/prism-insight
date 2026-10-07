from copy import deepcopy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from test_us_evidence_pipeline_contract import analysis, isolated_imports_and_effects  # noqa: F401

from prism_core import oneil_batch_setup as batch
from test_oneil_auto_review import AS_OF, valid_snapshot

_POPEN_INIT = subprocess.Popen.__init__


def test_atomic_write_failure_never_publishes_partial(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    monkeypatch.setattr(
        batch.os, "fsync", lambda *a: (_ for _ in ()).throw(OSError("write failed"))
    )
    with pytest.raises(OSError):
        batch._write_exclusive(path, {"value": 1})
    assert not path.exists() and list(tmp_path.iterdir()) == []


def test_atomic_write_race_one_winner_and_existing_preserved(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "record.json"

    def write(value):
        try:
            batch._write_exclusive(path, {"value": value})
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [1, 2])) == [False, True]
    before = path.read_bytes()
    assert not write(3) and path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


def module():
    path = Path(__file__).resolve().parents[1] / "prism-us/cores/data_prefetch.py"
    spec = importlib.util.spec_from_file_location("oneil_real_prefetch_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ONEIL_AUTO_REVIEW_CAPTURE_ENABLED", raising=False)
    assert not batch.enabled()


@pytest.mark.parametrize("active", [False, True])
def test_real_prefetch_same_frame_no_additional_target_fetch(monkeypatch, active):
    monkeypatch.setenv("ONEIL_AUTO_REVIEW_CAPTURE_ENABLED", "1" if active else "0")
    mod = module()
    frame = pd.DataFrame(
        dict(
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.0],
            volume=[1000.0],
            dividends=[0.0],
            stock_splits=[0.0],
        ),
        index=pd.DatetimeIndex(["2026-09-25"], tz="America/New_York"),
    )
    calls = []

    def price(*args, **kwargs):
        calls.append((args, kwargs))
        return frame.copy()

    monkeypatch.setattr(
        mod, "_get_us_data_client", lambda: SimpleNamespace(get_ohlcv=price)
    )
    metadata = {}
    text = mod.prefetch_us_stock_ohlcv("TEST", metadata=metadata)
    assert "OHLCV: TEST" in text and len(calls) == 1
    assert ("_oneil_prices" in metadata) is active
    if active:
        assert metadata["_oneil_prices"]["bars"][0]["stock_splits"] == 0
        assert metadata["_oneil_prices"]["available_at"]


def test_missing_actions_not_invented():
    frame = pd.DataFrame(dict(open=[100.0]), index=pd.DatetimeIndex(["2026-09-25"]))
    source = batch.frame_source(frame, "TEST")
    assert source["bars"][0]["stock_splits"] is None


def test_optional_price_failure_preserves_report(monkeypatch):
    monkeypatch.setenv("ONEIL_AUTO_REVIEW_CAPTURE_ENABLED", "on")
    mod = module()
    frame = pd.DataFrame(
        dict(open=[100.0], high=[101.0], low=[99.0], close=[100.0], volume=[10.0]),
        index=pd.DatetimeIndex(["2026-09-25"]),
    )
    monkeypatch.setattr(
        mod,
        "_get_us_data_client",
        lambda: SimpleNamespace(get_ohlcv=lambda *a, **k: frame),
    )
    monkeypatch.setattr(
        batch,
        "frame_source",
        lambda *a: (_ for _ in ()).throw(ValueError("bad optional data")),
    )
    assert "OHLCV: TEST" in mod.prefetch_us_stock_ohlcv("TEST", metadata={})


def test_assembly_reuses_inputs_and_only_fetches_benchmark():
    snapshot = valid_snapshot()
    prices = deepcopy(snapshot["prices"])
    financial = deepcopy(snapshot["financials"])
    calls = []

    def fetch(request):
        calls.append(request)
        return dict(status="timeout", retrieved_at=AS_OF)

    source = batch.assemble_batch_source(
        "TEST",
        prices,
        financial,
        dict(
            provider_symbol="TEST",
            provider_currency="USD",
            financial_currency="USD",
            exchange="NMS",
        ),
        benchmark_fetcher=fetch,
    )
    assert len(calls) == 1 and calls[0]["ticker"] == "SPY"
    assert source["snapshot"]["prices"] == prices
    assert source["snapshot"]["financials"]["records"] == financial["records"]
    assert source["snapshot"]["benchmark"]["bars"] == []
    assert source["snapshot"]["calendar"]["sessions"]


def test_unknown_currency_not_assumed_usd():
    snapshot = valid_snapshot()
    source = batch.assemble_batch_source(
        "TEST",
        snapshot["prices"],
        snapshot["financials"],
        dict(provider_symbol="TEST", exchange="NMS"),
        benchmark_fetcher=lambda request: dict(status="timeout"),
    )
    assert source["snapshot"]["prices"]["bars"] == []
    assert source["snapshot"]["financials"]["currency"] is None


@pytest.mark.parametrize("active", [False, True])
def test_real_aggregate_prefetch_opt_in_only(monkeypatch, active):
    monkeypatch.setenv("ONEIL_AUTO_REVIEW_CAPTURE_ENABLED", "1" if active else "0")
    mod = module()
    for name in (
        "prefetch_us_holder_info",
        "prefetch_recommendations",
        "prefetch_company_profile",
        "prefetch_segment_revenue",
        "prefetch_analysis_estimates",
    ):
        monkeypatch.setattr(mod, name, lambda *a, **kw: "")
    monkeypatch.setattr(mod, "prefetch_us_stock_ohlcv", lambda *a, **kw: "same-price")
    monkeypatch.setattr(mod, "prefetch_us_market_indices", lambda: {})
    monkeypatch.setattr(mod, "prefetch_stock_info", lambda *a, **kw: "same-info")
    calls = []
    monkeypatch.setattr(
        mod,
        "prefetch_financial_statements",
        lambda *a, **kw: calls.append(kw) or "same-finance",
    )
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", lambda *a: SimpleNamespace(sec_filings=[]))
    monkeypatch.setattr(
        batch, "assemble_batch_source", lambda *a: {"private": "not-prompt"}
    )
    output = mod.prefetch_us_analysis_data("TEST")
    assert output["financial_statements"] == "same-finance"
    assert ("_oneil_batch_source" in output) is active
    # metadata (the oneil sidecar) is passed only when opted in; quarterly_out is always passed.
    assert ("metadata" in calls[0]) is active


def test_sidecar_pdf_binding_and_same_source_replay(tmp_path):
    md = tmp_path / "md" / "TEST_report.md"
    pdf = tmp_path / "pdf" / "TEST_report.pdf"
    md.parent.mkdir()
    pdf.parent.mkdir()
    md.write_text("unchanged public report")
    pdf.write_bytes(b"exact PDF bytes")
    source = dict(snapshot=valid_snapshot(), reviewed_at=AS_OF)
    before = deepcopy(source)
    batch.write_sidecar(md, source)
    batch.bind_pdf_sidecar(md, pdf)
    assert source == before and md.read_text() == "unchanged public report"
    args = dict(symbol="TEST", decision_ref="report:TEST_report.pdf", as_of=AS_OF)
    result = batch.load_review_sidecar(pdf, **args)
    assert result["status"] == "OK"
    assert result["bundle"]["setup_input"]["status"] == "OK"
    assert result == batch.load_review_sidecar(pdf, **args)
    assert (
        batch.load_review_sidecar(pdf, **dict(args, symbol="WRONG"))["status"]
        == "MISSING"
    )
    assert (
        batch.load_review_sidecar(pdf, **dict(args, as_of="2026-09-01T00:00:00Z"))[
            "status"
        ]
        == "MISSING"
    )
    pdf.write_bytes(b"changed")
    assert batch.load_review_sidecar(pdf, **args)["status"] == "MISSING"


def test_changed_markdown_cannot_bind(tmp_path):
    md, pdf = tmp_path / "x.md", tmp_path / "x.pdf"
    md.write_text("original")
    pdf.write_bytes(b"pdf")
    batch.write_sidecar(md, dict(snapshot=valid_snapshot(), reviewed_at=AS_OF))
    md.write_text("changed")
    with pytest.raises(ValueError):
        batch.bind_pdf_sidecar(md, pdf)
    with pytest.raises(FileExistsError):
        batch.write_sidecar(md, dict(snapshot=valid_snapshot(), reviewed_at=AS_OF))


def test_corrupt_sidecar_is_missing(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"pdf")
    batch.sidecar_path(pdf).write_text(json.dumps({"private": "untrusted"}))
    result = batch.load_review_sidecar(
        pdf, symbol="TEST", decision_ref="report:x.pdf", as_of=AS_OF
    )
    assert result["status"] == "MISSING" and "bundle" not in result


def test_real_report_handoff_excludes_private_sidecar_from_prompts(
    analysis,  # noqa: F811
    monkeypatch,
):
    import asyncio

    source = dict(snapshot=valid_snapshot(), reviewed_at=AS_OF)
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                "_oneil_batch_source": source
            }

    def spec_for(name, path, *args, **kwargs):
        if name == "us_data_prefetch":
            return importlib.util.spec_from_loader(name, Loader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, "spec_from_file_location", spec_for)
    directory = analysis.get_us_agent_directory
    seen = []

    def agents(*args, **kwargs):
        seen.append(kwargs["prefetched_data"])
        assert "_oneil_batch_source" not in kwargs["prefetched_data"]
        return directory(*args, **kwargs)

    monkeypatch.setattr(analysis, "get_us_agent_directory", agents)

    async def synthesis(*args, **kwargs):
        return "UNCHANGED PUBLIC BODY"

    monkeypatch.setattr(analysis, "generate_investment_strategy", synthesis)
    monkeypatch.setattr(analysis, "generate_summary", synthesis)
    metadata = {}
    report = asyncio.run(
        analysis.analyze_us_stock(
            "TEST",
            "Example",
            "20260927",
            "en",
            include_news=False,
            research_metadata=metadata,
        )
    )
    assert seen and metadata["oneil_source"] == source
    assert "oneil-auto-review-input-v1" not in report


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("active", [False, True])
def test_real_orchestrator_report_pdf_sidecar(tmp_path, mode, active, monkeypatch):
    # Import namespace isolation matches the production US script entry point.
    # The shared report-fixture subprocess guard is intentionally lifted only for
    # this exact local Python test process; child sockets and dotenv are disabled.
    import os
    import subprocess
    import sys
    from unittest.mock import patch

    source = tmp_path / "fixture.json"
    source.write_text(json.dumps(dict(snapshot=valid_snapshot(), reviewed_at=AS_OF)))
    root = Path(__file__).resolve().parents[1]
    script = r"""
import asyncio, json, socket, sys
from pathlib import Path
from unittest.mock import patch
root, destination, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(root / 'prism-us'), str(root)]
def forbidden(*a, **k): raise AssertionError('network forbidden')
with patch.object(socket.socket, 'connect', forbidden), patch('dotenv.load_dotenv', return_value=False):
    import us_stock_analysis_orchestrator as orchestration
    import cores.us_analysis as analysis
    import pdf_converter
    from prism_core.oneil_batch_setup import enabled, sidecar_path, load_review_sidecar
    source = json.loads((destination / 'fixture.json').read_text())
    async def model(**kwargs):
        if enabled(): kwargs['research_metadata']['oneil_source'] = source
        else: assert 'research_metadata' not in kwargs
        return '# Unchanged public report'
    def render(src, dst, *a, **k): Path(dst).write_bytes(b'isolated pdf bytes')
    orchestration.US_REPORTS_DIR = destination
    orchestration.US_PDF_REPORTS_DIR = destination
    analysis.analyze_us_stock = model
    pdf_converter.markdown_to_pdf = render
    async def run():
        reports = await orchestration.USStockAnalysisOrchestrator.generate_reports(
            None, ['TEST'], mode, reference_date='20260927')
        assert len(reports) == 1
        pdfs = await orchestration.USStockAnalysisOrchestrator.convert_to_pdf(None, reports)
        assert len(pdfs) == 1 and Path(reports[0]).read_text() == '# Unchanged public report'
        assert sidecar_path(pdfs[0]).exists() == enabled()
        if enabled():
            value = load_review_sidecar(pdfs[0], symbol='TEST', decision_ref='report:' + Path(pdfs[0]).name,
                as_of='2026-09-27T12:00:00Z')
            assert value['status'] == 'OK', value
    asyncio.run(run())
"""
    env = dict(
        os.environ,
        ONEIL_AUTO_REVIEW_CAPTURE_ENABLED="1" if active else "0",
        PYTHON_DOTENV_DISABLED="1",
        PRISM_DISABLE_SIGNAL_PUBLISH="1",
    )
    with patch.object(subprocess.Popen, "__init__", _POPEN_INIT):
        result = subprocess.run(
            [sys.executable, "-c", script, str(root), str(tmp_path), mode],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-4000:]
