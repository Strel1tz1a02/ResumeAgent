"""SQLAlchemy ORM models for Resume Matcher.

A single declarative ``Base`` backs all tables (doc tables migrated from
TinyDB plus the new ``applications`` and ``api_keys`` tables). The facade in
``app/database.py`` converts ORM rows to plain dicts so the rest of the app
never sees ORM objects — preserving the TinyDB-era contracts.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Timestamps are stored as strings (not native datetimes) to preserve the
    TinyDB-era behavior: code compares them lexically and returns them to
    clients verbatim.
    """
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    """Declarative base shared by every table."""


class Resume(Base):
    """A resume document (master or tailored)."""

    __tablename__ = "resumes"

    resume_id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String, default="md")
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_status: Mapped[str] = mapped_column(String, default="pending")
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_prep: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    # original_markdown has *absence* semantics in the TinyDB era: the key was
    # omitted entirely when None. The facade reproduces that by only emitting
    # the key when this column is non-null.
    original_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # At most one master resume. Partial unique index enforces the invariant
        # at the storage layer; ``_master_resume_lock`` remains the primary
        # (race-free) mechanism in the facade.
        Index(
            "ux_resumes_single_master",
            "is_master",
            unique=True,
            sqlite_where=text("is_master = 1"),
        ),
    )


class Job(Base):
    """A job description.

    Only the stable columns are first-class; everything the pipeline attaches
    dynamically (``job_keywords``, ``job_keywords_hash``, ``preview_hash``,
    ``preview_hashes``, ``preview_prompt_id``, ``company``, ``role``) lives in
    ``metadata_json``. The facade flattens that map to top-level keys on read
    and merges non-core keys into it on update, reproducing TinyDB semantics.
    """

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Improvement(Base):
    """A tailoring result linking an original resume, a tailored resume, and a job."""

    __tablename__ = "improvements"

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    original_resume_id: Mapped[str] = mapped_column(String)
    tailored_resume_id: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(String)
    improvements: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class Application(Base):
    """A Kanban application-tracker card."""

    __tablename__ = "applications"
    __table_args__ = (
        # Concurrency-safe dedupe: a card is unique per (job, applied resume).
        # The app-level select-then-insert relies on this to collapse races.
        UniqueConstraint("job_id", "resume_id", name="uq_application_job_resume"),
    )

    application_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    # The applied/tailored resume shown in the modal and opened by "Edit".
    resume_id: Mapped[str] = mapped_column(String, index=True)
    # Optional base resume the tailored one descends from (powers "stack" grouping).
    master_resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="applied", index=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class ApiKey(Base):
    """An encrypted LLM provider API key.

    ``provider`` is the *key-store* provider name (e.g. ``google`` for the
    ``gemini`` LLM provider, via ``_PROVIDER_KEY_MAP``). Only ciphertext is
    stored; plaintext exists in memory only at call time.
    """

    __tablename__ = "api_keys"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class ExperienceItem(Base):
    """一条与简历文档解耦的个人经历记录。"""

    __tablename__ = "experience_items"

    experience_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, index=True)

    evidence_links: Mapped[list["ExperienceEvidence"]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        order_by="ExperienceEvidence.position",
        lazy="selectin",
        passive_deletes=True,
    )

class EvidenceItem(Base):
    """一条由经历引用的有序行动、结果和指标事实。"""

    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


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

    experience: Mapped[ExperienceItem] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceItem] = relationship(lazy="joined")


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
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="incomplete")
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


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
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
