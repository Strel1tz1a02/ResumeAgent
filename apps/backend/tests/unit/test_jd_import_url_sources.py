"""安全 URL 策略与受限 Playwright MCP 来源提供器测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from app.jd_import.sources.playwright_mcp import PlaywrightMCPSourceProvider
from app.jd_import.sources.url_policy import UrlPolicy


def test_url_policy_accepts_only_public_http_urls() -> None:
    policy = UrlPolicy(lambda _host: ["93.184.216.34"])
    assert policy.validate("HTTPS://Example.com/job#apply").url == "https://example.com/job"
    for url in (
        "ftp://example.com/job",
        "https://user:pass@example.com/job",
        "https://example.com:8080/job",
        "http://127.0.0.1/job",
        "http://169.254.169.254/latest/meta-data",
        "http://[::ffff:127.0.0.1]/job",
    ):
        with pytest.raises(ValueError):
            UrlPolicy(lambda _host: ["127.0.0.1"]).validate(url)


def test_url_policy_delegates_hostname_dns_but_still_blocks_literal_private_ip() -> None:
    policy = UrlPolicy(
        lambda _host: ["198.18.0.29"],
        resolve_hostnames=False,
    )

    validated = policy.validate("https://jobs.example.com/1")

    assert validated.url == "https://jobs.example.com/1"
    assert validated.addresses == ()
    with pytest.raises(ValueError, match="url_address_not_public"):
        policy.validate("https://198.18.0.29/1")


class FakeClient:
    def __init__(self, snapshot_text: str = "Page URL: https://jobs.example.com/1\nBackend Engineer") -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.snapshot_text = snapshot_text

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name="browser_navigate"), SimpleNamespace(name="browser_snapshot")]
        )

    async def call_tool(self, name: str, arguments: dict[str, str]):
        self.calls.append((name, arguments))
        text = "navigated" if name == "browser_navigate" else self.snapshot_text
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


async def test_provider_uses_only_navigation_and_snapshot_and_truncates() -> None:
    client = FakeClient()

    @asynccontextmanager
    async def factory(_endpoint: str):
        yield client

    policy = UrlPolicy(lambda _host: ["93.184.216.34"])
    provider = PlaywrightMCPSourceProvider(
        "http://mcp",
        egress_secured=True,
        policy=policy,
        max_chars=20,
        settle_seconds=0,
        client_factory=factory,
    )
    result = await provider.fetch(policy.validate("https://jobs.example.com/1"))
    assert result.status == "fetched"
    assert len(result.text) == 20
    assert [name for name, _args in client.calls] == ["browser_navigate", "browser_snapshot"]


async def test_provider_fails_closed_without_egress_boundary() -> None:
    policy = UrlPolicy(lambda _host: ["93.184.216.34"])
    provider = PlaywrightMCPSourceProvider("http://mcp", egress_secured=False, policy=policy)
    result = await provider.fetch(policy.validate("https://example.com/job"))
    assert result.status == "blocked"
    assert result.error_code == "source_security_unavailable"


async def test_provider_rejects_page_metadata_with_empty_snapshot() -> None:
    snapshot = (
        "### Page\n"
        "- Page URL: https://jobs.example.com/1\n"
        "- Page Title: Jobs\n"
        "- Console: 3 errors, 0 warnings\n"
        "### Snapshot\n"
        "```yaml\n\n```"
    )
    client = FakeClient(snapshot)

    @asynccontextmanager
    async def factory(_endpoint: str):
        yield client

    policy = UrlPolicy(lambda _host: ["93.184.216.34"])
    provider = PlaywrightMCPSourceProvider(
        "http://mcp",
        egress_secured=True,
        policy=policy,
        settle_seconds=0,
        client_factory=factory,
    )

    result = await provider.fetch(policy.validate("https://jobs.example.com/1"))

    assert result.status == "failed"
    assert result.error_code == "source_empty_content"
    assert result.text == ""


async def test_provider_revalidates_redirect_and_maps_login_page() -> None:
    policy = UrlPolicy(lambda host: ["127.0.0.1"] if host == "internal.test" else ["93.184.216.34"])

    @asynccontextmanager
    async def redirect_factory(_endpoint: str):
        yield FakeClient("Page URL: http://internal.test/private")

    provider = PlaywrightMCPSourceProvider(
        "http://mcp",
        egress_secured=True,
        policy=policy,
        settle_seconds=0,
        client_factory=redirect_factory,
    )
    assert (await provider.fetch(policy.validate("https://example.com/job"))).status == "blocked"

    @asynccontextmanager
    async def login_factory(_endpoint: str):
        yield FakeClient("Page URL: https://example.com/login\nSign in to continue")

    provider = PlaywrightMCPSourceProvider(
        "http://mcp",
        egress_secured=True,
        policy=policy,
        settle_seconds=0,
        client_factory=login_factory,
    )
    result = await provider.fetch(policy.validate("https://example.com/job"))
    assert result.status == "blocked"
    assert result.error_code == "source_interaction_required"
