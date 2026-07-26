#!/usr/bin/env python3
"""
Firecrawl Client Module

Singleton FirecrawlApp instance with helper functions for search and agent calls.
API key is loaded from FIRECRAWL_API_KEY env var or mcp_agent.config.yaml fallback.
"""
import logging
import os
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

    try:
        app = get_firecrawl_app()
        if with_content:
            try:
                from firecrawl import ScrapeOptions  # available in firecrawl-py >= 1.0
                # only_main_content strips nav/menu/footer boilerplate — without it
                # the first 2000 chars of an article are usually junk.
                scrape_opts = ScrapeOptions(formats=["markdown"], only_main_content=True)
                result = app.search(query, limit=limit, scrape_options=scrape_opts, **extra)
            except (ImportError, TypeError) as ie:
                # Fallback: SDK version doesn't support ScrapeOptions — use plain search
                logger.warning(f"ScrapeOptions not available ({ie}), falling back to plain search")
                result = app.search(query, limit=limit, **extra)
        else:
            result = app.search(query, limit=limit, **extra)

        n_web = len(result.web) if result and getattr(result, "web", None) else 0
        n_news = len(result.news) if result and getattr(result, "news", None) else 0
        logger.info(
            f"Firecrawl search: query='{query[:60]}', web={n_web}, news={n_news}, "
            f"with_content={with_content}, opts={extra}"
        )
        return result
    except TypeError as e:
        # SDK too old for one of the extra kwargs — retry without them rather than
        # silently returning nothing.
        if extra:
            logger.warning(f"Firecrawl search rejected extra opts ({e}); retrying without {list(extra)}")
            return firecrawl_search(query, limit=limit, with_content=with_content)
        logger.error(f"Firecrawl search failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Firecrawl search failed: {e}")
        return None


# Sources whose numbers must never be trusted for factual claims.
_LOW_TRUST_HOSTS = (
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "medium.com",
    "namu.wiki", "dcinside.com", "fmkorea.com", "clien.net",
)


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
                "date": _pick(raw, meta, "date", "publishedTime", "published_time", "modifiedTime"),
                "body": (getattr(raw, "markdown", "") or ""),
                "snippet": _pick(raw, meta, "snippet", "description"),
                "channel": channel,
                "low_trust": any(h in url for h in _LOW_TRUST_HOSTS),
            })
    return items


def firecrawl_search_multi(queries: list[str], **kwargs) -> list[dict]:
    """
    Fan out several queries and merge results, de-duplicated by URL.

    A single query can only surface one slice of a week. Market recaps need
    index moves, flows, themes and the forward calendar — those are different
    searches, not different paragraphs of one search.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for q in queries:
        for item in normalize_search_items(firecrawl_search(q, **kwargs)):
            url = item["url"].split("?")[0].rstrip("/")
            if url in seen:
                continue
            seen.add(url)
            item["query"] = q
            merged.append(item)
    logger.info(f"firecrawl_search_multi: {len(queries)} queries -> {len(merged)} unique results")
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
