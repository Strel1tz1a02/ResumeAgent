"""HTTP API for independent, manually editable JD imports."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.repositories.session import get_repository_session
from app.jd_import.schemas import (
    JDImportCreate,
    JDImportListResponse,
    JDImportResponse,
    JDInformationUpdate,
    JDRequirementCreate,
    JDRequirementUpdate,
)
from app.jd_import.services import (
    JDImportConflictError,
    JDImportNotFoundError,
    JDImportService,
    JDImportValidationError,
)

router = APIRouter(prefix="/jd-imports", tags=["JD Imports"])
Session = Annotated[AsyncSession, Depends(get_repository_session)]


def _raise_domain_error(error: Exception) -> None:
    if isinstance(error, JDImportNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, JDImportConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, JDImportValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post("", response_model=JDImportResponse, status_code=201)
async def create_import(request: JDImportCreate, session: Session):
    return await JDImportService(session).create(request)


@router.get("", response_model=JDImportListResponse)
async def list_imports(session: Session):
    return await JDImportService(session).list()


@router.get("/{information_id}", response_model=JDImportResponse)
async def get_import(information_id: int, session: Session):
    try:
        return await JDImportService(session).get(information_id)
    except JDImportNotFoundError as error:
        _raise_domain_error(error)


@router.patch("/{information_id}", response_model=JDImportResponse)
async def patch_import(
    information_id: int, request: JDInformationUpdate, session: Session
):
    try:
        return await JDImportService(session).patch(information_id, request)
    except (
        JDImportNotFoundError,
        JDImportConflictError,
        JDImportValidationError,
    ) as error:
        _raise_domain_error(error)


@router.delete("/{information_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import(information_id: int, session: Session) -> Response:
    try:
        await JDImportService(session).delete(information_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except JDImportNotFoundError as error:
        _raise_domain_error(error)


@router.post(
    "/{information_id}/requirements",
    response_model=JDImportResponse,
    status_code=201,
)
async def add_requirement(
    information_id: int, request: JDRequirementCreate, session: Session
):
    try:
        return await JDImportService(session).add_requirement(information_id, request)
    except (JDImportNotFoundError, JDImportConflictError) as error:
        _raise_domain_error(error)


@router.patch(
    "/{information_id}/requirements/{requirement_id}",
    response_model=JDImportResponse,
)
async def patch_requirement(
    information_id: int,
    requirement_id: int,
    request: JDRequirementUpdate,
    session: Session,
):
    try:
        return await JDImportService(session).patch_requirement(
            information_id, requirement_id, request
        )
    except (
        JDImportNotFoundError,
        JDImportConflictError,
        JDImportValidationError,
    ) as error:
        _raise_domain_error(error)


@router.delete(
    "/{information_id}/requirements/{requirement_id}",
    response_model=JDImportResponse,
)
async def delete_requirement(
    information_id: int,
    requirement_id: int,
    session: Session,
    expected_revision: int,
    expected_information_revision: int,
):
    try:
        return await JDImportService(session).delete_requirement(
            information_id,
            requirement_id,
            expected_revision=expected_revision,
            expected_information_revision=expected_information_revision,
        )
    except (JDImportNotFoundError, JDImportConflictError) as error:
        _raise_domain_error(error)
