"""Transactional business rules for manual JD analysis editing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.jd_analysis.models import JDInformation
from app.jd_analysis.repositories import JDAnalysisRepository
from app.jd_analysis.schemas import (
    JDAnalysisCreate,
    JDAnalysisListResponse,
    JDAnalysisResponse,
    JDInformationUpdate,
    JDRequirementCreate,
    JDRequirementUpdate,
)


class JDAnalysisError(Exception):
    pass


class JDAnalysisNotFoundError(JDAnalysisError):
    pass


class JDAnalysisConflictError(JDAnalysisError):
    pass


class JDAnalysisValidationError(JDAnalysisError):
    pass


class JDAnalysisService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = JDAnalysisRepository(session)

    async def create(self, request: JDAnalysisCreate) -> JDAnalysisResponse:
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
            return JDAnalysisResponse.model_validate(information)
        except Exception:
            await self._session.rollback()
            raise

    async def list(self) -> JDAnalysisListResponse:
        items = await self._repository.list()
        return JDAnalysisListResponse(
            items=[JDAnalysisResponse.model_validate(item) for item in items],
            total=len(items),
        )

    async def get(self, information_id: int) -> JDAnalysisResponse:
        return JDAnalysisResponse.model_validate(await self._require(information_id))

    async def patch(
        self, information_id: int, request: JDInformationUpdate
    ) -> JDAnalysisResponse:
        information = await self._require(information_id)
        fields = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        fields = {key: ("" if value is None else value) for key, value in fields.items()}
        if not fields:
            raise JDAnalysisValidationError("no fields to update")
        if information.status == "confirmed" and fields != {"status": "analysing"}:
            raise JDAnalysisConflictError(
                "confirmed analysis must be reopened before editing"
            )
        try:
            changed = await self._repository.update_information(
                information_id, request.expected_revision, fields
            )
            if not changed:
                raise JDAnalysisConflictError("stale JD information revision")
            await self._session.commit()
            return await self.get(information_id)
        except Exception:
            await self._session.rollback()
            raise

    async def add_requirement(
        self, information_id: int, request: JDRequirementCreate
    ) -> JDAnalysisResponse:
        await self._require(information_id)
        fields = request.model_dump(exclude={"expected_information_revision"})
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, request.expected_information_revision
            )
            if not advanced:
                raise JDAnalysisConflictError(
                    "analysis is confirmed or its revision is stale"
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
    ) -> JDAnalysisResponse:
        await self._require_owned_requirement(information_id, requirement_id)
        fields = request.model_dump(
            exclude={"expected_revision", "expected_information_revision"},
            exclude_unset=True,
        )
        if not fields:
            raise JDAnalysisValidationError("no requirement fields to update")
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, request.expected_information_revision
            )
            if not advanced:
                raise JDAnalysisConflictError(
                    "analysis is confirmed or its revision is stale"
                )
            changed = await self._repository.update_requirement(
                requirement_id, request.expected_revision, fields
            )
            if not changed:
                raise JDAnalysisConflictError("stale JD requirement revision")
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
    ) -> JDAnalysisResponse:
        await self._require_owned_requirement(information_id, requirement_id)
        try:
            advanced = await self._repository.advance_information_revision(
                information_id, expected_information_revision
            )
            if not advanced:
                raise JDAnalysisConflictError(
                    "analysis is confirmed or its revision is stale"
                )
            deleted = await self._repository.delete_requirement(
                requirement_id, expected_revision
            )
            if not deleted:
                raise JDAnalysisConflictError("stale JD requirement revision")
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
            raise JDAnalysisNotFoundError(
                f"JD information {information_id} does not exist"
            )
        return information

    async def _require_owned_requirement(
        self, information_id: int, requirement_id: int
    ) -> None:
        await self._require(information_id)
        requirement = await self._repository.get_requirement(requirement_id)
        if requirement is None or requirement.jd_information_id != information_id:
            raise JDAnalysisNotFoundError(
                f"JD requirement {requirement_id} does not belong to analysis {information_id}"
            )
