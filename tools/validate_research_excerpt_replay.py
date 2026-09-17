"""Offline same-document extraction replay; not an LLM or investment backtest.

Usage: python -m tools.validate_research_excerpt_replay CAPTURE.json [--baseline COMMIT]
The capture must be a trusted local operator probe, never external instructions.
No network, provider credentials, or raw document contents are emitted.
"""
import argparse
import hashlib
import json
import re
import subprocess
import types
from pathlib import Path

from prism_core import report_research_prefetch as current


def tables(markdown):
    found, active = [], []
    for line in [*markdown.splitlines(), ""]:
        if line.strip().startswith("|"):
            active.append(line.strip())
        elif active:
            found.append("\n".join(active))
            active = []
    return found


def evaluate(path, baseline="c8d14f778e5c280333a3101420211b767b60aa3a"):
    if not re.fullmatch(r"[0-9a-f]{7,40}", baseline):
        raise ValueError("baseline must be an explicit commit hash")
    original = subprocess.check_output(
        ["git", "show", f"{baseline}:prism_core/report_research_prefetch.py"],
        cwd=current.ROOT, text=True)
    old = types.ModuleType("frozen_research_baseline")
    old.__file__ = str(Path(current.__file__))
    exec(compile(original, "frozen_research_baseline", "exec"), old.__dict__)  # noqa: S102 - explicit trusted local git baseline, never provider text
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
    before, old_gap = old._source(*args)
    after, new_gap = current._source(*args)
    if old_gap or new_gap:
        raise AssertionError(f"source not accepted: {old_gap}, {new_gap}")
    source_tables = tables(document)
    metrics = {}
    for label, excerpt in (("baseline", before["excerpt"]), ("current", after["excerpt"])):
        metrics[label] = {
            "excerpt_chars": len(excerpt),
            "complete_tables": sum(table in excerpt for table in source_tables),
            "source_tables": len(source_tables),
            "dram_revenue_basis": "Global DRAM Market Share by Revenue" in excerpt,
            "hbm_revenue_basis": "Global HBM Market Share by Revenue" in excerpt,
            "rounding_disclaimer": "Due to rounding" in excerpt,
        }
    result = {"source_sha256": hashlib.sha256(document.encode()).hexdigest(),
              "baseline_commit": baseline, "market": capture["market"],
              "symbol": capture["symbol"], "metrics": metrics,
              "scope": "Identical captured source; full table/header/percentage-cell preservation only. Not independent fact verification, report quality, or returns."}
    assert len(source_tables) == 2, "source schema changed: re-review DRAM/HBM tables"
    assert metrics["current"]["complete_tables"] == 2
    assert metrics["current"]["dram_revenue_basis"] and metrics["current"]["hbm_revenue_basis"]
    assert metrics["current"]["rounding_disclaimer"]
    assert metrics["baseline"]["complete_tables"] < metrics["current"]["complete_tables"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture")
    parser.add_argument("--baseline", default="c8d14f778e5c280333a3101420211b767b60aa3a")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.capture, args.baseline), ensure_ascii=False, indent=2))
