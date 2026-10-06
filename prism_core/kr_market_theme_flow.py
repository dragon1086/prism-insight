"""'Today's theme flow' block for the KR report market section (roadmap R5).

Infostock posts co-move group lines, mostly at the open ("네오티스(085910)  +8.51%,
이수페타시스 +8.58%, ..."). Each line is named after the theme map theme its
members share most, lines of one theme are pooled, and the strongest rising and
falling themes are listed with the day's headlines that name the theme's
keywords or several of its stocks. Everything is deterministic: headlines are
shown as related titles, never as a confirmed cause. Report context only;
nothing here feeds screening or trading.
"""

import json
import logging
import re
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from prism_core import kr_news_store as store
from prism_core.kr_news_titles import _GENERIC_LIST

logger = logging.getLogger(__name__)

MAP_PATH = Path(__file__).resolve().parents[1] / "runtime" / "kr_theme_map_v1.json"
GROUP_LINE = re.compile(r"^(?P<lead>[^(]+)\([0-9A-Z]{6}\)\s+(?P<pct>[+-][0-9.]+)%,(?P<rest>.+)$")
MEMBER = re.compile(r"\s*(?P<name>[^,(]+?)\s+(?P<pct>[+-][0-9.]+)%")
# Titles that only announce a table/schedule or restate price and flow; they carry no reason.
EMPTY_TITLE = re.compile(r"테마별 등락률|테마동향|이슈&테마 스케줄|특징 테마|기술적 분석 특징주|테마시황$|장중수급포착|"
                         r"소폭 (?:상승|하락)세|(?:상|하)한가 진입|거래량 급증|신고가 경신|VI 발동|순매수…|순매도…")
# Words that mark a title as describing a group move ("광통신주, 장초반 줄상승").
MOVE_WORD = re.compile(r"테마|관련주|[가-힣A-Za-z]+주[,·\s↑]|株|강세|급등|동반|줄상승|들썩|↑")
MIN_OVERLAP = 2      # group members that must belong to a theme for the line to take its name
MAX_UP, MAX_DOWN = 5, 2
MAX_STOCKS = 4
MAX_TITLES = 2


def parse_line(title):
    """[(name, pct)] of one co-move line, leader first; [] for other headlines."""
    m = GROUP_LINE.match(title or "")
    if not m:
        return []
    members = [(m["lead"].strip(), float(m["pct"]))]
    for part in m["rest"].split(","):
        mm = MEMBER.match(part)
        if mm and mm["name"].strip() not in {n for n, _ in members}:
            members.append((mm["name"].strip(), float(mm["pct"])))
    return members


def load_map(path=MAP_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("themes") or []
    except (OSError, ValueError):
        return []


def name_line(members, themes):
    """Theme the line's stocks share most (at least MIN_OVERLAP of them), else None."""
    names = {n for n, _ in members}
    best, score = None, 0
    for theme in themes:
        overlap = len(names & {m.get("name") for m in theme.get("members", [])})
        if overlap > score or (overlap == score and best is not None and theme.get("lines", 0) > best.get("lines", 0)):
            best, score = theme, overlap
    return best if score >= MIN_OVERLAP else None


def _since(day):
    """News since the previous weekday's close; holidays only widen the window slightly."""
    back = 3 if day.weekday() == 0 else 1
    return (day - timedelta(days=back)).strftime("%Y-%m-%d 15:30:00")


def _word_start(keyword):
    # "강관" must not match inside "건강관리", nor "게임" inside "아시안게임".
    return re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(keyword))


