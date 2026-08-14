"""确定性解析 JD 导入中的混合文本与 URL。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from app.jd_import.agent.types import ImportSource, ParsedInput

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）】"


class JDImportInputError(ValueError):
    """稳定的请求级混合输入校验错误。"""


def _normalize_url(raw_url: str) -> str:
    candidate = raw_url.rstrip(_TRAILING_PUNCTUATION)
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    if not host:
        return candidate
    host_text = f"[{host}]" if ":" in host else host
    port = parsed.port
    if port is not None and not (
        parsed.scheme.lower() == "http" and port == 80
        or parsed.scheme.lower() == "https" and port == 443
    ):
        host_text = f"{host_text}:{port}"
    if parsed.username is not None:
        credentials = parsed.username
        if parsed.password is not None:
            credentials += f":{parsed.password}"
        host_text = f"{credentials}@{host_text}"
    return urlunsplit(
        (parsed.scheme.lower(), host_text, parsed.path or "", parsed.query, "")
    )


def parse_mixed_input(raw_input: str, *, max_urls: int = 10) -> ParsedInput:
    """提取规范化且去重的 URL，同时保留其余全部文本。"""
    if not raw_input.strip():
        raise JDImportInputError("empty_input")

    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.finditer(raw_input):
        normalized = _normalize_url(match.group(0))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    if len(urls) > max_urls:
        raise JDImportInputError("too_many_urls")

    text = _URL_PATTERN.sub("", raw_input).strip()
    sources: list[ImportSource] = []
    if text:
        sources.append(
            ImportSource(source_id="source:text:0", type="text", content=text)
        )
    sources.extend(
        ImportSource(
            source_id=f"source:url:{index}",
            type="url",
            source_url=url,
            url_status="skipped",
        )
        for index, url in enumerate(urls)
    )
    return ParsedInput(raw_input=raw_input, text=text, urls=urls, sources=sources)
