"""Build an offline ADX-like research diagnostic from an explicit sanitized Packet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.trend_quality_research import build_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--features", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in (args.packet, args.features) if p}:
        parser.error("output must not overwrite inputs")
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    features = json.loads(args.features.read_text(encoding="utf-8")) if args.features else None
    study = build_study(packet, features)
    args.output.write_text(json.dumps(study, ensure_ascii=False, sort_keys=True,
                                     indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: study[k] for k in ("status", "study_id", "verdict")}))


if __name__ == "__main__":
    main()
