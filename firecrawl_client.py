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


def firecrawl_search(query: str, limit: int = 10):
    """
    Search the web via Firecrawl.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10, costs 2 credits per 10)

    Returns:
        SearchData object with .web list of results, or None on error
    """
    try:
        app = get_firecrawl_app()
        result = app.search(query, limit=limit)
        logger.info(f"Firecrawl search completed: query='{query[:50]}', results={len(result.web) if result and result.web else 0}")
        return result
    except Exception as e:
        logger.error(f"Firecrawl search failed: {e}")
        return None


def firecrawl_search_and_analyze(search_query: str, analysis_prompt: str, limit: int = 5) -> Optional[str]:
    """
    Cost-efficient alternative to firecrawl_agent.
    Uses Firecrawl /search (2 credits) + Claude Sonnet for analysis.

    Args:
        search_query: Web search query
        analysis_prompt: Prompt for Claude to analyze the search results
        limit: Number of search results (default 5)

    Returns:
        Claude-generated analysis text, or None on error
    """
    try:
        # Step 1: Firecrawl search (2 credits per 10 results)
        result = firecrawl_search(search_query, limit=limit)
        items = result.web if result and result.web else []

        if not items:
            logger.warning(f"No search results for: {search_query[:50]}")
            return None

        # Step 2: Build context from search snippets
        context = ""
        for item in items:
            title = getattr(item, 'title', '') or ''
            url = getattr(item, 'url', '') or ''
            desc = getattr(item, 'description', '') or ''
            context += f"- {title}\n  URL: {url}\n  {desc}\n\n"

        logger.info(f"Search context built: {len(items)} results, {len(context)} chars")

        # Step 3: Call Claude Sonnet 4.6 for analysis
        import anthropic

        # Resolve Anthropic API key
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            try:
                import yaml
                config_path = os.path.join(os.path.dirname(__file__), "mcp_agent.secrets.yaml")
                with open(config_path, 'r') as f:
                    secrets = yaml.safe_load(f)
                api_key = secrets.get("anthropic", {}).get("api_key")
            except Exception:
                pass
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not found")
            return None

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"다음은 웹 검색 결과입니다:\n\n{context}\n\n---\n\n{analysis_prompt}"
            }]
        )

        response_text = message.content[0].text
        logger.info(f"Claude analysis completed: {len(response_text)} chars")
        return response_text

    except Exception as e:
        logger.error(f"firecrawl_search_and_analyze failed: {e}", exc_info=True)
        return None


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
