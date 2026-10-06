"""KR theme map v1 from Infostock open co-move group headlines (roadmap R2).

Each Infostock line such as "원일티엔아이(136150)  +8.86%, 팬오션 +3.99%, 일승 +2.52%"
is one observation that a group of stocks moved together that morning. Lines
of the same theme recur with mostly the same core members, so themes are
built by clustering lines on member overlap. A stock may belong to several
themes. Everything here is deterministic; only naming uses a model (tool side).
"""

import re
from collections import Counter

GROUP_LINE = re.compile(r"^(?P<lead>[^(]+)\((?P<code>[0-9A-Z]{6})\)\s+(?P<pct>[+-][0-9.]+)%,(?P<rest>.+)$")
MEMBER = re.compile(r"\s*(?P<name>[^,(]+?)\s+(?P<pct>[+-][0-9.]+)%")

JOIN_JACCARD = 0.3     # a line joins the cluster whose core it overlaps most, at least this much
MERGE_JACCARD = 0.5    # clusters whose cores overlap this much are one theme
CORE_SHARE = 0.3       # a member must appear in this share of a theme's lines
MIN_LINES = 5          # themes seen fewer times are noise
MIN_MEMBER_TIMES = 3
RELATED_SHARE = 0.1    # related members: seen at least twice and in this share of lines
MAX_MEMBERS = 20       # per theme, like a theme board
MAX_NEWS_MEMBERS = 8


def parse_group(title, name_to_code):
    """Members [(code, pct)] of one co-move line; unknown names are skipped."""
    m = GROUP_LINE.match(title or "")
    if not m:
        return []
    members = [(m["code"], float(m["pct"]))]
    for part in m["rest"].split(","):
        mm = MEMBER.match(part)
        code = name_to_code.get(mm["name"].strip()) if mm else None
        if code and code not in {c for c, _ in members}:
            members.append((code, float(mm["pct"])))
    return members if len(members) >= 3 else []


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def _core(cluster):
    floor = max(1, len(cluster["lines"]) * CORE_SHARE)
    return {code for code, n in cluster["counts"].items() if n >= floor}


def _add(cluster, at, members):
    cluster["counts"].update({c for c, _ in members})
    cluster["lines"].append((at, members))
    cluster["core"] = _core(cluster)


def build_themes(observations):
    """observations: [(published_at, [(code, pct), ...])] in time order → theme dicts."""
    clusters = []
    for at, members in observations:
        codes = {c for c, _ in members}
        best, score = None, 0.0
        for cluster in clusters:
            j = _jaccard(codes, cluster["core"])
            if j > score:
                best, score = cluster, j
        if best is None or score < JOIN_JACCARD:
            best = {"counts": Counter(), "lines": [], "core": set()}
            clusters.append(best)
        _add(best, at, members)

    merged = True
    while merged:  # fold near-duplicate themes together, several per pass
        merged, kept = False, []
        for cluster in clusters:
            target = next((k for k in kept if _jaccard(k["core"], cluster["core"]) >= MERGE_JACCARD), None)
            if target is None:
                kept.append(cluster)
                continue
            target["counts"].update(cluster["counts"])
            target["lines"] = sorted(target["lines"] + cluster["lines"], key=lambda x: x[0])
            target["core"] = _core(target)
            merged = True
        clusters = kept

    themes = []
    for cluster in clusters:
        lines = cluster["lines"]
        if len(lines) < MIN_LINES:
            continue
        members = _members(cluster["counts"], len(lines))
        if sum(m["role"] == "core" for m in members) < 2:
            continue
        days = {}
        for at, ms in lines:
            avg = sum(p for _, p in ms) / len(ms)
            days.setdefault(at[:10], []).append(avg)
        active = [{"date": d, "avg_pct": round(sum(v) / len(v), 2)} for d, v in sorted(days.items())]
        themes.append({"members": members, "counts": dict(cluster["counts"]), "lines": len(lines),
                       "active_days": active, "first_seen": active[0]["date"], "last_seen": active[-1]["date"]})
    themes.sort(key=lambda t: -t["lines"])
    for i, theme in enumerate(themes, 1):
        theme["id"] = f"T{i:03d}"
    return themes


