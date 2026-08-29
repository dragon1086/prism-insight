"""Resolve mutable runtime data without writing to tracked seed files."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STOCK_MAP_ENV_VARS = ("PRISM_STOCK_MAP_PATH", "KAKAO_STOCK_MAP_PATH")
_US_EXCHANGE_CACHE_ENV_VAR = "PRISM_US_EXCHANGE_CACHE_PATH"


def _configured_path(
    env_vars: tuple[str, ...],
    *,
    environ: Mapping[str, str],
) -> Path | None:
    for env_var in env_vars:
        value = environ.get(env_var)
        if value:
            return Path(value).expanduser()
    return None


def resolve_stock_map_write_path(
    explicit: str | Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the mutable stock-map target, never the tracked seed by default."""

    if explicit is not None:
        return Path(explicit).expanduser()
    env_path = _configured_path(
        _STOCK_MAP_ENV_VARS,
        environ=os.environ if environ is None else environ,
    )
    return env_path or project_root / "runtime" / "stock_map.json"


def resolve_stock_map_read_path(
    *,
    project_root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Prefer configured/runtime stock data and fall back to the tracked seed."""

    environment = os.environ if environ is None else environ
    configured_path = _configured_path(
        _STOCK_MAP_ENV_VARS,
        environ=environment,
    )
    default_runtime_path = project_root / "runtime" / "stock_map.json"
    for candidate in (configured_path, default_runtime_path):
        if candidate is not None and candidate.is_file():
            return candidate
    return project_root / "stock_map.json"


def resolve_us_exchange_cache_write_path(
    *,
    project_root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the runtime-only target for the mutable US exchange cache."""

    env_path = _configured_path(
        (_US_EXCHANGE_CACHE_ENV_VAR,),
        environ=os.environ if environ is None else environ,
    )
    return env_path or project_root / "runtime" / "us_exchange_cache.json"


def resolve_us_exchange_cache_read_path(
    *,
    project_root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Prefer the runtime exchange cache and fall back to the tracked seed."""

    environment = os.environ if environ is None else environ
    configured_path = _configured_path(
        (_US_EXCHANGE_CACHE_ENV_VAR,),
        environ=environment,
    )
    default_runtime_path = project_root / "runtime" / "us_exchange_cache.json"
    for candidate in (configured_path, default_runtime_path):
        if candidate is not None and candidate.is_file():
            return candidate
    return project_root / "prism-us" / "trading" / "data" / "exchange_cache.json"
