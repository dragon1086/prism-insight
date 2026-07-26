#!/usr/bin/env python3
"""
Firecrawl Client Module

Singleton FirecrawlApp instance with helper functions for search and agent calls.
API key is loaded from FIRECRAWL_API_KEY env var or mcp_agent.config.yaml fallback.
"""
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Singleton instance
_firecrawl_app = None

def _get_api_key() -> str:
    """Resolve Firecrawl API key from environment or mcp_agent.config.yaml."""
    key = os.getenv("FIRECRAWL_API_KEY")
    if key:
        return key

    # Fallback: read from mcp_agent.config.yaml
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "mcp_agent.config.yaml")
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        key = config.get("mcp", {}).get("servers", {}).get("firecrawl", {}).get("env", {}).get("FIRECRAWL_API_KEY")
        if key:
            logger.info("FIRECRAWL_API_KEY loaded from mcp_agent.config.yaml")
            return key
    except Exception as e:
        logger.warning(f"Failed to read mcp_agent.config.yaml: {e}")

    raise ValueError("FIRECRAWL_API_KEY not found in environment or mcp_agent.config.yaml")


def get_firecrawl_app():
    """Return singleton FirecrawlApp instance."""
    global _firecrawl_app
    if _firecrawl_app is None:
        from firecrawl import FirecrawlApp
        _firecrawl_app = FirecrawlApp(api_key=_get_api_key())
        logger.info("FirecrawlApp singleton initialized")
    return _firecrawl_app


_UNSUPPORTED_KWARG = re.compile(r"unexpected keyword argument '([^']+)'")


def _build_scrape_options():
    """
    ScrapeOptions moved around between firecrawl-py releases. Try the known
    locations so full article bodies still get scraped on older installs.
    Returns None when the SDK offers no equivalent.
    """
    for module, name in (
        ("firecrawl", "ScrapeOptions"),
        ("firecrawl.v2.types", "ScrapeOptions"),
        ("firecrawl.types", "ScrapeOptions"),
    ):
        try:
            mod = __import__(module, fromlist=[name])
            cls = getattr(mod, name)
            # only_main_content strips nav/menu/footer boilerplate — without it
            # the first chars of an article are usually junk.
            return cls(formats=["markdown"], only_main_content=True)
        except Exception:  # noqa: BLE001 - probing optional SDK surface
            continue
    logger.warning("ScrapeOptions unavailable in this firecrawl-py; bodies will not be scraped")
    return None


def _search_dropping_unsupported(app, query: str, limit: int, extra: dict):
    """
    Call app.search, dropping only the kwargs this SDK version rejects.

    Older firecrawl-py builds reject individual options (e.g. include_domains).
    Dropping the whole option set on the first TypeError would also throw away
    recency and source filters, which silently degrades result quality — so
    remove one offending kwarg at a time.
    """
    opts = dict(extra)
    while True:
        try:
            return app.search(query, limit=limit, **opts)
        except TypeError as e:
            match = _UNSUPPORTED_KWARG.search(str(e))
            if not match or match.group(1) not in opts:
                if not opts:
                    raise
                logger.warning(f"Firecrawl search TypeError ({e}); retrying with no extra options")
                opts = {}
                continue
            dropped = match.group(1)
            opts.pop(dropped)
            logger.warning(
                f"firecrawl-py does not support '{dropped}'; retrying without it "
                f"(remaining: {sorted(opts)})"
            )


