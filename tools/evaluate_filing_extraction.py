"""Offline, source-grounded filing selection benchmark, not a truth certification."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path


def normalize(value: str) -> str:
    """Normalize presentation only: never drop punctuation, units or digits."""
    value = unicodedata.normalize("NFKC", value).replace("**", "")
    return re.sub(r"\s+", " ", value).strip()


def contains(text: str, needle: str) -> bool:
    """Literal presentation-normalized match without numeric suffix collisions."""
    needle = normalize(needle)
    if not needle:
        return False
    left = r"(?<![\d.,])" if needle[0].isdigit() else ""
    right = r"(?![\d]|[.,]\d)" if needle[-1].isdigit() else ""
    return bool(re.search(left + re.escape(needle) + right, text))


def score_records(facts: list[dict], records: list[dict]) -> dict:
    """Low-level tagged-record recall, not proof of correct source association.

    Benchmark callers must use source_bound_records first: arbitrary caller-made
    bundles can contain conflicting periods/tables even if all strings occur.
    """
    normalized = [(record, normalize(record.get("text", ""))) for record in records]
    results = []
    for fact in facts:
        groups = []
        for group in fact["evidence_groups"]:
            needles = group["needles"]
            context = group.get("required_context", [])
            scope = group.get("scope", fact.get("scope", "unknown"))
            eligible = [(r, t) for r, t in normalized
                        if scope == "unknown" or r.get("scope") == scope]
            matches = [r.get("block_id") for r, t in eligible
                       if needles and all(contains(t, n) for n in needles + context)]
            any_scope_matches = [r.get("block_id") for r, t in normalized
                                 if needles and all(contains(t, n) for n in needles + context)]
            groups.append({
                "full": bool(matches), "matching_block_ids": matches,
                "context_complete_any_scope": bool(any_scope_matches),
                "needle_count": len(needles),
                "literal_hits_any_scope": sum(any(contains(t, n) for _, t in normalized)
                                              for n in needles),
                "literal_hits_correct_scope": sum(any(contains(t, n) for _, t in eligible)
                                                  for n in needles),
                "missing_context_any_eligible_record": [c for c in context
                    if not any(contains(t, c) for _, t in eligible)],
                "reason": None if matches else (
                    "scope_missing" if not eligible else "no_single_complete_record"),
            })
        results.append({"id": fact["id"], "weight": fact.get("materiality_weight", fact.get("weight", 1)),
                        "financial_note": fact.get("financial_note", fact.get("category") == "financial_notes"),
                        "context_complete_any_scope": bool(groups) and all(
                            g["context_complete_any_scope"] for g in groups),
                        "full": bool(groups) and all(g["full"] for g in groups), "groups": groups})
    total_weight = sum(f["weight"] for f in results)
    needles_total = sum(g["needle_count"] for f in results for g in f["groups"])
    group_count = sum(len(f["groups"]) for f in results)
    full_group_count = sum(g["full"] for f in results for g in f["groups"])
    return {
        "facts": results, "full_items": sum(f["full"] for f in results), "total_items": len(results),
        "weighted_full_recall": sum(f["weight"] for f in results if f["full"]) / total_weight
            if total_weight else 0.0,
        "context_complete_items_any_scope": sum(f["context_complete_any_scope"] for f in results),
        "weighted_context_complete_recall_any_scope": sum(
            f["weight"] for f in results if f["context_complete_any_scope"]) / total_weight
            if total_weight else 0.0,
        "note_full_items": sum(f["full"] for f in results if f["financial_note"]),
        "note_total_items": sum(f["financial_note"] for f in results),
        "complete_evidence_groups": full_group_count,
        "total_evidence_groups": group_count,
        "evidence_group_recall": full_group_count / group_count if group_count else 0.0,
        "weighted_group_recall": sum(f["weight"] * sum(g["full"] for g in f["groups"])
            / len(f["groups"]) for f in results if f["groups"]) / total_weight if total_weight else 0.0,
        "partial_literal_recall": sum(g["literal_hits_any_scope"] for f in results for g in f["groups"])
            / needles_total if needles_total else 0.0,
        "total_weight": total_weight,
    }


def pareto_frontier(rows: list[dict]) -> list[dict]:
    """Empirical nondominance only, independently within each document."""
    result = []
    for row in rows:
        dominated = any(
            other["document_id"] == row["document_id"]
            and other["actual_payload_bytes"] <= row["actual_payload_bytes"]
            and other["weighted_full_recall"] >= row["weighted_full_recall"]
            and (other["actual_payload_bytes"] < row["actual_payload_bytes"]
                 or other["weighted_full_recall"] > row["weighted_full_recall"])
            for other in rows
        )
        if not dominated:
            result.append({k: row[k] for k in ("document_id", "method", "budget_bytes",
                          "actual_payload_bytes", "weighted_full_recall")})
    return result


def load_documents(paths: list[Path]) -> list[dict]:
    documents = []
    for path in paths:
        gold = json.loads(path.read_text())
        if "documents" in gold:
            documents.extend(gold["documents"])
        else:
            documents.append({"document_id": path.stem, "facts": gold["facts"],
                              "source_file": gold["source_path"],
                              "source_field": gold.get("source_field", "response.markdown"),
                              "markdown_sha256": gold["source_sha256"]})
    return documents


def load_source(document: dict) -> str:
    path = Path(document["source_file"])
    raw = path.read_bytes()
    if document.get("source_sha256") and hashlib.sha256(raw).hexdigest() != document["source_sha256"]:
        raise ValueError(f"Source file hash mismatch: {path}")
    data = json.loads(raw)
    if "source_field" in document:
        text = data
        for key in document["source_field"].split("."):
            text = text[key]
    else:
        text = data.get("response", data)
        if "data" in text and isinstance(text["data"], dict):
            text = text["data"]
        text = text["markdown"]
    if document.get("markdown_sha256") and hashlib.sha256(text.encode()).hexdigest() != document["markdown_sha256"]:
        raise ValueError(f"Markdown hash mismatch: {path}")
    return text


def source_url(document: dict) -> str:
    """Use the saved response's actual source URL in the metered envelope."""
    data = json.loads(Path(document["source_file"]).read_text())
    response = data.get("response", data)
    response = response.get("data", response)
    return response.get("metadata", {}).get("sourceURL", "")


