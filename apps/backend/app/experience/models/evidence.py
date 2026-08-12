"""经历证据及其归属关系 ORM 模型。"""

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.experience.models.common import utcnow_iso
from app.models import Base

if TYPE_CHECKING:
    from app.experience.models.experience import ExperienceItem


class EvidenceItem(Base):
    """一条由经历引用的有序背景、行动和结果事实。"""

    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)


class ExperienceEvidence(Base):
    """保存经历对 EvidenceItem 的唯一归属和展示顺序。"""

    __tablename__ = "experience_evidence_items"
    __table_args__ = (
        UniqueConstraint("evidence_id", name="uq_experience_evidence_item_owner"),
        UniqueConstraint(
            "experience_id", "position", name="uq_experience_evidence_item_position"
        ),
        CheckConstraint("position >= 0", name="ck_experience_evidence_item_position"),
        Index("ix_experience_evidence_items_experience_id", "experience_id"),
    )

    experience_id: Mapped[int] = mapped_column(
        ForeignKey("experience_items.experience_id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    experience: Mapped["ExperienceItem"] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceItem] = relationship(lazy="joined")