def firecrawl_search(
    query: str,
    limit: int = 10,
    with_content: bool = False,
    *,
    tbs: Optional[str] = None,
    sources: Optional[list] = None,
    location: Optional[str] = None,
    include_domains: Optional[list] = None,
    exclude_domains: Optional[list] = None,
):
    """
    Search the web via Firecrawl.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10, costs 2 credits per 10)
        with_content: When True, scrape full markdown content for each result.
                      Each result item will have a .markdown attribute with the
                      article body (more credits used, but much richer data).
        tbs: Recency filter passed to the search backend. "qdr:w" = past week,
             "qdr:d" = past day, "qdr:m" = past month. Without this the engine
             happily returns years-old articles, which is the single biggest
             cause of stale market reports.
        sources: Result channels, e.g. ["news"], ["web", "news"]. News results
                 carry a published date, which lets us drop off-window articles.
        location: Country bias for the search backend, e.g. "KR" / "US".
        include_domains: Allowlist of domains. Use to pin results to primary
                         outlets instead of blog/community aggregators.
        exclude_domains: Blocklist of domains.

    Returns:
        SearchData object with .web / .news lists of results, or None on error
    """
    extra = {
        "tbs": tbs,
        "sources": sources,
        "location": location,
        "include_domains": include_domains,
        "exclude_domains": exclude_domains,
    }
    extra = {k: v for k, v in extra.items() if v}

    if with_content:
        scrape_opts = _build_scrape_options()
        if scrape_opts is not None:
            extra["scrape_options"] = scrape_opts

    try:
        app = get_firecrawl_app()
        result = _search_dropping_unsupported(app, query, limit, extra)
        if result is None:
            return None

        n_web = len(result.web) if getattr(result, "web", None) else 0
        n_news = len(result.news) if getattr(result, "news", None) else 0
        logger.info(
            f"Firecrawl search: query='{query[:60]}', web={n_web}, news={n_news}, "
            f"with_content={with_content}, opts={sorted(extra)}"
        )
        return result
    except Exception as e:
        logger.error(f"Firecrawl search failed: {e}")
        return None


# Sources whose numbers must never be trusted for factual claims.
_LOW_TRUST_HOSTS = (
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "medium.com",
    "namu.wiki", "dcinside.com", "fmkorea.com", "clien.net",
)


_RELATIVE_DATE = re.compile(r"^\s*(\d+)\s+(minute|hour|day|week|month)s?\s+ago\s*$", re.I)
_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}


def _absolute_date(raw: str) -> str:
    """
    Firecrawl news results report dates like "2 days ago". The report needs to
    decide whether an article falls inside the target week, which relative
    phrasing makes impossible — so resolve it to a real date.
    Non-relative values pass through untouched.
    """
    if not raw:
        return ""
    match = _RELATIVE_DATE.match(raw)
    if not match:
        return raw
    amount, unit = int(match.group(1)), match.group(2).lower()
    delta = timedelta(days=amount * _UNIT_DAYS[unit])
    return f"{(datetime.now() - delta):%Y-%m-%d} ({raw.strip()})"


def _pick(raw, meta, *names) -> str:
    """First non-empty value among `names`, checked on the object then its metadata."""
    for name in names:
        val = getattr(raw, name, None)
        if val:
            return val
    if isinstance(meta, dict):
        for name in names:
            val = meta.get(name)
            if val:
                return val
    return ""


def normalize_search_items(result) -> list[dict]:
    """
    Flatten a Firecrawl SearchData into plain dicts.

    Handles both bare search results (title/url/description|snippet/date) and
    scraped Documents (markdown + metadata), which the SDK returns
    interchangeably depending on whether scrape_options was set.
    """
    items: list[dict] = []
    if not result:
        return items

    for channel in ("news", "web"):
        for raw in (getattr(result, channel, None) or []):
            meta = getattr(raw, "metadata", None) or {}

            url = _pick(raw, meta, "url", "sourceURL", "source_url")
            if not url:
                continue

            items.append({
                "title": _pick(raw, meta, "title", "og_title", "ogTitle"),
                "url": url,
                "date": _absolute_date(
                    _pick(raw, meta, "date", "publishedTime", "published_time", "modifiedTime")
                ),
                "body": (getattr(raw, "markdown", "") or ""),
                "snippet": _pick(raw, meta, "snippet", "description"),
                "channel": channel,
                "low_trust": any(h in url for h in _LOW_TRUST_HOSTS),
            })
    return items


