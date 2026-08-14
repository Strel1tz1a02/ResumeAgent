"""Persistence operations for a JD import aggregate."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.jd_import.models import JDInformation, JDRequirement


class JDImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        information_fields: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> JDInformation:
        information = JDInformation(**information_fields)
        information.requirements = [JDRequirement(**item) for item in requirements]
        self._session.add(information)
        await self._session.flush()
        return information

    async def get(self, information_id: int) -> JDInformation | None:
        statement = select(JDInformation).where(JDInformation.id == information_id)
        return (await self._session.scalars(statement)).first()

    async def list(self) -> list[JDInformation]:
        statement = select(JDInformation).order_by(JDInformation.id.desc())
        return list((await self._session.scalars(statement)).all())

    async def update_information(
        self, information_id: int, expected_revision: int, fields: dict[str, Any]
    ) -> bool:
        result = await self._session.execute(
            update(JDInformation)
            .where(
                JDInformation.id == information_id,
                JDInformation.revision == expected_revision,
            )
            .values(**fields, revision=JDInformation.revision + 1)
        )
        await self._session.flush()
        return result.rowcount == 1

    async def advance_information_revision(
        self, information_id: int, expected_revision: int
    ) -> bool:
        result = await self._session.execute(
            update(JDInformation)
            .where(
                JDInformation.id == information_id,
                JDInformation.revision == expected_revision,
            )
            .values(revision=JDInformation.revision + 1)
        )
        await self._session.flush()
        return result.rowcount == 1

    async def create_requirement(
        self, information_id: int, fields: dict[str, Any]
    ) -> JDRequirement:
        item = JDRequirement(jd_information_id=information_id, **fields)
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_requirement(self, requirement_id: int) -> JDRequirement | None:
        return await self._session.get(JDRequirement, requirement_id)

    async def update_requirement(
        self, requirement_id: int, expected_revision: int, fields: dict[str, Any]
    ) -> bool:
        result = await self._session.execute(
            update(JDRequirement)
            .where(
                JDRequirement.id == requirement_id,
                JDRequirement.revision == expected_revision,
            )
            .values(**fields, revision=JDRequirement.revision + 1)
        )
        await self._session.flush()
        return result.rowcount == 1

    async def delete_requirement(
        self, requirement_id: int, expected_revision: int
    ) -> bool:
        result = await self._session.execute(
            delete(JDRequirement).where(
                JDRequirement.id == requirement_id,
                JDRequirement.revision == expected_revision,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def delete(self, information: JDInformation) -> None:
        await self._session.delete(information)
        await self._session.flush()