def _members(counts, n_lines):
    """Core (>= 30% of lines, >= 3 times) then related (>= 2 times, >= 10%), at most MAX_MEMBERS."""
    core_floor = max(MIN_MEMBER_TIMES, n_lines * CORE_SHARE)
    related_floor = max(2, n_lines * RELATED_SHARE)
    out = []
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        role = "core" if n >= core_floor else "related" if n >= related_floor else None
        if role:
            out.append({"code": code, "times": n, "share": round(n / n_lines, 2), "role": role})
    return out[:MAX_MEMBERS]


def merge_by_name(themes):
    """Themes the namer gave the same (sector, name) are one theme; ids are reassigned."""
    groups = {}
    for t in themes:
        key = (t.get("sector", ""), t.get("name", "")) if t.get("name") not in (None, "", "미분류") else ("", t["id"])
        groups.setdefault(key, []).append(t)
    merged = []
    for (_, _), parts in groups.items():
        if len(parts) == 1:
            merged.append(parts[0])
            continue
        counts, days = Counter(), {}
        for t in parts:
            counts.update(t["counts"])
            for d in t["active_days"]:
                days.setdefault(d["date"], []).append(d["avg_pct"])
        lines = sum(t["lines"] for t in parts)
        active = [{"date": d, "avg_pct": round(sum(v) / len(v), 2)} for d, v in sorted(days.items())]
        first = dict(parts[0])
        first.update(members=_members(counts, lines), counts=dict(counts), lines=lines, active_days=active,
                     first_seen=active[0]["date"], last_seen=active[-1]["date"],
                     merged_from=[t["id"] for t in parts])
        merged.append(first)
    merged.sort(key=lambda t: -t["lines"])
    for i, theme in enumerate(merged, 1):
        theme["id"] = f"T{i:03d}"
    return merged


# Market roundups name many stocks without a shared theme ("외국인 순매수 상위 …").
ROUNDUP = re.compile(r"순매수|순매도|상위|하위|외국인|기관|코스피|코스닥|마감|시황|증시|급등락주")
SIMILAR_OVERLAP = 0.5   # same-sector themes sharing half of the smaller one's stocks are one theme


def merge_similar(themes):
    """Fold same-sector themes whose stock sets overlap heavily (e.g. 타이어주 / 타이어·타이어소재)."""
    themes = sorted(themes, key=lambda t: -t["lines"])
    kept = []
    for t in themes:
        codes = {m["code"] for m in t["members"]}
        target = None
        for k in kept:
            other = {m["code"] for m in k["members"]}
            small = min(len(codes), len(other))
            if k.get("sector") == t.get("sector") and small and len(codes & other) / small >= SIMILAR_OVERLAP:
                target = k
                break
        if target is None:
            kept.append(t)
            continue
        counts = Counter(target["counts"])
        counts.update(t["counts"])
        days = {d["date"]: [d["avg_pct"]] for d in target["active_days"]}
        for d in t["active_days"]:
            days.setdefault(d["date"], []).append(d["avg_pct"])
        lines = target["lines"] + t["lines"]
        active = [{"date": d, "avg_pct": round(sum(v) / len(v), 2)} for d, v in sorted(days.items())]
        target.update(members=_members(counts, lines), counts=dict(counts), lines=lines, active_days=active,
                      first_seen=active[0]["date"], last_seen=active[-1]["date"],
                      merged_from=target.get("merged_from", [target["id"]]) + t.get("merged_from", [t["id"]]))
    for i, theme in enumerate(kept, 1):
        theme["id"] = f"T{i:03d}"
    return kept


def stock_name_pattern(name_to_code, min_len=3):
    """One regex over listed names (longest first, >= min_len chars to avoid short-name noise)."""
    names = sorted((n for n in name_to_code if len(n) >= min_len), key=len, reverse=True)
    return re.compile("|".join(re.escape(n) for n in names)) if names else None


