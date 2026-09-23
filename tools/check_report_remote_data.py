"""Read-only deployment smoke for the Telegram -> DB KIS data path.

Run from the app-server checkout: python tools/check_report_remote_data.py --mcp
No orders, report generation, Telegram messages, or credential output.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def check_mcp(ticker, start, end):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from cores.llm.config_loader import load_report_mcp_registry

    spec = load_report_mcp_registry().get("kospi_kosdaq")
    params = StdioServerParameters(command=sys.executable, args=list(spec.args), env=dict(spec.env))
    # Keep stream creation visibly before the session that consumes the streams.
    async with stdio_client(params) as (reader, writer):  # noqa: SIM117
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("get_stock_ohlcv", {
                "ticker": ticker, "fromdate": start, "todate": end,
            })
            if result.isError:
                raise RuntimeError("MCP tool failed")
            payload = json.loads(result.content[0].text)
            rows = [key for key in payload if key != "__meta__"]
            if "error" in payload or not rows:
                raise RuntimeError("MCP data missing")
            return len(rows)


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    args = parser.parse_args()
    url = os.getenv("ARCHIVE_API_URL", "")
    if not url:
        raise RuntimeError("Archive tunnel is not configured")
    os.environ.setdefault("PRISM_MARKET_DATA_REMOTE_URL", url)
    os.environ["PRISM_MCP_PYTHON"] = sys.executable

    from cores.market_data import default_chain, get_market_trading_volume_by_date

    chain = default_chain()
    if chain.names != ["kis-remote"]:
        raise RuntimeError("Remote-only routing is not active")
    today = datetime.now(ZoneInfo("Asia/Seoul"))
    start, end = (today - timedelta(days=180)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
    stock = chain.fetch("price_history", "000660", start, end)
    index = chain.fetch("index_history", "1001", start, end)
    market = chain.fetch("ticker_market", "000660")
    flow_start = (today - timedelta(days=30)).strftime("%Y%m%d")
    flows = get_market_trading_volume_by_date(flow_start, end, "000660")
    if stock.empty or index.empty or flows.empty:
        raise RuntimeError("Remote market data missing")
    if market != "KOSPI":
        raise RuntimeError("Unexpected listing market")
    result = {"provider": chain.names, "stock_rows": len(stock), "index_rows": len(index), "market": market,
              "flow_rows": len(flows), "flow_last_date": str(flows.index.max())}
    if args.mcp:
        result["mcp_rows"] = asyncio.run(asyncio.wait_for(check_mcp("000660", start, end), 45))
    if "krx_data_client" in sys.modules or "kis_auth" in sys.modules:
        raise RuntimeError("Local credentialed provider was loaded")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deployment output must not expose credentials
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
