#!/usr/bin/env python3
"""
Verify the search-preset wiring end to end.

1. presets resolve to the expected kwargs
2. every /command call site actually passes search_opts
3. generate_firecrawl_search_response accepts the preset kwargs (signature check)
4. widen_tbs ladder terminates
5. live: the tuned path returns dated news where the bare path returned none

Usage:  python3 tools/bench/verify_search_presets.py [--live]
"""
import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from cores.search_presets import (  # noqa: E402
    KR_PRIMARY_DOMAINS, US_PRIMARY_DOMAINS, search_preset, widen_tbs,
)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def test_presets() -> None:
    print("\n[1] preset shape")
    kr = search_preset("KR", tbs="qdr:w")
    check("KR preset has tbs/sources/location/domains",
          kr["tbs"] == "qdr:w" and kr["sources"] == ["news", "web"]
          and kr["location"] == "KR" and kr["include_domains"] == KR_PRIMARY_DOMAINS)
    us = search_preset("US", tbs="qdr:m")
    check("US preset uses US domains",
          us["location"] == "US" and us["include_domains"] == US_PRIMARY_DOMAINS)
    free = search_preset("KR", tbs="qdr:m", allowlist=False)
    check("allowlist=False drops include_domains", "include_domains" not in free)
    try:
        search_preset("JP")
        check("unknown market rejected", False, "no ValueError raised")
    except ValueError:
        check("unknown market rejected", True)


def test_ladder() -> None:
    print("\n[2] widening ladder terminates")
    seen, cur, steps = [], "qdr:d", 0
    while cur and steps < 10:
        seen.append(cur)
        cur = widen_tbs(cur)
        steps += 1
    check("qdr:d -> ... -> None", cur is None and seen == ["qdr:d", "qdr:w", "qdr:m", "qdr:y"],
          " -> ".join(seen))
    check("unknown window returns None", widen_tbs("bogus") is None)


def test_signature() -> None:
    print("\n[3] generate_firecrawl_search_response accepts preset kwargs")
    from report_generator import generate_firecrawl_search_response
    params = inspect.signature(generate_firecrawl_search_response).parameters
    for key in ("tbs", "sources", "location", "include_domains"):
        check(f"accepts {key}", key in params)


def test_call_sites() -> None:
    """Every _run_firecrawl_command call must pass search_opts."""
    print("\n[4] bot call sites pass search_opts")
    src = (ROOT / "telegram_ai_bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("_run_firecrawl_command", "_run_search_and_claude"):
            continue
        found += 1
        kwargs = {k.arg for k in node.keywords}
        # definition site has no keywords; skip the def by requiring a call with args
        if not node.args and not kwargs:
            continue
        check(f"line {node.lineno} passes search_opts", "search_opts" in kwargs,
              f"kwargs={sorted(kwargs)}")
    check("found all 5 command call sites", found >= 5, f"found={found}")


def test_live() -> None:
    print("\n[5] LIVE: bare vs tuned (costs a few credits)")
    from firecrawl_client import firecrawl_search_multi
    q = "코스피 증시 마감 시황"
    bare = firecrawl_search_multi([q], limit=6, with_content=False)
    tuned = firecrawl_search_multi([q], limit=6, with_content=False,
                                   **search_preset("KR", tbs="qdr:w"))
    bd = sum(1 for i in bare if i.get("date"))
    td = sum(1 for i in tuned if i.get("date"))
    print(f"     bare : {len(bare)} results, {bd} dated")
    print(f"     tuned: {len(tuned)} results, {td} dated")
    check("tuned yields more dated results than bare", td > bd, f"{td} > {bd}")


def main() -> int:
    test_presets()
    test_ladder()
    test_signature()
    test_call_sites()
    if "--live" in sys.argv:
        test_live()
    else:
        print("\n[5] LIVE skipped (pass --live to run)")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
