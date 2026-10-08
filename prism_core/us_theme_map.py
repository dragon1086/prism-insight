"""US theme map v1 from daily price co-movement (roadmap U3).

The US has no Infostock-style "moved together" headlines, so the raw groups come
from prices: one year of daily returns, the S&P 500 (SPY) component removed by a
per-stock regression, and average-linkage clustering of the residual-return
correlation. The output follows the KR map shape (`runtime/kr_theme_map_v1.json`)
so readers can be shared: a theme's `lines` are the days its members moved
together beyond the market, `active_days` those days with the members' average
raw return, and a member's `share` the fraction of those days it moved with them.

Descriptive reference data only: nothing here feeds screening, scores, gates or
trading. Everything is deterministic; naming and placement use a model (tool side).
"""

import re
from collections import Counter

import numpy as np

CLUSTER_CORR = 0.45      # average residual correlation that holds a cluster together
SPLIT_STEP = 0.05        # oversized clusters are re-cut this much tighter
MIN_CLUSTER, MAX_CLUSTER = 2, 25
MIN_OBS = 60             # trading days a stock needs before it is clustered
MOVE_Z = 2.0             # a co-move day: cluster mean residual beyond this many std
MOVE_AGREE = 2 / 3       # ... with at least this share of members on the same side
MAX_THEMES_PER_STOCK = 3
ROLE_RANK = {"core": 0, "related": 1, "override": 1, "ai": 2, "news": 3}

# US-feed titles that list many stocks or restate ratings without a shared theme.
ROUNDUP = re.compile(r"뉴욕증시|뉴욕마켓|마켓 브리핑|마켓워치|장중시황|주요 종목|투자의견|목표주가|목표가|"
                     r"순매수|순매도|상위|하위|시황|증시|마감|\[ETF")

REIT_INDUSTRY = re.compile(r"^REIT\b", re.IGNORECASE)
# Generic first words of Korean company names that must not stand for one company.
GENERIC_FIRST = {"아메리칸", "아메리카", "유나이티드", "제너럴", "인터내셔널", "내셔널", "퍼스트", "글로벌", "뱅크",
                 "브리티시", "로열", "로얄", "사우스", "노스", "웨스트", "이스트", "웨스턴", "이스턴", "서던", "노던",
                 "뉴욕", "캐나디안", "재팬", "차이나", "코리아", "도이치", "트루", "뉴", "더", "스탠다드", "컨티넨탈",
                 "퍼시픽", "애틀랜틱", "디지털", "다이내믹", "에어", "에너지", "파이낸셜", "캐피털", "캐피탈", "홈",
                 "블루", "그린", "레드", "골든", "실버", "스타", "유니버설", "인터랙티브", "어플라이드", "어드밴스드",
                 "얼라이드", "콘솔리데이티드", "메디컬", "헬스", "텍사스", "플로리다", "버지니아", "시티", "피플스"}


# ---------------------------------------------------------------- universe

