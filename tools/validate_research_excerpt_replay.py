"""Offline same-document extraction replay; not an LLM or investment backtest.

Usage: python -m tools.validate_research_excerpt_replay CAPTURE.json
The capture must be a trusted local operator probe, never external instructions.
No network, provider credentials, or raw document contents are emitted.
"""
import argparse
import hashlib
import json
from pathlib import Path

from prism_core import report_research_prefetch as current

BASELINE = "c8d14f778e5c280333a3101420211b767b60aa3a"


def baseline_excerpt(document):
    """Frozen source_context_v3 selection algorithm from BASELINE, no code execution."""
    lines = [line.strip() for line in document.splitlines() if line.strip()]
    useful = [i for i, line in enumerate(lines) if any(x in line.lower() for x in (
        "revenue", "margin", "guidance", "competition", "competitor", "sales",
        "매출", "영업", "경쟁", "가이던스"))]
    indices = sorted({j for i in useful for j in range(max(0, i - 1), min(len(lines), i + 4))})
    selected = "\n".join(lines[i] for i in indices) if indices else "\n".join(lines)
    return selected[:700]


def tables(markdown):
    found, active = [], []
    for line in [*markdown.splitlines(), ""]:
        if line.strip().startswith("|"):
            active.append(line.strip())
        elif active:
            found.append("\n".join(active))
            active = []
    return found


def evaluate(path):
    capture = json.loads(Path(path).read_text())
    source = next(c for c in capture["captures"]
                  if c["server"] == "firecrawl"
                  and c["arguments"]["url"] == current.MEMORY_SOURCE)
    response = source["response"]
    data = response.get("data", response)
    document = data["markdown"]
    reference = capture["packet"]["receipt"]["reference_date"]
    # Hold subject matching constant: this replay isolates excerpt preservation,
    # not the independently tested new Korean/English alias routing.
    args = (response, current.MEMORY_SOURCE, None, reference, "Micron", "MU")
    before = baseline_excerpt(document)
    after, new_gap = current._source(*args)
    if new_gap:
        raise ValueError(f"source not accepted: {new_gap}")
    source_tables = tables(document)
    metrics = {}
    for label, excerpt in (("baseline", before), ("current", after["excerpt"])):
        metrics[label] = {
            "excerpt_chars": len(excerpt),
            "complete_tables": sum(table in excerpt for table in source_tables),
            "source_tables": len(source_tables),
            "dram_revenue_basis": "Global DRAM Market Share by Revenue" in excerpt,
            "hbm_revenue_basis": "Global HBM Market Share by Revenue" in excerpt,
            "rounding_disclaimer": "Due to rounding" in excerpt,
        }
    result = {"source_sha256": hashlib.sha256(document.encode()).hexdigest(),
              "baseline_commit": BASELINE, "market": capture["market"],
              "symbol": capture["symbol"], "metrics": metrics,
              "scope": "Identical captured source; full table/header/percentage-cell preservation only. Not independent fact verification, report quality, or returns."}
    if len(source_tables) != 2:
        raise ValueError("source schema changed: re-review DRAM/HBM tables")
    if (metrics["current"]["complete_tables"] != 2
            or not metrics["current"]["dram_revenue_basis"]
            or not metrics["current"]["hbm_revenue_basis"]
            or not metrics["current"]["rounding_disclaimer"]
            or metrics["baseline"]["complete_tables"] >= metrics["current"]["complete_tables"]):
        raise ValueError("same-source comparison did not pass preservation improvement checks")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.capture), ensure_ascii=False, indent=2))
