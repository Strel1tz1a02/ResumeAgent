"""经历乐观锁 ORM 模型。"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.experience.models.common import utcnow_iso


class ExperienceRevision(Base):
    """经历数据单元和 Evidence 集合的统一乐观锁。"""

    __tablename__ = "experience_revisions"
    __table_args__ = (
        UniqueConstraint(
            "experience_id",
            "scope",
            "unit_key",
            "ref_id",
            name="uq_experience_revision_target",
        ),
        CheckConstraint(
            "scope IN ('unit', 'collection')",
            name="ck_experience_revision_scope",
        ),
        CheckConstraint("revision >= 0", name="ck_experience_revision_nonnegative"),
        Index("ix_experience_revisions_experience_id", "experience_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experience_id: Mapped[int] = mapped_column(
        ForeignKey("experience_items.experience_id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_key: Mapped[str] = mapped_column(String(80), nullable=False)
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
