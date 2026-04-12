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
        # Extract text from result — result.data is a dict
        if result and hasattr(result, 'data') and result.data:
            data = result.data
            if isinstance(data, dict):
                # Try common keys
                return data.get('result') or data.get('telegram_message') or data.get('text') or str(data)
            return str(data)
        logger.warning("Firecrawl agent returned empty result")
        return None
    except Exception as e:
        logger.error(f"Firecrawl agent failed: {e}")
        return None
