"""经历字段完善状态 ORM 模型。"""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.experience.models.common import utcnow_iso


class ExperienceFieldState(Base):
    """一个经历字段只供前端提醒使用的完善状态。"""

    __tablename__ = "experience_field_states"
    __table_args__ = (
        UniqueConstraint(
            "experience_id", "target_key", "ref_id", name="uq_experience_field_state_target"
        ),
        Index("ix_experience_field_states_experience_id", "experience_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experience_id: Mapped[int] = mapped_column(
        ForeignKey("experience_items.experience_id", ondelete="CASCADE"), nullable=False
    )
    target_key: Mapped[str] = mapped_column(String(80), nullable=False)
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="incomplete"
    )
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
