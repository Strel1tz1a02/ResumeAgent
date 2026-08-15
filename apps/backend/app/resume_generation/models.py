"""简历生成运行的持久化模型。"""

from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class ResumeGenerationRun(Base):
    __tablename__ = "resume_generation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'previewed', 'failed', 'confirmed')",
            name="ck_resume_generation_run_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    jd_information_id: Mapped[int] = mapped_column(Integer, index=True)
    request_json: Mapped[dict] = mapped_column(JSON)
    jd_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    experience_snapshots_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resume_data_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    generated_resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
