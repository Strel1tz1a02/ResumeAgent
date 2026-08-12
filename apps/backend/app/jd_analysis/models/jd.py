"""ORM models for source JDs and their structured analysis."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class JDOrigin(Base):
    """Original JD text, kept as the immutable source of truth."""

    __tablename__ = "jd_origin"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    information: Mapped[JDInformation] = relationship(
        back_populates="origin",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class JDInformation(Base):
    """One editable structured analysis for one source JD."""

    __tablename__ = "jd_information"
    __table_args__ = (
        UniqueConstraint("jd_origin_id"),
        CheckConstraint(
            "status IN ('analysing', 'confirmed')", name="ck_jd_information_status"
        ),
        CheckConstraint("revision >= 0", name="ck_jd_information_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jd_origin_id: Mapped[int] = mapped_column(
        ForeignKey("jd_origin.id", ondelete="CASCADE"), index=True
    )
    company: Mapped[str] = mapped_column(String(200), default="")
    job_name: Mapped[str] = mapped_column(String(200), default="")
    type: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="analysing", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)

    origin: Mapped[JDOrigin] = relationship(
        back_populates="information", lazy="selectin"
    )
    requirements: Mapped[list[JDRequirement]] = relationship(
        back_populates="information",
        cascade="all, delete-orphan",
        order_by=lambda: (JDRequirement.sort_order, JDRequirement.id),
        passive_deletes=True,
        lazy="selectin",
    )


class JDRequirement(Base):
    """One ordered requirement without imposing a requirement category."""

    __tablename__ = "jd_requirements"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('required', 'preferred', 'normal')",
            name="ck_jd_requirement_priority",
        ),
        CheckConstraint("sort_order >= 0", name="ck_jd_requirement_sort_order"),
        CheckConstraint("revision >= 0", name="ck_jd_requirement_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jd_information_id: Mapped[int] = mapped_column(
        ForeignKey("jd_information.id", ondelete="CASCADE"), index=True
    )
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    content: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)

    information: Mapped[JDInformation] = relationship(back_populates="requirements")