def source_units(text: str) -> list[dict]:
    """Original atomic tables and contiguous, same-path/scope prose units."""
    from prism_core.filing_structure import parse_filing

    units = []
    for block in parse_filing(text):
        previous = units[-1] if units else None
        if (previous and previous["kind"] == block["kind"] == "prose"
                and not previous.get("is_heading") and not block.get("is_heading")
                and previous["section_path"] == block["section_path"]
                and previous["scope"] == block["scope"]
                and not text[previous["end"]:block["start"]].strip()):
            units[-1] = {**previous, "end": block["end"],
                         "text": text[previous["start"]:block["end"]]}
        else:
            units.append(dict(block))
    return units


def source_bound_records(records: list[dict], text: str, units: list[dict] | None = None) -> list[dict]:
    """Validate origin/scope and split delivery bundles along original units.

    Never join separate delivery records or separate source tables for a group.
    Selected rows retain original-table association; missing headers remain
    missing. Unselected source text is never supplied to the metric.
    """
    units = source_units(text) if units is None else units
    evaluated = []
    for record in records:
        spans = record.get("source_spans", [])
        if not spans:
            raise ValueError("Evidence record lacks source spans")
        last_end = -1
        for span in spans:
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or not all(type(v) is int for v in span)):
                raise ValueError("Invalid source offsets")
            a, b = span
            if not 0 <= a < b <= len(text) or a < last_end:
                raise ValueError("Source spans must be valid, sorted and nonoverlapping")
            last_end = b
        if record["text"] not in ("".join(text[a:b] for a, b in spans),
                                    "\n".join(text[a:b] for a, b in spans)):
            raise ValueError("Evidence text differs from declared source spans")
        for a, b in spans:
            cursor = a
            for unit in units:
                lo, hi = max(a, unit["start"]), min(b, unit["end"])
                if lo >= hi:
                    continue
                if text[cursor:lo].strip():
                    raise ValueError("Selected source text has no structural unit")
                if text[lo:hi].strip() and record.get("scope") != unit["scope"]:
                    raise ValueError("Declared scope differs from original source unit")
                cursor = hi
            if text[cursor:b].strip():
                raise ValueError("Selected source text has no structural unit")
        for unit in units:
            intersections = [[max(a, unit["start"]), min(b, unit["end"])] for a, b in spans
                             if max(a, unit["start"]) < min(b, unit["end"])]
            if not intersections:
                continue
            # Projection separators reproduce only selected slices. Adjacent
            # source slices may be contiguous or separated by omitted content.
            selected = "\n".join(text[a:b] for a, b in intersections)
            if selected.strip():
                evaluated.append({**record, "block_id": f"{record.get('block_id')}@{unit['start']}",
                                  "text": selected, "scope": unit["scope"],
                                  "section_path": unit["section_path"], "source_spans": intersections})
    return evaluated