def core_sectors(themes):
    """Sectors where each stock is a core member; used to keep incidental co-mentions out."""
    sectors = {}
    for t in themes:
        for m in t["members"]:
            if m["role"] == "core":
                sectors.setdefault(m["code"], set()).add(t.get("sector", ""))
    return sectors


def add_news_members(theme, titles, name_to_code, pattern, min_mentions=3, max_names=3, home_sectors=None):
    """Stocks named alongside the theme's own members in its keyword headlines become 'news' members.

    A headline counts only if it names at least one existing member, which ties
    the keyword (e.g. 로봇) to this sub-theme rather than any robot story.
    """
    have = {m["code"] for m in theme["members"]}
    mentions = Counter()
    for title in titles:
        found = {name_to_code[n] for n in set(pattern.findall(title))} if pattern else set()
        # Market roundups ("순매수 상위: A, B, C, D…") name many stocks and no shared theme.
        if found & have and len(found) <= max_names and not ROUNDUP.search(title):
            mentions.update(found - have)
    home_sectors = home_sectors or {}

    def belongs_elsewhere(code):
        # A stock that is core only in other sectors (삼성전자 in 반도체) is an incidental mention here.
        homes = home_sectors.get(code)
        return bool(homes) and theme.get("sector") not in homes

    added = [{"code": code, "times": n, "share": None, "role": "news"}
             for code, n in mentions.most_common() if n >= min_mentions and not belongs_elsewhere(code)][:MAX_NEWS_MEMBERS]
    theme["members"] = theme["members"] + added[:max(0, MAX_MEMBERS + MAX_NEWS_MEMBERS - len(theme["members"]))]
    return added


def add_ai_members(themes, assignments, new_themes, name_to_code, sectors):
    """Apply the model's placement of uncovered large caps; every addition is marked role 'ai'."""
    by_id = {t["id"]: t for t in themes}
    added = 0
    for stock, theme_ids in assignments.items():
        code = name_to_code.get(str(stock).strip())
        if not code:
            continue
        for tid in [i for i in theme_ids if i in by_id][:2]:
            theme = by_id[tid]
            if code not in {m["code"] for m in theme["members"]}:
                theme["members"].append({"code": code, "times": 0, "share": None, "role": "ai"})
                added += 1
    created = []
    for item in new_themes:
        codes = list(dict.fromkeys(name_to_code[n] for n in item.get("stocks", []) if n in name_to_code))
        name = str(item.get("name", "")).strip()
        if len(codes) < 3 or not (0 < len(name) <= 14):
            continue
        created.append({"name": name, "sector": item.get("sector") if item.get("sector") in sectors else "기타",
                        "keywords": [], "lines": 0, "active_days": [], "first_seen": None, "last_seen": None,
                        "source": "ai", "counts": {},
                        "members": [{"code": c, "times": 0, "share": None, "role": "ai"} for c in codes[:MAX_MEMBERS]]})
    existing = {t["name"]: t for t in themes}
    fresh = []
    for theme in created:
        same = existing.get(theme["name"])
        if same is None:
            fresh.append(theme)
            continue
        have = {m["code"] for m in same["members"]}
        for m in theme["members"]:
            if m["code"] not in have:
                same["members"].append(m)
                added += 1
    for i, theme in enumerate(fresh, len(themes) + 1):
        theme["id"] = f"T{i:03d}"
    return added, fresh


def coverage(themes, cap_by_code, top=500):
    """Share of the top market-cap stocks that belong to any theme, and the largest gaps."""
    covered = {m["code"] for t in themes for m in t["members"]}
    ranked = [c for c, _ in sorted(cap_by_code.items(), key=lambda kv: -kv[1])][:top]
    missing = [c for c in ranked if c not in covered]
    return {"top": len(ranked), "covered": len(ranked) - len(missing), "missing": missing}


def theme_of(themes, code):
    """Themes a stock belongs to, strongest membership first."""
    hits = [(m["share"] or 0, t) for t in themes for m in t["members"] if m["code"] == code]
    return [t for _, t in sorted(hits, key=lambda x: -x[0])]
