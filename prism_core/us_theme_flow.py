"""Today's US theme flow for the US signal alert (roadmap U4, KR mirror: kr_market_theme_flow / kr_theme_brief).

The US theme map (runtime/us_theme_map_vN.json, ~1,000 names) is too large to quote at alert time on the db-server
(about three minutes through yfinance), so `tools/build_us_theme_flow.py` runs a few minutes before each US batch and
writes runtime/us_theme_flow.json. The alert renders that file when it is fresh and otherwise keeps the industry-ETF
line (prism_core/us_sector_brief.py). Deterministic, no model call. Descriptive only: never a trading input, and a
headline is shown as a related title, not as the cause.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "runtime" / "us_theme_flow.json"
KST = ZoneInfo("Asia/Seoul")
MIN_MEMBERS = 3        # themes with fewer quoted members are noise
MIN_MOVE = 1.0         # % median move to call a theme strong / weak
MIN_BREADTH = 0.6      # share of quoted members moving that way (one outlier cannot carry a small theme)
TOP_UP, TOP_DOWN, TOP_STOCKS = 5, 2, 3
MAX_AGE_MIN = 30


def latest_map_path(root=ROOT):
    """The highest-version runtime/us_theme_map_vN.json (override: PRISM_US_THEME_MAP)."""
    if os.getenv("PRISM_US_THEME_MAP"):
        return Path(os.environ["PRISM_US_THEME_MAP"])
    found = []
    for path in (Path(root) / "runtime").glob("us_theme_map_v*.json"):
        match = re.fullmatch(r"us_theme_map_v(\d+)\.json", path.name)
        if match:
            found.append((int(match.group(1)), path))
    return max(found)[1] if found else None


def theme_stats(themes, changes):
    """Per theme: quoted members, mean % change, share rising, top movers. `changes` = {ticker: pct}."""
    out = []
    for theme in themes:
        quoted = [(m["code"], m.get("name") or m["code"], changes[m["code"]])
                  for m in theme["members"] if m["code"] in changes]
        if len(quoted) < MIN_MEMBERS:
            continue
        pcts = [p for _, _, p in quoted]
        out.append({"name": theme["name"], "sector": theme.get("sector"), "keywords": theme.get("keywords") or [],
                    "members": [m["code"] for m in theme["members"]], "n": len(quoted),
                    "median": round(median(pcts), 2), "mean": round(mean(pcts), 2), "up": sum(p > 0 for p in pcts),
                    "down": sum(p < 0 for p in pcts),
                    "movers": sorted(quoted, key=lambda q: -q[2])[:TOP_STOCKS]})
    return sorted(out, key=lambda s: -s["median"])


def attach_headlines(stats, conn, since, *, limit=1):
    """Newest US headline since `since` (KST 'YYYY-MM-DD HH:MM:SS') tagged with a member, else keyword hit."""
    from prism_core import kr_news_store as store
    for s in stats:
        rows = []
        for code in s["members"][:12]:
            rows += store.search(conn, ticker=code, since=since, limit=3)
        if not rows and s["keywords"]:
            rows = store.search(conn, keywords=s["keywords"][:6], since=since, limit=3)
        rows.sort(key=lambda r: r["published_at"], reverse=True)
        s["headlines"] = [{"title": r["title"], "provider": r["provider"], "at": r["published_at"]} for r in rows[:limit]]
    return stats


def build(theme_map, changes, *, trade_date, mode, conn=None, now=None):
    now = now or datetime.now(KST)
    stats = theme_stats(theme_map["themes"], changes)
    picked = [s for s in stats if s["median"] >= MIN_MOVE and s["up"] >= MIN_BREADTH * s["n"]][:TOP_UP] + \
             [s for s in reversed(stats) if s["median"] <= -MIN_MOVE and s["down"] >= MIN_BREADTH * s["n"]][:TOP_DOWN]
    if conn is not None:
        since = (now - timedelta(hours=18)).strftime("%Y-%m-%d %H:%M:%S")
        attach_headlines(picked, conn, since)
    return {"as_of": now.isoformat(timespec="seconds"), "trade_date": trade_date, "mode": mode,
            "map_version": theme_map.get("meta", {}).get("version"), "quoted": len(changes),
            "themes_scored": len(stats), "themes": picked}


def load_fresh(trade_date, *, path=FLOW_PATH, now=None, max_age_min=MAX_AGE_MIN):
    """The saved flow when it is for `trade_date` and at most `max_age_min` old, else None."""
    try:
        flow = json.loads(Path(path).read_text())
        age = (now or datetime.now(KST)) - datetime.fromisoformat(flow["as_of"])
    except (OSError, ValueError, KeyError):
        return None
    if flow.get("trade_date") != trade_date or not timedelta(0) <= age <= timedelta(minutes=max_age_min):
        return None
    return flow


_MARKDOWN = str.maketrans({c: "" for c in "*_`["})
HEADLINE_MAX = 42


def _stock(code, pct):
    return f"{code} {pct:+.1f}%"


def _headline(title):
    title = str(title).translate(_MARKDOWN).replace("]", "").strip()
    return title if len(title) <= HEADLINE_MAX else title[:HEADLINE_MAX].rstrip() + "…"


def render(flow, language="ko"):
    """Telegram (legacy Markdown) block: strong themes with members and one headline, then weak themes."""
    themes = (flow or {}).get("themes") or []
    if not themes:
        return ""
    up = [t for t in themes if t["median"] > 0]
    down = [t for t in themes if t["median"] < 0]
    ko = language == "ko"
    quoted = f"{flow.get('quoted', 0):,}"
    lines = ["🧭 *오늘 테마 흐름*" if ko else "🧭 *Theme flow today*",
             f"미국 {quoted}종목 · 전일 종가 대비 · 테마별 중앙값" if ko
             else f"US {quoted} names · vs previous close · theme median", ""]
    if up:
        lines.append("📈 *강한 테마*" if ko else "📈 *Strong themes*")
        for i, t in enumerate(up, 1):
            rise = f"{t['up']}/{t['n']} 상승" if ko else f"{t['up']}/{t['n']} up"
            lines.append(f"{i}. *{t['name']}* {t['median']:+.1f}%  ({rise})")
            lines.append("    " + " · ".join(_stock(c, p) for c, _, p in t["movers"]))
            for h in t.get("headlines") or []:
                lines.append(f"    📰 {_headline(h['title'])}")
        lines.append("")
    if down:
        lines.append("📉 *약한 테마*" if ko else "📉 *Weak themes*")
        lines += [f"· {t['name']} {t['median']:+.1f}%" for t in down]
        lines.append("")
    lines.append("※ 등락은 테마 종목의 중앙값이고, 기사 제목은 참고용이며 원인으로 단정하지 않습니다." if ko else
                 "※ Moves are theme medians; headlines are context, not a stated cause.")
    return "\n".join(lines) + "\n\n"
