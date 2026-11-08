"""Dependency rules (enforced by parsing imports) and adapter contracts."""

from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from prism_inverse.adapters import kis_data
from prism_inverse.adapters.registry import load_registry
from prism_inverse.domain.models import Instrument
from prism_inverse.ports import Unavailable, Unsupported
from prism_inverse.tests.helpers import spec

PKG = Path(__file__).resolve().parents[1]
STDLIB = set(sys.stdlib_module_names)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_domain_uses_only_stdlib_and_itself():
    for path in (PKG / "domain").glob("*.py"):
        for mod in _imports(path):
            top = mod.split(".")[0]
            assert top in STDLIB or mod.startswith("prism_inverse.domain"), (path.name, mod)


def test_app_and_ports_know_no_vendor():
    banned = ("kis", "yfinance", "requests", "pandas", "cores", "prism_core", "trading", "prism_inverse.adapters")
    for path in [PKG / "ports.py", *(PKG / "app").glob("*.py")]:
        for mod in _imports(path):
            assert not any(mod == b or mod.startswith(b + ".") or b in mod.split(".")[0] for b in banned), (path.name, mod)


def test_no_yfinance_and_no_order_code_anywhere():
    for path in PKG.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "yfinance" not in text or path.name == "kis_data.py" and "yfinance_basis" in text, path
        for word in ("buy_stock", "sell_stock", "order-cash", "submit_order"):
            assert word not in text, (path.name, word)


def test_registry_is_explicit_and_excludes_3x():
    specs = load_registry()
    assert len(specs) >= 30 and all(abs(s.multiplier) in (1, 2) for s in specs)
    assert {s.scope for s in specs} == {"index", "sector", "stock"}
    us = [s for s in specs if s.market == "US"]
    assert all(s.exchange in ("NAS", "NYS", "AMS") and s.underlying.exchange for s in us)
    assert next(s for s in us if s.code == "QID").exchange == "AMS"  # KIS: QID on NAS is blank (2026-11-05)
    codes = {s.code for s in specs}
    assert {"114800", "252670", "0197X0", "SH", "PSQ", "NVDD", "NVD"} <= codes and "SQQQ" not in codes


def test_frame_to_bars_contract():
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    f = pd.DataFrame({"Open": [10, 11, 11], "High": [11, 12, 11], "Low": [9, 10, 11], "Close": [10.5, 11.5, 11],
                      "Volume": [0, 100, 0]}, index=idx)
    out = kis_data.frame_to_bars(f, index_volume=True)
    assert [b.day.isoformat() for b in out] == ["2026-01-02", "2026-01-05"]  # flat no-volume filler dropped
    assert out[0].volume is None and out[1].volume == 100  # index volume 0 -> unknown
    assert kis_data.frame_to_bars(f.iloc[:1])[0].volume == 0  # stock/ETF 0 is a real value
    with pytest.raises(Unavailable):
        kis_data.frame_to_bars(f.iloc[2:])


class _FakeKis:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def _fetch(self, url, tr, params):
        self.calls.append((url, tr, params))
        return SimpleNamespace(output=self.output)

    def price_history(self, code, s, e, adjusted=True):
        idx = pd.bdate_range(end=pd.Timestamp(e), periods=25)
        return pd.DataFrame({"Open": 1340, "High": 1345, "Low": 1335, "Close": 1341, "Volume": 1e9}, index=idx)

    def index_history(self, code, s, e):
        raise type("Unsupported", (Exception,), {})("no code")


def test_kr_snapshot_reads_nav_fields_and_rejects_missing_price():
    src = _FakeKis({"stck_prpr": "1341", "nav": "1339.5", "dprt": "-0.11", "trc_errt": "1.62"})
    snap = kis_data.KisKrData(src).snapshot(spec(), date(2026, 11, 6))
    assert snap.price == 1341 and snap.tick == 1 and snap.premium_pct == pytest.approx(0.11)
    assert snap.tracking_error == pytest.approx(1.62) and snap.value_20d == pytest.approx(1341e9)
    assert src.calls[0][1] == kis_data.ETF_PRICE_TR
    with pytest.raises(Unavailable):
        kis_data.KisKrData(_FakeKis({"stck_prpr": ""})).snapshot(spec(), date(2026, 11, 6))
    with pytest.raises(Unsupported):
        kis_data.KisKrData(src).daily_bars(Instrument("KR", "2001", "index"), date(2026, 1, 1), date(2026, 2, 1))


def test_us_bars_pass_the_registry_exchange(monkeypatch):
    from prism_core import us_market_data as m

    seen = []

    def fake_fetch(url, tr, params):
        seen.append(params["EXCD"])
        return SimpleNamespace(output2=[{"xymd": "20261106", "open": "16.4", "high": "16.7", "low": "16.3",
                                         "clos": "16.5", "tvol": "3000000"}])

    monkeypatch.setattr(m, "_fetch", fake_fetch)
    bars = kis_data.KisUsData().daily_bars(Instrument("US", "QID", "etf", "AMS"), date(2026, 11, 6), date(2026, 11, 6))
    assert set(seen) == {"AMS"} and bars[-1].close == 16.5
    with pytest.raises(Unsupported):
        kis_data.KisUsData().daily_bars(Instrument("US", "QID", "etf"), date(2026, 11, 6), date(2026, 11, 6))
