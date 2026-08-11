"""后台任务 Outbox 的持久化模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def utcnow_iso() -> str:
    """返回可按字符串排序的 UTC 时间。"""
    return datetime.now(UTC).isoformat()


class BackgroundJobOutbox(Base):
    """与业务事务一起写入、随后投递到 Redis 的后台事件。"""

    __tablename__ = "background_job_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(120))
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    published_at: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'published', 'processed')",
            name="ck_background_job_outbox_status",
        ),
        Index(
            "ix_background_job_outbox_dispatch",
            "status",
            "available_at",
            "id",
        ),
    )
