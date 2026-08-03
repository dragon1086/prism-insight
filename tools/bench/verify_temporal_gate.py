#!/usr/bin/env python3
"""
Verify the Temporal Gate.

`tbs` is a hint the search engine may ignore — a `qdr:m` request really did
return a `May 28, 2026` article. Until now that article reached the model with
its date attached and a prompt asking politely not to treat it as this week's
news. This gate turns that request into a filter.

What has to hold:

1. every date format the pipeline actually sees parses    — a format we cannot
                                                             read is a document
                                                             we silently drop
2. URL recovery works and rejects coincidences            — article IDs look
                                                             like dates
3. out-of-window and undated items are dropped
4. boundary is tolerant                                   — timezone skew must
                                                             not evict good news
5. an unknown window disables the gate                    — never guess a window
6. widening keys off survivors, not raw hits              — the whole point

Usage:  python3 tools/bench/verify_temporal_gate.py
"""
import ast
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cores.temporal_gate import (  # noqa: E402
    apply_temporal_gate, parse_published, recover_from_body, recover_from_url,
    window_days,
)

failures: list[str] = []
AS_OF = date(2026, 8, 3)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def test_parsing() -> None:
    print("\n[1] every observed date format parses")
    cases = [
        # 실제로 파이프라인에 들어오는 형태들
        ("2026-07-31", date(2026, 7, 31)),
        ("2026-08-01 (2 days ago)", date(2026, 8, 1)),      # _absolute_date 출력
        ("2026-07-31T09:00:00Z", date(2026, 7, 31)),
        ("2026-07-31T09:00:00+09:00", date(2026, 7, 31)),
        ("May 28, 2026", date(2026, 5, 28)),                # 창을 새어 나온 그 형식
        ("Jul 31, 2026", date(2026, 7, 31)),
        ("September 1, 2026", date(2026, 9, 1)),
        ("28 May 2026", date(2026, 5, 28)),
        ("2026.07.31", date(2026, 7, 31)),
        ("2026/07/31", date(2026, 7, 31)),
        ("2026년 7월 31일", date(2026, 7, 31)),
    ]
    for raw, want in cases:
        check(f"{raw!r} -> {want}", parse_published(raw) == want,
              f"got {parse_published(raw)}")

    print("  -- unparseable stays unparseable --")
    for raw in ("", "발행일 미상", "May 2026", "yesterday", "2026-13-45"):
        check(f"{raw!r} -> None", parse_published(raw) is None,
              f"got {parse_published(raw)}")


def test_url_recovery() -> None:
    print("\n[2] URL recovery, and coincidences rejected")
    good = [
        ("https://www.yna.co.kr/view/AKR20260731012300002", date(2026, 7, 31)),
        ("https://n.news.naver.com/article/2026/07/31/0001", date(2026, 7, 31)),
        ("https://biz.chosun.com/stock/2026-07-31/abc/", date(2026, 7, 31)),
    ]
    for url, want in good:
        check(f"...{url[-28:]} -> {want}",
              recover_from_url(url, as_of=AS_OF) == want,
              f"got {recover_from_url(url, as_of=AS_OF)}")

    bad = [
        "https://example.com/article/1234567890",      # 날짜 아님
        "https://example.com/2099/01/01/future",       # 미래
        "https://example.com/1999/12/31/too-old",      # 2000년 이전
        "https://example.com/no-digits-here",
        # 창 안의 날짜처럼 보이지만 발행일이 아닌 슬러그. 이걸 승격시키면
        # 날짜를 지어내는 셈이라 게이트가 막으려던 오염 그 자체가 된다.
        "https://example.com/company-20260731-results-preview",
        "https://example.com/reports?id=20260731999",
    ]
    for url in bad:
        check(f"rejects {url[-26:]}", recover_from_url(url, as_of=AS_OF) is None,
              f"got {recover_from_url(url, as_of=AS_OF)}")


