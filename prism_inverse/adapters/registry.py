"""Load ``registry/etfs.json`` into ``EtfSpec`` values."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from prism_inverse.domain.models import EtfSpec, Instrument

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "registry" / "etfs.json"


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
