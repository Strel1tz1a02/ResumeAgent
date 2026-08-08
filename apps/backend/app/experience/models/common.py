"""Experience ORM 模型共享的审计时间。"""

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """返回 UTC ISO-8601 审计时间戳。"""
    return datetime.now(timezone.utc).isoformat()
