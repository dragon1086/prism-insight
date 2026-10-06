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
        floor = max(MIN_MEMBER_TIMES, len(lines) * CORE_SHARE)
        members = [{"code": code, "times": n, "share": round(n / len(lines), 2)}
                   for code, n in cluster["counts"].most_common() if n >= floor]
        if len(members) < 2:
            continue
        days = {}
        for at, ms in lines:
            avg = sum(p for _, p in ms) / len(ms)
            days.setdefault(at[:10], []).append(avg)
        active = [{"date": d, "avg_pct": round(sum(v) / len(v), 2)} for d, v in sorted(days.items())]
        themes.append({"members": members, "lines": len(lines), "active_days": active,
                       "first_seen": active[0]["date"], "last_seen": active[-1]["date"]})
    themes.sort(key=lambda t: -t["lines"])
    for i, theme in enumerate(themes, 1):
        theme["id"] = f"T{i:03d}"
    return themes


FAMILY_OVERLAP = 0.5   # share of the smaller theme's members that must be shared


def assign_families(themes):
    """Group fine themes into theme families; returns {family_id: [theme, ...]}.

    Two themes join when half of the smaller one's members are shared, or when
    they carry the same name (after naming). Names "" and "미분류" never join by
    name. Each theme gets family_id and family_name (most-observed member name).
    """
    parent = list(range(len(themes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sets = [{m["code"] for m in t["members"]} for t in themes]
    for i in range(len(themes)):
        for j in range(i + 1, len(themes)):
            small = min(len(sets[i]), len(sets[j]))
            if small and len(sets[i] & sets[j]) / small >= FAMILY_OVERLAP:
                parent[find(j)] = find(i)
    by_name = {}
    for i, t in enumerate(themes):
        name = t.get("name") or ""
        if name and name != "미분류":
            if name in by_name:
                parent[find(i)] = find(by_name[name])
            else:
                by_name[name] = i
    groups = {}
    for i in range(len(themes)):
        groups.setdefault(find(i), []).append(themes[i])
    families = {}
    for n, members in enumerate(sorted(groups.values(), key=lambda g: -sum(t["lines"] for t in g)), 1):
        weights = Counter()
        for t in members:
            if t.get("name") and t["name"] != "미분류":
                weights[t["name"]] += t["lines"]
        family_id = f"F{n:03d}"
        family_name = weights.most_common(1)[0][0] if weights else "미분류"
        for t in members:
            t["family_id"], t["family_name"] = family_id, family_name
        families[family_id] = members
    return families


def theme_of(themes, code):
    """Themes a stock belongs to, strongest membership first."""
    hits = [(m["share"], t) for t in themes for m in t["members"] if m["code"] == code]
    return [t for _, t in sorted(hits, key=lambda x: -x[0])]