def company_key(name):
    """Share classes of one company (GOOG/GOOGL, FOX/FOXA) normalize to one key."""
    key = re.sub(r"\b(class\s+[a-c]|series\s+[a-c]|common stock|ordinary shares?|inc|corp(oration)?|co|plc|"
                 r"ltd|n\.?v|s\.?a|holdings?|company|group)\b", " ", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", key).strip()


def select_universe(candidates, top=500, eligible=None):
    """Rank candidates by market cap and keep the first `top` operating common stocks.

    candidates: [{"symbol", "cap", "info": yfinance-style dict, "name_verified": bool}].
    eligible(info, name_verified) -> exclusion reason or None (the screening's own check).
    REITs are left out like KR left out 리츠; a second share class of the same company
    is folded into the larger one. Returns (kept, excluded Counter, {alias: kept symbol}).
    """
    kept, excluded, aliases, by_company = [], Counter(), {}, {}
    for cand in sorted(candidates, key=lambda c: -(c.get("cap") or 0)):
        if len(kept) >= top:
            break
        info = cand.get("info") or {}
        reason = eligible(info, cand.get("name_verified", True)) if eligible else None
        if reason is None and REIT_INDUSTRY.search(str(info.get("industry") or "")):
            reason = "reit"
        if reason:
            excluded[reason] += 1
            continue
        key = company_key(info.get("longName") or info.get("shortName") or cand["symbol"])
        if key and key in by_company:
            aliases[cand["symbol"]] = by_company[key]
            excluded["share_class"] += 1
            continue
        by_company[key] = cand["symbol"]
        kept.append(cand)
    return kept, excluded, aliases


# ---------------------------------------------------------------- prices → clusters

def residual_returns(returns, market):
    """Remove the market from each stock: residuals of an OLS of its returns on the market's.

    returns: T x N (NaN where a stock did not trade); market: T. Columns with fewer
    than MIN_OBS shared days come back all-NaN.
    """
    rets = np.asarray(returns, dtype=float)
    mkt = np.asarray(market, dtype=float)
    out = np.full_like(rets, np.nan)
    for j in range(rets.shape[1]):
        ok = np.isfinite(rets[:, j]) & np.isfinite(mkt)
        if ok.sum() < MIN_OBS:
            continue
        x = np.column_stack([np.ones(ok.sum()), mkt[ok]])
        coef, *_ = np.linalg.lstsq(x, rets[ok, j], rcond=None)
        out[ok, j] = rets[ok, j] - x @ coef
    return out


def residual_corr(resid):
    """Correlation of residual returns; a missing day counts as no idiosyncratic move (0)."""
    z = np.nan_to_num(np.asarray(resid, dtype=float))
    z = z - z.mean(axis=0)
    sd = z.std(axis=0)
    sd[sd == 0] = np.inf
    z = z / sd
    corr = np.clip((z.T @ z) / z.shape[0], -1.0, 1.0)
    np.fill_diagonal(corr, 1.0)
    return corr


def cluster_indices(corr, threshold=CLUSTER_CORR, max_size=MAX_CLUSTER, min_size=MIN_CLUSTER):
    """Average-linkage clusters held together at `threshold` residual correlation.

    A cluster larger than max_size is re-cut SPLIT_STEP tighter until it fits; its
    leftovers are dropped (the tool places them later). Singletons are not clusters.
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    corr = np.asarray(corr, dtype=float)

    def cut(idx, thr):
        if len(idx) < min_size:
            return []
        sub = corr[np.ix_(idx, idx)]
        dist = 1.0 - (sub + sub.T) / 2
        np.fill_diagonal(dist, 0.0)
        labels = fcluster(linkage(squareform(np.clip(dist, 0, 2), checks=False), method="average"),
                          t=1.0 - thr, criterion="distance")
        out = []
        for label in sorted(set(labels)):
            members = [idx[i] for i in np.flatnonzero(labels == label)]
            if len(members) > max_size:
                if thr + SPLIT_STEP < 1.0:
                    out += cut(members, round(thr + SPLIT_STEP, 4))
            elif len(members) >= min_size:
                out.append(members)
        return out

    return cut(list(range(corr.shape[0])), threshold)


def size_profile(clusters, n_stocks):
    """Cluster count, size spread and how many stocks sit in a cluster (threshold sweep)."""
    sizes = sorted((len(c) for c in clusters), reverse=True)
    return {"clusters": len(sizes), "in_clusters": sum(sizes), "share": round(sum(sizes) / max(1, n_stocks), 3),
            "largest": sizes[:5], "pairs": sum(s == 2 for s in sizes), "median": sizes[len(sizes) // 2] if sizes else 0}


def cluster_stats(corr, idx):
    """Mean pairwise residual correlation, and each member's mean correlation to the others."""
    sub = np.asarray(corr, dtype=float)[np.ix_(idx, idx)]
    n = len(idx)
    if n < 2:
        return 0.0, [0.0] * n
    mean = (sub.sum() - np.trace(sub)) / (n * (n - 1))
    member = [(sub[i].sum() - sub[i, i]) / (n - 1) for i in range(n)]
    return round(float(mean), 3), [round(float(m), 3) for m in member]


def co_move_days(resid, raw, idx, dates, z=MOVE_Z, agree=MOVE_AGREE):
    """Days the cluster moved together beyond the market.

    A day counts when the members' mean residual is at least z standard deviations
    from zero and at least `agree` of the members that traded are on that side.
    Returns ([(date, members' mean raw return in %)], Counter{column: same-side days}).
    """
    res = np.asarray(resid, dtype=float)[:, idx]
    valid = np.isfinite(res)
    counts_valid = valid.sum(axis=1)
    mean = np.where(counts_valid > 0, np.nansum(res, axis=1) / np.maximum(counts_valid, 1), np.nan)
    sd = np.nanstd(mean)
    days, counts = [], Counter()
    if not np.isfinite(sd) or sd == 0:
        return days, counts
    rawsub = np.asarray(raw, dtype=float)[:, idx]
    for t, day in enumerate(dates):
        if counts_valid[t] < 2 or not np.isfinite(mean[t]) or abs(mean[t]) < z * sd:
            continue
        same = valid[t] & (np.sign(np.nan_to_num(res[t])) == np.sign(mean[t]))
        if same.sum() < agree * counts_valid[t]:
            continue
        days.append((str(day)[:10], round(float(np.nanmean(rawsub[t])) * 100, 2)))
        for k in np.flatnonzero(same):
            counts[idx[k]] += 1
    return days, counts


def cluster_theme(symbols, idx, corr, resid, raw, dates):
    """One price cluster as a theme in the KR map shape, members ordered by fit."""
    mean_corr, member_corr = cluster_stats(corr, idx)
    days, counts = co_move_days(resid, raw, idx, dates)
    lines = len(days)
    members = []
    for k in sorted(range(len(idx)), key=lambda k: -member_corr[k]):
        n = counts.get(idx[k], 0)
        members.append({"code": symbols[idx[k]], "times": n, "share": round(n / lines, 2) if lines else 0.0,
                        "role": "core", "corr": member_corr[k]})
    active = [{"date": d, "avg_pct": p} for d, p in days]
    return {"members": members, "lines": lines, "active_days": active,
            "first_seen": active[0]["date"] if active else None, "last_seen": active[-1]["date"] if active else None,
            "mean_corr": mean_corr, "source": "price"}


def build_clusters(symbols, returns, market, dates, threshold=CLUSTER_CORR):
    """symbols (N), returns T x N, market T → (themes ordered by mean corr with ids C001…, corr matrix)."""
    resid = residual_returns(returns, market)
    usable = [j for j in range(len(symbols)) if np.isfinite(resid[:, j]).any()]
    corr = np.zeros((len(symbols), len(symbols)))
    if usable:
        corr[np.ix_(usable, usable)] = residual_corr(resid[:, usable])
    clusters = [[usable[i] for i in c] for c in cluster_indices(corr[np.ix_(usable, usable)], threshold)]
    themes = [cluster_theme(symbols, c, corr, resid, returns, dates) for c in clusters]
    themes.sort(key=lambda t: (-t["mean_corr"], -len(t["members"])))
    for i, theme in enumerate(themes, 1):
        theme["id"] = f"C{i:03d}"
    return themes, corr


def pair_corr_lookup(symbols, corr):
    pos = {s: i for i, s in enumerate(symbols)}
    return lambda a, b: float(corr[pos[a], pos[b]]) if a in pos and b in pos else None


def recompute_fit(theme, pair_corr):
    """Mean pairwise and per-member residual correlation over the theme's price members."""
    codes = [m["code"] for m in theme["members"] if m["role"] == "core"]
    for m in theme["members"]:
        others = [pair_corr(m["code"], c) for c in codes if c != m["code"]]
        others = [x for x in others if x is not None]
        m["corr"] = round(sum(others) / len(others), 3) if others else None
    pairs = [pair_corr(a, b) for i, a in enumerate(codes) for b in codes[i + 1:]]
    pairs = [x for x in pairs if x is not None]
    theme["mean_corr"] = round(sum(pairs) / len(pairs), 3) if pairs else None


def merge_same_name(themes, pair_corr):
    """Clusters the namer gave the same (sector, name) are one theme; ids are reassigned T001…."""
    groups = {}
    for t in themes:
        key = (t.get("sector", ""), t.get("name")) if t.get("name") not in (None, "", "미분류") else ("", t["id"])
        groups.setdefault(key, []).append(t)
    merged = []
    for parts in groups.values():
        first = dict(parts[0])
        if len(parts) > 1:
            members, days = {}, {}
            for t in parts:
                for m in t["members"]:
                    members.setdefault(m["code"], dict(m))
                for d in t["active_days"]:
                    days.setdefault(d["date"], []).append(d["avg_pct"])
            active = [{"date": d, "avg_pct": round(sum(v) / len(v), 2)} for d, v in sorted(days.items())]
            first.update(members=list(members.values()), lines=len(active), active_days=active,
                         first_seen=active[0]["date"] if active else None,
                         last_seen=active[-1]["date"] if active else None,
                         merged_from=[t["id"] for t in parts], keywords=list(dict.fromkeys(
                             k for t in parts for k in t.get("keywords", [])))[:6])
            recompute_fit(first, pair_corr)
            first["members"].sort(key=lambda m: -(m.get("corr") or 0))
        merged.append(first)
    merged.sort(key=lambda t: (-(t.get("mean_corr") or 0), -len(t["members"])))
    for i, theme in enumerate(merged, 1):
        theme["id"] = f"T{i:03d}"
    return merged


# ---------------------------------------------------------------- names and headlines

def korean_aliases(name):
    """How Korean headlines write a US company: the KIS name without (ADR)/class marks,
    plus its first word when that word is distinctive ("마이크론 테크놀로지" → "마이크론")."""
    base = re.sub(r"\([^)]*\)", " ", str(name or ""))
    base = re.sub(r"\s+(?:클래스\s*)?[A-C]$", "", re.sub(r"\s+", " ", base).strip()).strip()
    out = [base] if len(base) >= 2 else []
    words = base.split(" ")
    if len(words) > 1 and len(words[0]) >= 3 and words[0] not in GENERIC_FIRST:
        out.append(words[0])
    return out


def alias_map(kr_names):
    """{alias: symbol} over {symbol: KIS Korean name}; aliases shared by two companies are dropped."""
    owners = {}
    for symbol, name in kr_names.items():
        for alias in korean_aliases(name):
            owners.setdefault(alias, set()).add(symbol)
    return {alias: next(iter(s)) for alias, s in owners.items() if len(s) == 1}


def name_pattern(aliases):
    """Whole-word alias regex: "애플" must not match inside "파인애플", "AMD" not inside "AMDX"."""
    names = sorted(aliases, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<![가-힣A-Za-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])")


def keyword_pattern(keyword):
    kw = str(keyword).strip()
    flags = re.IGNORECASE if kw.isascii() else 0
    tail = r"(?![A-Za-z0-9])" if kw[-1:].isascii() and kw[-1:].isalnum() else ""
    return re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(kw) + tail, flags)


