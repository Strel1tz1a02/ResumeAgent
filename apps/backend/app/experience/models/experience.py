"""个人经历 ORM 模型。"""

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.experience.models.common import utcnow_iso

if TYPE_CHECKING:
    from app.experience.models.evidence import ExperienceEvidence


class ExperienceItem(Base):
    """一条与简历文档解耦的个人经历记录。"""

    __tablename__ = "experience_items"

    experience_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="other", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    organization: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(7), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(7), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    completeness: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso, index=True)

    evidence_links: Mapped[list["ExperienceEvidence"]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        order_by="ExperienceEvidence.position",
        lazy="selectin",
        passive_deletes=True,
    )
