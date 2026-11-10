"""Load ``registry/etfs.json`` into ``EtfSpec`` values."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from prism_inverse.domain.lane import Product
from prism_inverse.domain.models import EtfSpec, Instrument

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "registry" / "etfs.json"
KR_SECTORS_PATH = DEFAULT_PATH.with_name("kr_sectors.json")


def load_kr_sectors(path: Path = KR_SECTORS_PATH) -> tuple[list[str], str]:
    """(sector ETF codes, relative-strength benchmark) for the KR signal recorder."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["sectors"]), data["benchmark"]


def load_us_sectors(path: Path = DEFAULT_PATH.with_name("us_sectors.json")) -> dict[str, str]:
    """ticker -> KIS exchange code for the US sector signal recorder."""
    return dict(json.loads(Path(path).read_text(encoding="utf-8"))["sectors"])


def load_us_inverse(path: Path = DEFAULT_PATH.with_name("us_sectors.json")) -> dict[str, Product]:
    """sector ticker -> real inverse product the live lane orders (S5). Sectors without one are never ordered."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {t: Product(code, int(mult), exchange) for t, (code, exchange, mult) in data["inverse"].items()}
    unknown = set(out) - set(data["sectors"])
    if unknown:
        raise ValueError(f"inverse products for sectors outside the universe: {sorted(unknown)}")
    return out


def load_registry(path: Path = DEFAULT_PATH) -> list[EtfSpec]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["etfs"]
    specs = []
    for row in rows:
        u = row["underlying"]
        spec = EtfSpec(
            code=row["code"], market=row["market"], name=row["name"], multiplier=int(row["multiplier"]),
            scope=row["scope"], underlying=Instrument(u["market"], u["code"], u["kind"], u.get("exchange")),
            listed=date.fromisoformat(row["listed"]), exchange=row.get("exchange"),
        )
        if spec.market == "US" and not (spec.exchange and spec.underlying.exchange):
            raise ValueError(f"{spec.code}: US entries need explicit KIS exchanges")
        specs.append(spec)
    return specs