def mentioned(row, aliases, pattern):
    """Symbols a headline names: KIS tags plus aliases found in the title."""
    found = set(row.get("tags") or ())
    if pattern:
        found |= {aliases[a] for a in pattern.findall(row["title"]) if a in aliases}
    return found


def theme_evidence(theme, rows, aliases, pattern, limit=3):
    """Headline evidence for one theme.

    co_mention: titles naming at least two members (tags or names). keyword: titles
    with a theme keyword that also name a member. keyword_only: keyword titles naming
    no member (weakest; samples only when nothing better exists). Market roundups and
    rating lists are skipped. Samples are newest first.
    """
    have = {m["code"] for m in theme["members"]}
    kws = [keyword_pattern(k) for k in theme.get("keywords", []) if str(k).strip()]
    co, kw, kw_only = [], [], []
    for row in rows:
        title = row["title"]
        if ROUNDUP.search(title):
            continue
        hits = mentioned(row, aliases, pattern) & have
        if len(hits) >= 2:
            co.append(title)
        elif kws and any(p.search(title) for p in kws):
            (kw if hits else kw_only).append(title)
    samples = list(dict.fromkeys(co + kw + kw_only))[:limit]
    return {"co_mention": len(co), "keyword": len(kw), "keyword_only": len(kw_only), "samples": samples}