def _related_titles(conn, keywords, names, since, until):
    """Up to MAX_TITLES headlines that name the theme or several of its stocks, best first."""
    rows = store.search(conn, keywords=list(keywords) + list(names), since=since, until=until, limit=300)
    patterns = [_word_start(k) for k in keywords]
    scored = []
    for r in rows:
        title = r["title"]
        if GROUP_LINE.match(title) or _GENERIC_LIST.search(title) or EMPTY_TITLE.search(title):
            continue
        keyword_hit = any(p.search(title) for p in patterns)
        named = sum(n in title for n in names)
        # A lone stock name is that company's own news; a theme reason names the theme or several members.
        if not (keyword_hit or named >= 2):
            continue
        score = 2 * keyword_hit + min(named, 3) + bool(MOVE_WORD.search(title))
        scored.append((score, r["published_at"], r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for score, _, r in scored if score >= 3][:MAX_TITLES]


def theme_flow(conn, themes, reference_date, *, now=None, language="ko"):
    """Rendered markdown block, or "" when the day has no co-move lines."""
    day = datetime.strptime(str(reference_date), "%Y%m%d")
    until = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    if until[:10] > day.strftime("%Y-%m-%d"):
        until = day.strftime("%Y-%m-%d 23:59:59")
    rows = conn.execute(
        "SELECT published_at, title FROM news_titles WHERE provider = '인포스탁' "
        "AND published_at BETWEEN ? AND ? ORDER BY published_at",
        (day.strftime("%Y-%m-%d 00:00:00"), until)).fetchall()
    pooled, first_at, last_at = {}, None, None
    for at, title in rows:
        members = parse_line(title)
        if len(members) < 3:
            continue
        first_at, last_at = first_at or at, at
        theme = name_line(members, themes)
        key = theme["name"] if theme else f"{members[0][0]} 등"
        entry = pooled.setdefault(key, {"theme": theme, "stocks": {}})
        for name, pct in members:
            entry["stocks"][name] = pct  # the latest line has the latest move
    if not pooled:
        return ""
    for entry in pooled.values():
        entry["avg"] = sum(entry["stocks"].values()) / len(entry["stocks"])
    ranked = sorted(pooled.items(), key=lambda kv: -kv[1]["avg"])
    up = [kv for kv in ranked if kv[1]["avg"] > 0][:MAX_UP]
    down = [kv for kv in reversed(ranked) if kv[1]["avg"] < 0][:MAX_DOWN]
    since = _since(day)

    ko = language == "ko"
    span = f"{first_at[11:16]}~{last_at[11:16]}"
    stamp = f"{day.month}월 {day.day}일 {span}" if ko else f"{day:%b %d} {span} KST"
    lines = [
        f"\n\n### {'오늘 테마 흐름' if ko else 'Theme flow today'}\n",
        (f"인포스탁 동반 등락 묶음({stamp}에 함께 움직인 종목)을 테마 지도로 묶은 자료입니다. "
         "관련 제목은 같은 날 나온 기사 제목이며, 원인으로 확인된 것은 아닙니다.\n" if ko else
         f"Infostock co-move groups ({stamp}) pooled by the theme map. "
         "Related titles are same-day Korean headlines, not confirmed causes.\n"),
    ]
    for header, block in (("오른 테마" if ko else "Rising themes", up), ("내린 테마" if ko else "Falling themes", down)):
        if not block:
            continue
        lines.append(f"**{header}**\n")
        for key, entry in block:
            stocks = sorted(entry["stocks"].items(), key=lambda kv: -abs(kv[1]))
            shown = ", ".join(f"{n} {p:+.1f}%" for n, p in stocks[:MAX_STOCKS])
            if len(stocks) > MAX_STOCKS:
                shown += f" {'외' if ko else '+'} {len(stocks) - MAX_STOCKS}{'종목' if ko else ' more'}"
            lines.append(f"- **{key}** ({'평균' if ko else 'avg'} {entry['avg']:+.1f}%): {shown}")
            theme = entry["theme"] or {}
            titles = _related_titles(conn, theme.get("keywords") or [], list(entry["stocks"]), since, until)
            for r in titles:
                lines.append(f"  - {'관련 제목' if ko else 'Related title'}: {r['published_at'][5:16]} "
                             f"{r['provider']} — {r['title']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_theme_flow(reference_date, language="ko"):
    """db-server path: local headline store and theme map; "" when either is unavailable."""
    themes = load_map()
    if not themes:  # without the map every line is "<leader> 등", which says little
        return ""
    try:
        with closing(store.connect(readonly=True)) as conn:
            block = theme_flow(conn, themes, reference_date, language=language)
    except Exception as exc:  # noqa: BLE001 - optional context for the market section
        logger.info("Theme flow unavailable: %s", type(exc).__name__)
        return ""
    logger.info("[THEME_FLOW] date=%s map_themes=%d chars=%d", reference_date, len(themes), len(block))
    return block
