"""HTTP API for independent, manually editable JD analyses."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.repositories.session import get_repository_session
from app.jd_analysis.schemas import (
    JDAnalysisCreate,
    JDAnalysisListResponse,
    JDAnalysisResponse,
    JDInformationUpdate,
    JDRequirementCreate,
    JDRequirementUpdate,
)
from app.jd_analysis.services import (
    JDAnalysisConflictError,
    JDAnalysisNotFoundError,
    JDAnalysisService,
    JDAnalysisValidationError,
)

router = APIRouter(prefix="/jd-analyses", tags=["JD Analysis"])
Session = Annotated[AsyncSession, Depends(get_repository_session)]


def _raise_domain_error(error: Exception) -> None:
    if isinstance(error, JDAnalysisNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, JDAnalysisConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, JDAnalysisValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post("", response_model=JDAnalysisResponse, status_code=201)
async def create_analysis(request: JDAnalysisCreate, session: Session):
    return await JDAnalysisService(session).create(request)


@router.get("", response_model=JDAnalysisListResponse)
async def list_analyses(session: Session):
    return await JDAnalysisService(session).list()


@router.get("/{information_id}", response_model=JDAnalysisResponse)
async def get_analysis(information_id: int, session: Session):
    try:
        return await JDAnalysisService(session).get(information_id)
    except JDAnalysisNotFoundError as error:
        _raise_domain_error(error)


@router.patch("/{information_id}", response_model=JDAnalysisResponse)
async def patch_analysis(
    information_id: int, request: JDInformationUpdate, session: Session
):
    try:
        return await JDAnalysisService(session).patch(information_id, request)
    except (JDAnalysisNotFoundError, JDAnalysisConflictError, JDAnalysisValidationError) as error:
        _raise_domain_error(error)


@router.delete("/{information_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_analysis(information_id: int, session: Session) -> Response:
    try:
        await JDAnalysisService(session).delete(information_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except JDAnalysisNotFoundError as error:
        _raise_domain_error(error)


@router.post(
    "/{information_id}/requirements",
    response_model=JDAnalysisResponse,
    status_code=201,
)
async def add_requirement(
    information_id: int, request: JDRequirementCreate, session: Session
):
    try:
        return await JDAnalysisService(session).add_requirement(information_id, request)
    except (JDAnalysisNotFoundError, JDAnalysisConflictError) as error:
        _raise_domain_error(error)


@router.patch(
    "/{information_id}/requirements/{requirement_id}",
    response_model=JDAnalysisResponse,
)
async def patch_requirement(
    information_id: int,
    requirement_id: int,
    request: JDRequirementUpdate,
    session: Session,
):
    try:
        return await JDAnalysisService(session).patch_requirement(
            information_id, requirement_id, request
        )
    except (JDAnalysisNotFoundError, JDAnalysisConflictError, JDAnalysisValidationError) as error:
        _raise_domain_error(error)


@router.delete(
    "/{information_id}/requirements/{requirement_id}",
    response_model=JDAnalysisResponse,
)
async def delete_requirement(
    information_id: int,
    requirement_id: int,
    session: Session,
    expected_revision: int,
    expected_information_revision: int,
):
    try:
        return await JDAnalysisService(session).delete_requirement(
            information_id,
            requirement_id,
            expected_revision=expected_revision,
            expected_information_revision=expected_information_revision,
        )
    except (JDAnalysisNotFoundError, JDAnalysisConflictError) as error:
        _raise_domain_error(error)
