from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import update_stock_data
from prism_core.runtime_paths import (
    resolve_stock_map_read_path,
    resolve_stock_map_write_path,
    resolve_us_exchange_cache_read_path,
    resolve_us_exchange_cache_write_path,
)


def _write_json(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stock_map_read_prefers_runtime_then_tracked_seed(tmp_path: Path) -> None:
    seed = tmp_path / "stock_map.json"
    runtime = tmp_path / "runtime" / "stock_map.json"
    _write_json(seed, {"source": "seed"})

    assert resolve_stock_map_read_path(project_root=tmp_path, environ={}) == seed

    _write_json(runtime, {"source": "runtime"})
    assert resolve_stock_map_read_path(project_root=tmp_path, environ={}) == runtime


def test_stock_map_env_override_and_default_write_target(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "stocks.json"
    _write_json(override, {"source": "override"})
    environ = {"PRISM_STOCK_MAP_PATH": str(override)}

    assert (
        resolve_stock_map_read_path(project_root=tmp_path, environ=environ) == override
    )
    assert (
        resolve_stock_map_write_path(project_root=tmp_path, environ=environ) == override
    )
    assert resolve_stock_map_write_path(project_root=tmp_path, environ={}) == (
        tmp_path / "runtime" / "stock_map.json"
    )


def test_stock_map_legacy_kakao_override_remains_supported(tmp_path: Path) -> None:
    override = tmp_path / "legacy-stock-map.json"
    _write_json(override, {"source": "legacy"})

    assert (
        resolve_stock_map_read_path(
            project_root=tmp_path,
            environ={"KAKAO_STOCK_MAP_PATH": str(override)},
        )
        == override
    )


def test_missing_stock_map_override_falls_back_to_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "stock_map.json"
    _write_json(runtime, {"source": "runtime"})

    assert (
        resolve_stock_map_read_path(
            project_root=tmp_path,
            environ={"PRISM_STOCK_MAP_PATH": str(tmp_path / "missing.json")},
        )
        == runtime
    )


def test_exchange_cache_reads_seed_but_writes_runtime(tmp_path: Path) -> None:
    seed = tmp_path / "prism-us" / "trading" / "data" / "exchange_cache.json"
    runtime = tmp_path / "runtime" / "us_exchange_cache.json"
    _write_json(seed, {"AAPL": "NASD"})

    assert (
        resolve_us_exchange_cache_read_path(
            project_root=tmp_path,
            environ={},
        )
        == seed
    )
    assert (
        resolve_us_exchange_cache_write_path(
            project_root=tmp_path,
            environ={},
        )
        == runtime
    )

    _write_json(runtime, {"AAPL": "NASD", "IBM": "NYSE"})
    assert (
        resolve_us_exchange_cache_read_path(
            project_root=tmp_path,
            environ={},
        )
        == runtime
    )


def test_exchange_cache_env_path_is_runtime_write_target(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "exchange.json"
    environ = {"PRISM_US_EXCHANGE_CACHE_PATH": str(override)}

    assert (
        resolve_us_exchange_cache_write_path(
            project_root=tmp_path,
            environ=environ,
        )
        == override
    )


def test_missing_exchange_override_falls_back_to_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "us_exchange_cache.json"
    _write_json(runtime, {"AAPL": "NASD"})

    assert (
        resolve_us_exchange_cache_read_path(
            project_root=tmp_path,
            environ={"PRISM_US_EXCHANGE_CACHE_PATH": str(tmp_path / "missing.json")},
        )
        == runtime
    )


def test_us_trading_cache_save_never_modifies_tracked_seed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    module_path = project_root / "prism-us" / "trading" / "us_stock_trading.py"
    runtime_cache = tmp_path / "runtime" / "exchange.json"
    monkeypatch.setenv("PRISM_US_EXCHANGE_CACHE_PATH", str(runtime_cache))

    spec = importlib.util.spec_from_file_location(
        "runtime_path_us_trading", module_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seed_path = project_root / "prism-us" / "trading" / "data" / "exchange_cache.json"
    seed_before = seed_path.read_bytes()
    module._save_exchange_cache({"ZZZZ": "NYSE"})

    assert json.loads(runtime_cache.read_text(encoding="utf-8")) == {"ZZZZ": "NYSE"}
    assert seed_path.read_bytes() == seed_before


def test_stock_update_default_writes_configured_runtime_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_map = tmp_path / "runtime" / "stock_map.json"

    class FakeClient:
        def get_market_ticker_name(self, *, market: str) -> dict[str, str]:
            assert market == "ALL"
            return {"005930": "삼성전자"}

    monkeypatch.setenv("PRISM_STOCK_MAP_PATH", str(runtime_map))
    monkeypatch.setattr(update_stock_data, "_get_client", lambda: FakeClient())

    assert update_stock_data.update_stock_data()
    payload = json.loads(runtime_map.read_text(encoding="utf-8"))
    assert payload["code_to_name"] == {"005930": "삼성전자"}
    assert payload["name_to_code"] == {"삼성전자": "005930"}
