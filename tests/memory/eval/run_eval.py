"""
Run synthetic Recall@5 / MRR eval comparing V1 (recency) and V2 (hybrid).

Usage:
    python tests/memory/eval/run_eval.py [--check]

`--check` exits non-zero if V2 Recall@5 < 0.6 (merge gate).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Dict, List

# Ensure project root + tests dir importable.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TESTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (ROOT, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)

from tracking.memory import embed as embed_mod
from tracking.memory import retrieve as retrieve_mod
from tracking.memory.manager import UserMemoryManager

from tests.memory.conftest import FakeEmbedder
from tests.memory.eval.build_eval_set import USER_ID, build_eval_set


def _seed_db(db_path: str, journals: List[Dict]):
    m = UserMemoryManager(db_path)
    id_remap: Dict[int, int] = {}
    for j in journals:
        new_id = m.save_journal(
            user_id=j["user_id"],
            text=j["text"],
            ticker=j["ticker"],
            ticker_name=j["ticker_name"],
        )
        id_remap[j["id"]] = new_id
    return m, id_remap


def _v1_top_k(m: UserMemoryManager, user_id: int, query: str, k: int) -> List[int]:
    """V1 baseline = recency, ignoring the query (V1's get_journals)."""
    rows = m.get_journals(user_id, limit=k)
    return [r["id"] for r in rows]


def _v2_top_k(m: UserMemoryManager, user_id: int, query: str, k: int) -> List[int]:
    rows = m.search_memories(user_id, query=query, k=k)
    return [r["id"] for r in rows]


def _recall_at_k(retrieved: List[int], gold: List[int]) -> float:
    if not gold:
        return 0.0
    s_gold = set(gold)
    hits = sum(1 for r in retrieved if r in s_gold)
    return hits / len(s_gold)


def _mrr(retrieved: List[int], gold: List[int]) -> float:
    s_gold = set(gold)
    for i, r in enumerate(retrieved):
        if r in s_gold:
            return 1.0 / (i + 1)
    return 0.0


def evaluate(k: int = 5) -> Dict[str, float]:
    # Force the deterministic theme-aware embedder.
    embed_mod.set_provider(FakeEmbedder())
    retrieve_mod.reset_vector_cache()

    data = build_eval_set()
    journals = data["journals"]
    queries = data["queries"]

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "eval.sqlite")
        m, id_remap = _seed_db(db_path, journals)

        v1_recalls, v2_recalls = [], []
        v1_mrrs, v2_mrrs = [], []

        for q in queries:
            mapped_gold = [id_remap[gid] for gid in q["gold_ids"]]
            v1_top = _v1_top_k(m, USER_ID, q["query"], k)
            v2_top = _v2_top_k(m, USER_ID, q["query"], k)
            v1_recalls.append(_recall_at_k(v1_top, mapped_gold))
            v2_recalls.append(_recall_at_k(v2_top, mapped_gold))
            v1_mrrs.append(_mrr(v1_top, mapped_gold))
            v2_mrrs.append(_mrr(v2_top, mapped_gold))

        return {
            "v1_recall_at_5": sum(v1_recalls) / len(v1_recalls),
            "v2_recall_at_5": sum(v2_recalls) / len(v2_recalls),
            "v1_mrr": sum(v1_mrrs) / len(v1_mrrs),
            "v2_mrr": sum(v2_mrrs) / len(v2_mrrs),
            "n_queries": len(queries),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if V2 recall@5 < 0.6")
    ap.add_argument("--threshold", type=float, default=0.6)
    args = ap.parse_args()

    res = evaluate(k=5)
    print(f"queries: {res['n_queries']}")
    print(f"V1 recall@5: {res['v1_recall_at_5']:.3f}")
    print(f"V2 recall@5: {res['v2_recall_at_5']:.3f}")
    print(f"V1 MRR     : {res['v1_mrr']:.3f}")
    print(f"V2 MRR     : {res['v2_mrr']:.3f}")

    if args.check and res["v2_recall_at_5"] < args.threshold:
        print(
            f"FAIL: V2 recall@5 {res['v2_recall_at_5']:.3f} < threshold {args.threshold}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
