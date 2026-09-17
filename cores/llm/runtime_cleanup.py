"""Process-entrypoint cleanup only; never call between shared pipeline stages."""
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def shutdown_mcp_logging(timeout_seconds=15.0):
    """Stop an existing MCP event bus after every agent and proxy is finished.

    Do not initialize an SDK global or reset shared state. Timeout handling
    requires cooperative SDK cancellation; this is not a process-kill fallback.
    """
    module = sys.modules.get("mcp_agent.logging.transport")
    bus_class = getattr(module, "AsyncEventBus", None)
    bus = getattr(bus_class, "_instance", None)
    if bus is None:
        return True
    try:
        await asyncio.wait_for(bus.stop(), timeout=timeout_seconds)
        return True
    except asyncio.TimeoutError:
        logger.warning("MCP logging shutdown exceeded its timeout")
    except Exception:  # noqa: BLE001 - best-effort SDK shutdown must not mask batch result
        logger.warning("MCP logging shutdown failed")
    return False


def pending_runtime_tasks():
    """Return bounded task types, not coroutine locals or credential-bearing stacks."""
    current = asyncio.current_task()
    return sorted(
        getattr(task.get_coro(), "__qualname__", type(task.get_coro()).__name__)
        for task in asyncio.all_tasks() if task is not current and not task.done()
    )[:100]
