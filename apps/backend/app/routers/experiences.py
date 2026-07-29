"""HTTP endpoints for the person-level experience library."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.session import get_repository_session
from app.schemas.experiences import (
    ExperienceCreate,
    ExperienceDetail,
    ExperienceImportTextRequest,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceUpdate,
)
from app.services.experience_import_service import ExperienceImportService
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)

router = APIRouter(prefix="/experiences", tags=["Experience Library"])
Session = Annotated[AsyncSession, Depends(get_repository_session)]


def _raise_domain_error(error: Exception) -> None:
    if isinstance(error, ExperienceNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, ExperienceConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ExperienceValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post("/import-text", response_model=ExperienceDetail, status_code=status.HTTP_201_CREATED)
async def import_text(request: ExperienceImportTextRequest, session: Session) -> ExperienceDetail:
    """Persist accepted pasted text before any optional later enrichment workflow."""
    try:
        return await ExperienceImportService(session).import_text(request.text)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.post("", response_model=ExperienceDetail, status_code=status.HTTP_201_CREATED)
async def create_experience(request: ExperienceCreate, session: Session) -> ExperienceDetail:
    """Create one manually entered experience draft."""
    try:
        return await ExperienceService(session).create(request)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.get("", response_model=ExperienceListResponse)
async def list_experiences(
    session: Session,
    query: Annotated[ExperienceListQuery, Depends()],
) -> ExperienceListResponse:
    """List the local experience library using its stable filters and sort modes."""
    try:
        return await ExperienceService(session).list(query)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.get("/{experience_id}", response_model=ExperienceDetail)
async def get_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """Fetch one experience with expanded ordered evidence."""
    try:
        return await ExperienceService(session).get(experience_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.patch("/{experience_id}", response_model=ExperienceDetail)
async def patch_experience(
    experience_id: int,
    request: ExperienceUpdate,
    session: Session,
) -> ExperienceDetail:
    """Apply a manual edit and recompute persisted completeness."""
    try:
        return await ExperienceService(session).patch(experience_id, request)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)
