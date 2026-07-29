"""HTTP endpoints for the person-level experience library."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.session import get_repository_session
from app.schemas.experiences import (
    DeletionImpactResponse,
    ExperienceCreate,
    ExperienceEnrichmentAnswerRequest,
    ExperienceEnrichmentAnswerResponse,
    ExperienceEnrichmentQuestion,
    ExperienceDetail,
    ExperienceImportTextRequest,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceUpdate,
    ReadyConflictResponse,
)
from app.schemas.evidence_items import EvidenceCreate, EvidenceReorder, EvidenceUpdate
from app.services.evidence_service import EvidenceService
from app.services.experience_import_service import ExperienceImportService
from app.services.experience_enrichment_service import (
    EnrichmentRetryableError,
    ExperienceEnrichmentService,
    InvalidEnrichmentPatch,
)
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceReadyConflictError,
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


@router.post("/{experience_id}/questions/next", response_model=ExperienceEnrichmentQuestion)
async def next_enrichment_question(
    experience_id: int, session: Session
) -> ExperienceEnrichmentQuestion:
    """Generate one stateless factual follow-up without persisting a chat transcript."""
    try:
        return await ExperienceEnrichmentService(session).next_question(experience_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/answers", response_model=ExperienceEnrichmentAnswerResponse)
async def apply_enrichment_answer(
    experience_id: int,
    request: ExperienceEnrichmentAnswerRequest,
    session: Session,
) -> ExperienceEnrichmentAnswerResponse:
    """Apply one typed answer patch atomically; conversation history is never stored."""
    try:
        return await ExperienceEnrichmentService(session).apply_answer(
            experience_id, request.question_id, request.answer
        )
    except EnrichmentRetryableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (InvalidEnrichmentPatch, ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
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


@router.post("/{experience_id}/mark-ready", response_model=ExperienceDetail)
async def mark_ready(experience_id: int, session: Session) -> ExperienceDetail:
    """Mark a complete active experience ready for later resume use."""
    try:
        return await ExperienceService(session).mark_ready(experience_id)
    except ExperienceReadyConflictError as error:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ReadyConflictResponse(
                completeness=error.completeness,
                missing_dimensions=error.missing_dimensions,
            ).model_dump(),
        )
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/archive", response_model=ExperienceDetail)
async def archive_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """Archive an experience as its reversible normal-delete action."""
    try:
        return await ExperienceService(session).archive(experience_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/restore", response_model=ExperienceDetail)
async def restore_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """Restore an archived experience to draft state."""
    try:
        return await ExperienceService(session).restore(experience_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.get("/{experience_id}/deletion-impact", response_model=DeletionImpactResponse)
async def deletion_impact(experience_id: int, session: Session) -> DeletionImpactResponse:
    """Preview the stable impact shape for a pending permanent deletion."""
    try:
        return await ExperienceService(session).deletion_impact(experience_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.delete("/{experience_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_experience(experience_id: int, session: Session) -> Response:
    """Irreversibly delete an archived experience after its impact has been reviewed."""
    try:
        await ExperienceService(session).permanently_delete(experience_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.post(
    "/{experience_id}/evidence",
    response_model=ExperienceDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_evidence(
    experience_id: int, request: EvidenceCreate, session: Session
) -> ExperienceDetail:
    """Append one owned evidence fact and return the refreshed experience detail."""
    try:
        return await EvidenceService(session).create(experience_id, request)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.patch("/{experience_id}/evidence/{evidence_id}", response_model=ExperienceDetail)
async def patch_evidence(
    experience_id: int,
    evidence_id: int,
    request: EvidenceUpdate,
    session: Session,
) -> ExperienceDetail:
    """Edit an evidence fact only through its owning experience."""
    try:
        return await EvidenceService(session).patch(experience_id, evidence_id, request)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.delete("/{experience_id}/evidence/{evidence_id}", response_model=ExperienceDetail)
async def delete_evidence(experience_id: int, evidence_id: int, session: Session) -> ExperienceDetail:
    """Delete an owned evidence fact and remove its JSON reference atomically."""
    try:
        return await EvidenceService(session).delete(experience_id, evidence_id)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)


@router.put("/{experience_id}/evidence-order", response_model=ExperienceDetail)
async def reorder_evidence(
    experience_id: int,
    request: EvidenceReorder,
    session: Session,
) -> ExperienceDetail:
    """Persist a client order only when it is a permutation of the current evidence IDs."""
    try:
        return await EvidenceService(session).reorder(experience_id, request)
    except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError) as error:
        _raise_domain_error(error)
