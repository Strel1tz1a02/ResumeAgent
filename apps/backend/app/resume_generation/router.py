"""简历生成预览、读取和确认 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.repositories.session import get_repository_session
from app.resume_generation.schemas import (
    ResumeGenerationConfirmRequest,
    ResumeGenerationConfirmResponse,
    ResumeGenerationPreview,
    ResumeGenerationRequest,
    ResumeGenerationRunResponse,
)
from app.resume_generation.service import (
    ResumeGenerationConflictError,
    ResumeGenerationError,
    ResumeGenerationNotFoundError,
    ResumeGenerationService,
    ResumeGenerationValidationError,
)

router = APIRouter(prefix="/resume-generations", tags=["Resume Generation"])
Session = Annotated[AsyncSession, Depends(get_repository_session)]


def _service(session: AsyncSession) -> ResumeGenerationService:
    return ResumeGenerationService(session)


def _raise(error: Exception) -> None:
    if isinstance(error, ResumeGenerationNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, ResumeGenerationValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, ResumeGenerationConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ResumeGenerationError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    raise error


@router.post(
    "/preview",
    response_model=ResumeGenerationPreview,
    status_code=status.HTTP_201_CREATED,
)
async def preview_resume_generation(
    request: ResumeGenerationRequest,
    session: Session,
) -> ResumeGenerationPreview:
    try:
        return await _service(session).preview(request)
    except ResumeGenerationError as error:
        _raise(error)


@router.get("/{run_id}", response_model=ResumeGenerationRunResponse)
async def get_resume_generation(
    run_id: str,
    session: Session,
) -> ResumeGenerationRunResponse:
    try:
        return await _service(session).get(run_id)
    except ResumeGenerationError as error:
        _raise(error)


@router.post(
    "/{run_id}/confirm",
    response_model=ResumeGenerationConfirmResponse,
)
async def confirm_resume_generation(
    run_id: str,
    request: ResumeGenerationConfirmRequest,
    session: Session,
) -> ResumeGenerationConfirmResponse:
    try:
        return await _service(session).confirm(run_id, request)
    except ResumeGenerationError as error:
        _raise(error)