def _search_passes(sources, include_domains, limit) -> list[dict]:
    """
    Split one logical search into the API calls it actually needs.

    Verified against the live API: passing `include_domains` suppresses the
    news channel entirely (news=0, results silently fall back to web). Since
    news is the only channel carrying publish dates, it has to be fetched in
    its own unfiltered pass whenever a domain allowlist is in play.
    """
    srcs = list(sources) if sources else ["web"]

    if not (include_domains and "news" in srcs):
        return [{"sources": srcs, "include_domains": include_domains, "limit": limit}]

    news_limit = max(1, limit // 2)
    web_srcs = [s for s in srcs if s != "news"] or ["web"]
    return [
        {"sources": ["news"], "include_domains": None, "limit": news_limit},
        {"sources": web_srcs, "include_domains": include_domains, "limit": limit - news_limit},
    ]


def firecrawl_search_multi(
    queries: list[str],
    *,
    sources=None,
    include_domains=None,
    limit: int = 10,
    **kwargs,
) -> list[dict]:
    """
    Fan out several queries and merge results, de-duplicated by URL.

    A single query can only surface one slice of a week. Market recaps need
    index moves, flows, themes and the forward calendar — those are different
    searches, not different paragraphs of one search.
    """
    passes = _search_passes(sources, include_domains, limit)
    seen: set[str] = set()
    merged: list[dict] = []

    for q in queries:
        for opts in passes:
            result = firecrawl_search(q, **opts, **kwargs)
            for item in normalize_search_items(result):
                url = item["url"].split("?")[0].rstrip("/")
                if url in seen:
                    continue
                seen.add(url)
                item["query"] = q
                merged.append(item)

    n_dated = sum(1 for i in merged if i.get("date"))
    logger.info(
        f"firecrawl_search_multi: {len(queries)} queries x {len(passes)} passes "
        f"-> {len(merged)} unique results ({n_dated} dated)"
    )
    return merged


def _extract_agent_text(result) -> Optional[str]:
    """Extract readable text from Firecrawl agent result, trying multiple formats."""
    if not result:
        return None

    # Try result.data (common in SDK v4)
    if hasattr(result, 'data') and result.data:
        data = result.data
        if isinstance(data, dict):
            # Try known keys in priority order
            for key in ['telegram_message', 'result', 'text', 'answer', 'report', 'report_content', 'content']:
                val = data.get(key)
                if val and isinstance(val, str) and len(val) > 50:
                    return val
            # Try nested dict — search all string values recursively
            for key, val in data.items():
                if isinstance(val, str) and len(val) > 100:
                    return val
                if isinstance(val, dict):
                    for k2, v2 in val.items():
                        if isinstance(v2, str) and len(v2) > 100:
                            return v2
            # Last resort: stringify the whole dict
            text = str(data)
            if len(text) > 50:
                return text
        elif isinstance(data, str) and len(data) > 50:
            return data
        else:
            return str(data)

    # Try result as dict directly
    if isinstance(result, dict):
        for key in ['data', 'result', 'telegram_message', 'text']:
            val = result.get(key)
            if val and isinstance(val, str) and len(val) > 50:
                return val
            if isinstance(val, dict):
                return _extract_agent_text(type('Obj', (), {'data': val})())

    # Try result as string
    if isinstance(result, str) and len(result) > 50:
        return result

    return None


def firecrawl_agent(prompt: str, max_credits: int = 200, model: Literal["spark-1-mini", "spark-1-pro"] = "spark-1-mini") -> Optional[str]:
    """
    Run Firecrawl agent (Spark) with a prompt.

    Args:
        prompt: Natural language prompt for the agent
        max_credits: Maximum credits to spend (default 200)
        model: Agent model to use (default "spark-1-mini")

    Returns:
        Agent response text, or None on error
    """
    try:
        app = get_firecrawl_app()
        result = app.agent(
            prompt=prompt,
            model=model,
            max_credits=max_credits,
        )
        # Debug: log raw result structure
        logger.info(f"Firecrawl agent raw result type: {type(result)}")
        if result:
            logger.info(f"Firecrawl agent result attrs: {[a for a in dir(result) if not a.startswith('_')]}")

        # Extract text from result — try multiple response formats
        text = _extract_agent_text(result)
        if text:
            logger.info(f"Firecrawl agent response: {len(text)} chars")
            return text

        logger.warning(f"Firecrawl agent returned empty result. Raw: {str(result)[:500]}")
        return None
    except Exception as e:
        logger.error(f"Firecrawl agent failed: {e}")
        return None
