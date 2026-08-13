"""Transactional business rules for manual JD import editing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.jd_import.models import JDInformation
from app.jd_import.repositories import JDImportRepository
from app.jd_import.schemas import (
    JDImportCreate,
    JDImportListResponse,
    JDImportResponse,
    JDInformationUpdate,
    JDRequirementCreate,
    JDRequirementUpdate,
)


class JDImportError(Exception):
    pass


class JDImportNotFoundError(JDImportError):
    pass


class JDImportConflictError(JDImportError):
    pass


class JDImportValidationError(JDImportError):
    pass


class JDImportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = JDImportRepository(session)

    async def create(self, request: JDImportCreate) -> JDImportResponse:
        try:
            information = await self._repository.create(
                origin_fields={
                    "raw_text": request.raw_text,
                    "source_url": request.source_url,
                },
                information_fields={
                    "company": request.company,
                    "job_name": request.job_name,
                    "type": request.type,
                    "location": request.location,
                    "status": request.status,
                    "revision": 0,
                },
                requirements=[item.model_dump() for item in request.requirements],
            )
            await self._session.commit()
            return JDImportResponse.model_validate(information)
        except Exception:
            await self._session.rollback()
            raise

    async def list(self) -> JDImportListResponse:
        items = await self._repository.list()
        return JDImportListResponse(
            items=[JDImportResponse.model_validate(item) for item in items],
            total=len(items),
        )

    async def get(self, information_id: int) -> JDImportResponse:
        return JDImportResponse.model_validate(await self._require(information_id))

    async def patch(
        self, information_id: int, request: JDInformationUpdate
    ) -> JDImportResponse:
        information = await self._require(information_id)
        fields = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        fields = {key: ("" if value is None else value) for key, value in fields.items()}
        if not fields:
            raise JDImportValidationError("no fields to update")
        if information.status == "confirmed" and fields != {"status": "analysing"}:
            raise JDImportConflictError(
                "confirmed import must be reopened before editing"
            )
        try:
            changed = await self._repository.update_information(
                information_id, request.expected_revision, fields
            )
            if not changed:
                raise JDImportConflictError("stale JD information revision")
            await self._session.commit()
            return await self.get(information_id)
        except Exception:
            await self._session.rollback()
            raise

    async def add_requirement(
        self, information_id: int, request: JDRequirementCreate
    ) -> JDImportResponse:
        await self._require(information_id)
        fields = request.model_dump(exclude={"expected_information_revision"})
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, request.expected_information_revision
            )
            if not advanced:
                raise JDImportConflictError(
                    "import is confirmed or its revision is stale"
                )
            await self._repository.create_requirement(information_id, fields)
            await self._session.commit()
            return await self.get(information_id)
        except Exception:
            await self._session.rollback()
            raise

    async def patch_requirement(
        self,
        information_id: int,
        requirement_id: int,
        request: JDRequirementUpdate,
    ) -> JDImportResponse:
        await self._require_owned_requirement(information_id, requirement_id)
        fields = request.model_dump(
            exclude={"expected_revision", "expected_information_revision"},
            exclude_unset=True,
        )
        if not fields:
            raise JDImportValidationError("no requirement fields to update")
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, request.expected_information_revision
            )
            if not advanced:
                raise JDImportConflictError(
                    "import is confirmed or its revision is stale"
                )
            changed = await self._repository.update_requirement(
                requirement_id, request.expected_revision, fields
            )
            if not changed:
                raise JDImportConflictError("stale JD requirement revision")
            await self._session.commit()
            return await self.get(information_id)
        except Exception:
            await self._session.rollback()
            raise

    async def delete_requirement(
        self,
        information_id: int,
        requirement_id: int,
        *,
        expected_revision: int,
        expected_information_revision: int,
    ) -> JDImportResponse:
        await self._require_owned_requirement(information_id, requirement_id)
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, expected_information_revision
            )
            if not advanced:
                raise JDImportConflictError(
                    "import is confirmed or its revision is stale"
                )
            deleted = await self._repository.delete_requirement(
                requirement_id, expected_revision
            )
            if not deleted:
                raise JDImportConflictError("stale JD requirement revision")
            await self._session.commit()
            return await self.get(information_id)
        except Exception:
            await self._session.rollback()
            raise

    async def delete(self, information_id: int) -> None:
        information = await self._require(information_id)
        try:
            await self._repository.delete(information)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def _require(self, information_id: int) -> JDInformation:
        information = await self._repository.get(information_id)
        if information is None:
            raise JDImportNotFoundError(
                f"JD information {information_id} does not exist"
            )
        return information

    async def _require_owned_requirement(
        self, information_id: int, requirement_id: int
    ) -> None:
        await self._require(information_id)
        requirement = await self._repository.get_requirement(requirement_id)
        if requirement is None or requirement.jd_information_id != information_id:
            raise JDImportNotFoundError(
                f"JD requirement {requirement_id} does not belong to import {information_id}"
            )