def tagged_titles(rows, canonical):
    """Titles with their tagged companies' Korean names appended, so a name-only matcher
    (kr_theme_map.add_news_members) also sees the KIS tag."""
    out = []
    for row in rows:
        names = [canonical[s] for s in row.get("tags") or () if s in canonical]
        out.append(row["title"] + (" | " + " ".join(names) if names else ""))
    return out


def _is_override(member):
    return member["role"] == "override" or bool(member.get("override"))


def cap_memberships(themes, limit=MAX_THEMES_PER_STOCK):
    """A stock keeps at most `limit` themes: reviewed overrides first (never dropped), then
    price clusters, then AI, then news members."""
    seen = []
    for t in themes:
        for m in t["members"]:
            rank = -1 if _is_override(m) else ROLE_RANK.get(m["role"], 9)
            seen.append((rank, -(m.get("corr") or 0), t["id"], m["code"]))
    keep, per_stock = set(), Counter()
    for rank, _, tid, code in sorted(seen):
        if rank < 0 or per_stock[code] < limit:
            per_stock[code] += 1
            keep.add((tid, code))
    dropped = 0
    for t in themes:
        before = len(t["members"])
        t["members"] = [m for m in t["members"] if (t["id"], m["code"]) in keep]
        dropped += before - len(t["members"])
    return dropped