def benchmark(documents: list[dict], selector, budgets: list[int], repeats: int,
              methods=("legacy", "structure_order", "structured")) -> dict:
    if repeats < 1 or not budgets or any(b < 1 for b in budgets):
        raise ValueError("Positive budgets and repeats required")
    rows = []
    for document in documents:
        text = load_source(document)
        url = source_url(document)
        units = source_units(text)
        for method in methods:
            for budget in budgets:
                timings, hashes = [], []
                for _ in range(repeats):
                    start = time.perf_counter()
                    packet = selector(text, source_id=document["document_id"], source_url=url,
                                      budget_bytes=budget, method=method)
                    timings.append((time.perf_counter() - start) * 1000)
                    payload = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
                    if len(payload.encode()) > budget:
                        raise ValueError(f"Selector exceeded budget: {method} {budget}")
                    evidence_records = source_bound_records(packet["records"], text, units)
                    hashes.append(hashlib.sha256(payload.encode()).hexdigest())
                result = score_records(document["facts"], evidence_records)
                rows.append({"document_id": document["document_id"], "method": method,
                             "budget_bytes": budget, "actual_payload_bytes": len(payload.encode()),
                             "record_text_bytes": sum(len(r.get("text", "").encode()) for r in packet["records"]),
                             "record_count": len(packet["records"]),
                             "source_evaluation_unit_count": len(evidence_records),
                             "records_without_source_spans": sum(not r.get("source_spans") for r in packet["records"]),
                             "packet_reported_bytes": packet.get("bytes"),
                             "processing_ms_median": statistics.median(timings),
                             "processing_ms_samples": timings, "deterministic_repeats": len(set(hashes)) == 1,
                             "payload_sha256": hashes[0], "source_chars": len(text),
                             "source_url": url,
                             "source_bytes": len(text.encode()), "provider_calls": 0, "model_calls": 0,
                             **result})
    return {"schema_version": 2, "metric_version": "source-unit-bound-v2",
            "truth_status": "source fidelity only; not independent truth certification",
            "metric_notes": "Strict literal all-groups recall; partial hits are not complete evidence. "
                "Full serialized packet bytes include metadata; not token counts or total report inputs. "
                "Groups are scored within original atomic tables or contiguous same-path/scope prose; "
                "delivery bundles are split and missing context is never filled from unselected source. "
                "Prose semantic association and conflicting headers inside one original table remain limitations. "
                "Empirical Pareto frontier is not global optimality. Processing includes selection only.",
            "rows": rows, "pareto_frontier": pareto_frontier(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budgets", default="3000,6000,9000,12000,18000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--methods", default="legacy,structure_order,structured")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from prism_core.filing_selection import select_filing_evidence
    report = benchmark(load_documents(args.gold), select_filing_evidence,
                       [int(b) for b in args.budgets.split(",")], args.repeats,
                       args.methods.split(","))
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__), root / "prism_core/filing_structure.py",
             root / "prism_core/filing_selection.py", root / "prism_core/filing_table_projection.py"]
    report["implementation_sha256"] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in files}
    report["gold_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.gold}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.out.chmod(0o600)
    print(json.dumps({"output": str(args.out), "rows": len(report["rows"]),
                      "pareto_candidates": len(report["pareto_frontier"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
