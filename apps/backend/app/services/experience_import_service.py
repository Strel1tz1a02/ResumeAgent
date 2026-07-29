"""Immediate text persistence for experience-library imports."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.experiences import ExperienceCreate, ExperienceDetail, ExperienceKind
from app.services.experience_service import ExperienceService


class ExperienceImportService:
    """Persist pasted text immediately without any AI enrichment work."""

    def __init__(self, session: AsyncSession) -> None:
        self._experiences = ExperienceService(session)

    async def import_text(self, text: str) -> ExperienceDetail:
        """Store exact accepted user text as a deliberately incomplete draft."""
        return await self._experiences.create(
            ExperienceCreate(
                kind=ExperienceKind.other,
                title="",
                raw_input=text,
                technologies=[],
                tags=[],
            )
        )
