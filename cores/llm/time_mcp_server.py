"""Small in-repo MCP time server with no external package bootstrap.

The published ``mcp-server-time`` package currently imports ``McpError`` from
an API that newer ``mcp`` releases renamed.  Starting it through ``uvx`` then
closes the connection before an agent can call ``get_current_time``.  Keeping
the two tiny time operations here removes that runtime compatibility surface.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time")


def _timezone(name: str) -> ZoneInfo:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("timezone is required")
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {normalized}") from exc


@mcp.tool()
def get_current_time(timezone: str) -> dict[str, object]:
    """Return the current ISO-8601 time in an IANA timezone."""
    zone = _timezone(timezone)
    current = datetime.now(zone)
    return {
        "timezone": timezone,
        "datetime": current.isoformat(),
        "is_dst": bool(current.dst()),
    }


@mcp.tool()
def convert_time(
    source_timezone: str,
    time: str,
    target_timezone: str,
) -> dict[str, object]:
    """Convert an ISO-8601 local datetime between IANA timezones."""
    source_zone = _timezone(source_timezone)
    target_zone = _timezone(target_timezone)
    try:
        parsed = datetime.fromisoformat(str(time).strip())
    except ValueError as exc:
        raise ValueError(f"time must be ISO-8601, got {time!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_zone)
    else:
        parsed = parsed.astimezone(source_zone)
    converted = parsed.astimezone(target_zone)
    return {
        "source_timezone": source_timezone,
        "source_datetime": parsed.isoformat(),
        "target_timezone": target_timezone,
        "target_datetime": converted.isoformat(),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
