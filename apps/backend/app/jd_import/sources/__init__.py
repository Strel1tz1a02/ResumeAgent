"""JD 导入的外部来源边界。"""

from app.jd_import.sources.playwright_mcp import (
    PageSourceProvider,
    PageSourceResult,
    PlaywrightMCPSourceProvider,
)
from app.jd_import.sources.url_policy import UrlPolicy, ValidatedUrl

__all__ = [
    "PageSourceProvider",
    "PageSourceResult",
    "PlaywrightMCPSourceProvider",
    "UrlPolicy",
    "ValidatedUrl",
]
