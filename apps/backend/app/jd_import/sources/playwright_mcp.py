"""受限的 Playwright MCP 适配器：仅允许导航和无障碍快照。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.jd_import.sources.url_policy import UrlPolicy, ValidatedUrl

_PAGE_URL = re.compile(r"(?:Page URL:|URL:)\s*(https?://\S+)", re.IGNORECASE)
_BLOCKED_MARKERS = ("captcha", "sign in", "log in", "access denied", "机器人验证", "登录")


class PageSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    final_url: str | None = None
    text: str = ""
    error_code: str | None = None


class PageSourceProvider(Protocol):
    async def fetch(self, url: ValidatedUrl) -> PageSourceResult: ...


class _MCPClient(Protocol):
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


ClientFactory = Callable[[str], AbstractAsyncContextManager[_MCPClient]]


def _official_client(endpoint: str) -> AbstractAsyncContextManager[_MCPClient]:
    from mcp import Client

    return Client(endpoint)


def _tool_names(result: Any) -> set[str]:
    tools = getattr(result, "tools", result)
    return {
        item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
        for item in tools
    }


def _text_blocks(result: Any) -> list[str]:
    blocks = getattr(result, "content", [])
    values: list[str] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        value = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type")
            value = block.get("text")
        if block_type == "text" and isinstance(value, str):
            values.append(value)
    return values


def _reported_url(*results: Any) -> str | None:
    for result in results:
        structured = getattr(result, "structuredContent", None)
        if isinstance(result, dict):
            structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("url"), str):
            return structured["url"]
        for value in _text_blocks(result):
            match = _PAGE_URL.search(value)
            if match:
                return match.group(1)
    return None


class PlaywrightMCPSourceProvider:
    def __init__(
        self,
        endpoint: str | None,
        *,
        egress_secured: bool,
        policy: UrlPolicy | None = None,
        timeout_seconds: float = 20.0,
        max_chars: int = 100_000,
        client_factory: ClientFactory = _official_client,
    ) -> None:
        self._endpoint = endpoint
        self._egress_secured = egress_secured
        self._policy = policy or UrlPolicy()
        self._timeout = timeout_seconds
        self._max_chars = max_chars
        self._client_factory = client_factory

    async def fetch(self, url: ValidatedUrl) -> PageSourceResult:
        if not self._endpoint or not self._egress_secured:
            return PageSourceResult(status="blocked", error_code="source_security_unavailable")
        try:
            return await asyncio.wait_for(self._fetch(url), timeout=self._timeout)
        except TimeoutError:
            return PageSourceResult(status="failed", error_code="source_timeout")
        except ValueError as error:
            return PageSourceResult(status="blocked", error_code=str(error))
        # 第三方 MCP 传输层可能抛出多类异常；此边界统一转换为稳定的公开错误码。
        except Exception:  # noqa: BLE001
            return PageSourceResult(status="failed", error_code="source_fetch_failed")

    async def _fetch(self, url: ValidatedUrl) -> PageSourceResult:
        async with self._client_factory(self._endpoint or "") as client:
            names = _tool_names(await client.list_tools())
            required = {"browser_navigate", "browser_snapshot"}
            if not required.issubset(names):
                raise RuntimeError("required Playwright MCP tools unavailable")
            navigation = await client.call_tool("browser_navigate", {"url": url.url})
            snapshot = await client.call_tool("browser_snapshot", {})

        final_url = _reported_url(snapshot, navigation)
        if final_url is None:
            raise ValueError("source_final_url_missing")
        validated_final = self._policy.validate(final_url)
        text = "\n".join(_text_blocks(snapshot))[: self._max_chars]
        if any(marker in text.lower() for marker in _BLOCKED_MARKERS):
            return PageSourceResult(
                status="blocked",
                final_url=validated_final.url,
                error_code="source_interaction_required",
            )
        return PageSourceResult(status="fetched", final_url=validated_final.url, text=text)