def test_body_recovery() -> None:
    """
    The web channel barely ever carries a publish date. Measured: /theme kept
    only 2 of 7 before body recovery, so the gate was effectively deleting the
    whole channel. Body text is already in hand, so recovery is free.
    """
    print("\n[3] body recovery, and prose dates rejected")
    good = [
        ("입력 2026-07-31 15:56  코스피가 급등했다", date(2026, 7, 31)),
        ("기사입력 2026년 7월 31일 오전 9시", date(2026, 7, 31)),
        ("2026.07.31 15:56 | 기자 홍길동", date(2026, 7, 31)),
        ("Published 2026-07-29 by Reuters", date(2026, 7, 29)),
        ("등록 2026/07/28  반도체 업황", date(2026, 7, 28)),
    ]
    for body, want in good:
        check(f"{body[:26]!r} -> {want}",
              recover_from_body(body, as_of=AS_OF) == want,
              f"got {recover_from_body(body, as_of=AS_OF)}")

    bad = [
        "2026년 상반기 실적이 크게 개선됐다",          # 서술상의 날짜, 시각·라벨 없음
        "목표가는 2026년 12월 31일까지 유효하다",       # 미래
        "2019년 7월 31일 당시와 비교하면",             # 3년 초과 과거
        "특별한 날짜가 없는 본문",
    ]
    for body in bad:
        check(f"rejects {body[:24]!r}",
              recover_from_body(body, as_of=AS_OF) is None,
              f"got {recover_from_body(body, as_of=AS_OF)}")

    tail = ("x" * 900) + " 입력 2026-07-31 15:56"
    check("only the head is scanned", recover_from_body(tail, as_of=AS_OF) is None,
          f"got {recover_from_body(tail, as_of=AS_OF)}")


def _item(dt: str = "", url: str = "https://example.com/x", title: str = "t") -> dict:
    return {"date": dt, "url": url, "title": title, "body": "b"}


def test_gate_drops() -> None:
    print("\n[4] out-of-window and undated are dropped")
    items = [
        _item("2026-08-02"),                      # 창 안
        _item("2026-07-30"),                      # 창 안 (qdr:w = 8일)
        _item("May 28, 2026"),                    # 창 밖 — 실제 유출 사례
        _item(""),                                # 발행일 미상
        _item("", "https://www.yna.co.kr/view/AKR20260801010000001"),  # URL 복원
    ]
    kept, stats = apply_temporal_gate(items, tbs="qdr:w", as_of=AS_OF)

    check("kept 3 of 5", stats["kept"] == 3, str(stats))
    check("out-of-window counted", stats["dropped_out_of_window"] == 1, str(stats))
    check("undated counted", stats["dropped_undated"] == 1, str(stats))
    check("URL recovery counted", stats["recovered_from_url"] == 1, str(stats))
    check("May 28 article is gone",
          not any("May 28" in (i.get("date") or "") for i in kept))
    check("recovered item carries a normalized date",
          any("URL 복원" in (i.get("date") or "") for i in kept),
          str([i.get("date") for i in kept]))
    check("survivors carry _published for sorting",
          all(isinstance(i.get("_published"), date) for i in kept))

    # 복원 경로에만 상한이 있고 메타데이터 경로에 없어서 미래 발행일이 통과했다.
    # 미래 날짜는 신선한 게 아니라 소스가 틀린 것이다.
    future = [_item("September 1, 2026"), _item("2026-12-31"), _item("2026-08-04")]
    kept_f, st_f = apply_temporal_gate(future, tbs="qdr:w", as_of=AS_OF)
    check("future-dated items are dropped", st_f["dropped_future"] == 2, str(st_f))
    check("as_of+1 is still allowed (timezone skew)",
          len(kept_f) == 1 and kept_f[0]["date"] == "2026-08-04", str(st_f))


