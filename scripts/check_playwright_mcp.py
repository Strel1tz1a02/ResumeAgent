"""Verify that the configured Playwright MCP endpoint exposes required tools."""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import Client


async def check(endpoint: str, navigate_url: str | None = None) -> None:
    async with Client(endpoint) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}
        required = {"browser_navigate", "browser_snapshot"}
        missing = required - names
        if missing:
            raise RuntimeError(f"Playwright MCP is missing required tools: {sorted(missing)}")
        if navigate_url:
            navigation = await client.call_tool("browser_navigate", {"url": navigate_url})
            snapshot = await client.call_tool("browser_snapshot", {})
            if navigation.is_error or snapshot.is_error:
                raise RuntimeError(
                    f"Playwright MCP navigation failed for {navigate_url}: "
                    f"navigate={navigation.content!r}, snapshot={snapshot.content!r}"
                )
        print(f"Playwright MCP ready at {endpoint} ({len(names)} tools)")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
        "PLAYWRIGHT_MCP_URL", "http://127.0.0.1:8931/mcp"
    )
    target = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(check(url, target))