# ---------------------------------------------------------------- reviewed corrections

def _anchor_theme(themes, ticker, taken):
    """The built theme holding `ticker`, its strongest membership first (core, then corr)."""
    hits = [(ROLE_RANK.get(m["role"], 9), -(m.get("corr") or 0), i)
            for i, t in enumerate(themes) if id(t) not in taken for m in t["members"] if m["code"] == ticker]
    return themes[min(hits)[2]] if hits else None


def resolve_targets(themes, targets):
    """Map each reviewed target name to a built theme (anchor ticker, else exact name) or a new one.

    An anchor-resolved theme takes the reviewed name (and sector/keywords when given), so
    the correction survives name drift between builds. Returns ({target: theme}, {how: [names]}).
    """
    resolved, how, taken = {}, {"anchor": [], "name": [], "created": []}, set()
    by_name = {t.get("name"): t for t in themes}
    next_id = 1 + max((int(t["id"][1:]) for t in themes if str(t.get("id", ""))[1:].isdigit()), default=0)
    for name, spec in targets.items():
        spec = spec if isinstance(spec, dict) else {}
        theme = _anchor_theme(themes, spec["anchor"], taken) if spec.get("anchor") else None
        if theme is not None:
            how["anchor"].append(name)
            if theme.get("name") != name:
                theme["renamed_from"] = theme.get("name")
                theme["name"] = name
        elif by_name.get(name) is not None and id(by_name[name]) not in taken:
            theme = by_name[name]
            how["name"].append(name)
        else:
            theme = {"id": f"T{next_id:03d}", "name": name, "sector": spec.get("sector") or "기타",
                     "keywords": list(spec.get("keywords") or []), "lines": 0, "active_days": [],
                     "first_seen": None, "last_seen": None, "mean_corr": None, "source": "override", "members": []}
            next_id += 1
            themes.append(theme)
            how["created"].append(name)
        if spec.get("sector"):
            theme["sector"] = spec["sector"]
        if spec.get("keywords"):
            theme["keywords"] = list(dict.fromkeys(list(spec["keywords"]) + list(theme.get("keywords") or [])))
        taken.add(id(theme))
        resolved[name] = theme
    return resolved, how


def apply_overrides(themes, overrides, universe, limit=MAX_THEMES_PER_STOCK):
    """Apply reviewed corrections as the last build step; returns (themes, report).

    assign: the ticker's memberships become exactly the listed targets. add: the targets
    are added (an existing membership is kept and flagged). Overridden memberships survive
    the per-stock cap; automatic ones are dropped instead. Tickers outside the universe and
    unknown targets are reported and skipped. Themes left empty are removed.
    """
    resolved, how = resolve_targets(themes, overrides.get("themes") or {})
    universe = set(universe)
    report = {"version": overrides.get("version"), "targets": how, "assigned": 0, "added": 0,
              "skipped_tickers": [], "unknown_targets": []}

    def place(theme, ticker):
        member = next((m for m in theme["members"] if m["code"] == ticker), None)
        if member is None:
            theme["members"].append({"code": ticker, "times": 0, "share": None, "role": "override"})
        else:
            member["override"] = True

    for kind in ("assign", "add"):
        for ticker, names in (overrides.get(kind) or {}).items():
            if ticker not in universe:
                report["skipped_tickers"].append(ticker)
                continue
            known = [n for n in names if n in resolved]
            report["unknown_targets"] += [n for n in names if n not in resolved]
            if not known:
                continue
            if kind == "assign":
                for t in themes:
                    t["members"] = [m for m in t["members"] if m["code"] != ticker]
            for name in known:
                place(resolved[name], ticker)
                report["assigned" if kind == "assign" else "added"] += 1
    report["capped"] = cap_memberships(themes, limit)
    report["emptied"] = [t.get("name") for t in themes if not t["members"]]
    report["skipped_tickers"] = sorted(set(report["skipped_tickers"]))
    report["unknown_targets"] = sorted(set(report["unknown_targets"]))
    return [t for t in themes if t["members"]], report


def membership_counts(themes):
    return Counter(m["code"] for t in themes for m in t["members"])