def test_boundary() -> None:
    print("\n[5] boundary is tolerant of timezone skew")
    # qdr:d 를 24시간으로 잡으면 KST 기사가 UTC 기준으로 하루 밀려 전량 탈락한다.
    kept, _ = apply_temporal_gate(
        [_item("2026-08-02"), _item("2026-08-03")], tbs="qdr:d", as_of=AS_OF)
    check("qdr:d keeps yesterday and today", len(kept) == 2, f"kept={len(kept)}")

    kept, _ = apply_temporal_gate([_item("2026-07-26")], tbs="qdr:w", as_of=AS_OF)
    check("qdr:w keeps 8 days back", len(kept) == 1)

    kept, _ = apply_temporal_gate([_item("2026-07-01")], tbs="qdr:w", as_of=AS_OF)
    check("qdr:w drops 33 days back", len(kept) == 0)

    check("window_days maps the ladder",
          [window_days(t) for t in ("qdr:d", "qdr:w", "qdr:m", "qdr:y")] == [2, 8, 32, 367])


def test_unknown_window() -> None:
    print("\n[6] an unknown window disables the gate")
    items = [_item("May 28, 2026"), _item("")]
    for tbs in (None, "", "qdr:decade"):
        kept, stats = apply_temporal_gate(items, tbs=tbs, as_of=AS_OF)
        check(f"tbs={tbs!r} passes everything through",
              len(kept) == 2 and stats["kept"] == 2, str(stats))
    check("window_days(None) is None", window_days(None) is None)


def test_widening_uses_survivors() -> None:
    """
    The gate is worthless if widening still keys off the raw hit count: a search
    that returns eight out-of-window articles would look like a success, gate to
    zero, and never widen.
    """
    print("\n[7] widening keys off gated survivors")
    src = (ROOT / "report_generator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "generate_firecrawl_search_response"
    )
    body = ast.dump(fn)
    check("gate is applied in the search path", "apply_temporal_gate" in body)

    whiles = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    check("a widening loop still exists", len(whiles) >= 1)
    if whiles:
        loop = whiles[0]
        check("loop re-applies the gate on each rung",
              "apply_temporal_gate" in ast.dump(loop))
        check("loop condition is the gated list",
              "widen_tbs" in ast.dump(loop.test) and "items" in ast.dump(loop.test),
              ast.dump(loop.test)[:110])

    check("sort uses the parsed date", "_published" in src)

    # 팩트 조회는 fail-soft 다 — build_kr_facts 는 "", daily_facts 는 {} 를
    # 돌려준다. 그게 게이트 전량 폐기와 겹치면 섹션이 통째로 사라진다.
    check("last-resort keeps stale items when facts are also missing",
          "_stale" in src and "not grounded_facts" in src)
    check("stale items are labelled in the context",
          "신선도 미검증" in src)


def test_live() -> None:
    """
    Pass rate against the real search. A gate that drops everything is worse
    than no gate — the answer loses its web context entirely. This is the check
    that cannot be faked with fixtures.
    """
    print("\n[8] LIVE: pass rate on real search results (costs credits)")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from firecrawl_client import firecrawl_search_multi
    from cores.search_presets import search_preset

    probes = [
        ("signal KR", "KR", "qdr:w", True, "코스피 증시 마감 시황"),
        ("theme KR", "KR", "qdr:m", True, "주식 테마 주도주 상승"),
        ("ask KR", "KR", "qdr:m", False, "반도체 업황 전망"),
    ]
    for label, mkt, tbs, allow, query in probes:
        opts = search_preset(mkt, tbs=tbs, allowlist=allow)
        items = firecrawl_search_multi([query], limit=8, with_content=True, **opts)
        kept, st = apply_temporal_gate(items, tbs=tbs)
        print(f"     {label:10} tbs={tbs:6} raw={st['total']:2} kept={st['kept']:2} "
              f"undated={st['dropped_undated']:2} outwin={st['dropped_out_of_window']:2} "
              f"urlrec={st['recovered_from_url']}")
        for i in kept[:2]:
            print(f"       KEEP {i.get('date')}  {(i.get('title') or '')[:42]}")
        check(f"{label}: gate leaves usable context", st["kept"] > 0,
              f"kept={st['kept']}/{st['total']}")


def main() -> int:
    test_parsing()
    test_url_recovery()
    test_body_recovery()
    test_gate_drops()
    test_boundary()
    test_unknown_window()
    test_widening_uses_survivors()
    if "--live" in sys.argv:
        test_live()
    else:
        print("\n[8] LIVE skipped (pass --live to run)")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
