"""个人经历库的 HTTP 接口。"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat import get_ai_chat_service
from app.experience.repositories.session import get_repository_session
from app.experience.schemas.evidence_items import (
    EvidenceCreateRequest,
    EvidenceReorder,
    EvidenceUpdate,
)
from app.experience.schemas.experiences import (
    DeletionImpactResponse,
    ExperienceCreate,
    ExperienceDetail,
    ExperienceGlobalSave,
    ExperienceImportTextRequest,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceUpdate,
    ReadyConflictResponse,
)
from app.experience.services.evidence_service import EvidenceService
from app.experience.services.experience_global_save_service import (
    ExperienceGlobalSaveService,
)
from app.experience.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceReadyConflictError,
    ExperienceService,
    ExperienceValidationError,
)
from app.experience.services.experience_text_extractor import (
    ExperienceTextExtractionError,
    ExperienceTextExtractor,
)

router = APIRouter(prefix="/experiences", tags=["Experience Library"])
Session = Annotated[
    AsyncSession, Depends(get_repository_session)
]  # 避免每个接口重复写：Annotated[AsyncSession, Depends(get_repository_session)]
logger = logging.getLogger(__name__)


def _raise_domain_error(error: Exception) -> None:
    if isinstance(error, ExperienceNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, ExperienceConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ExperienceValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post(
    "/import-text/preview",
    response_model=ExperienceGlobalSave,
    response_model_exclude_none=True,
)
async def preview_import_text(
    request: ExperienceImportTextRequest,
) -> ExperienceGlobalSave:
    """把临时文本解析为对象，不创建任何数据库记录。"""
    try:
        return await ExperienceTextExtractor().extract(request.text)
    except ExperienceTextExtractionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post(
    "/save",
    response_model=ExperienceDetail,
)
async def save_experience(
    request: ExperienceGlobalSave, session: Session
) -> ExperienceDetail:
    """有 ID 覆盖、无 ID 创建，并原子保存整个经历聚合。"""
    try:
        return await ExperienceGlobalSaveService(session).save(request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.post("", response_model=ExperienceDetail, status_code=status.HTTP_201_CREATED)
async def create_experience(
    request: ExperienceCreate, session: Session
) -> ExperienceDetail:
    """创建一条手动录入的经历草稿。"""
    try:
        return await ExperienceService(session).create(request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.get("", response_model=ExperienceListResponse)
async def list_experiences(
    session: Session,
    query: Annotated[ExperienceListQuery, Depends()],
) -> ExperienceListResponse:
    """使用稳定的筛选和排序方式列出本地经历库。"""
    try:
        return await ExperienceService(session).list(query)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.get("/{experience_id}", response_model=ExperienceDetail)
async def get_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """获取一条经历，并按保存顺序展开证据。"""
    try:
        return await ExperienceService(session).get(experience_id)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.patch("/{experience_id}", response_model=ExperienceDetail)
async def patch_experience(
    experience_id: int,
    request: ExperienceUpdate,
    session: Session,
) -> ExperienceDetail:
    """应用手动编辑并重新计算持久化完整度。"""
    try:
        return await ExperienceService(session).patch(experience_id, request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/mark-ready", response_model=ExperienceDetail)
async def mark_ready(experience_id: int, session: Session) -> ExperienceDetail:
    """将完整的活动经历标记为就绪，供后续简历使用。"""
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
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/archive", response_model=ExperienceDetail)
async def archive_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """将经历归档，作为可恢复的普通删除操作。"""
    try:
        return await ExperienceService(session).archive(experience_id)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.post("/{experience_id}/restore", response_model=ExperienceDetail)
async def restore_experience(experience_id: int, session: Session) -> ExperienceDetail:
    """将已归档经历恢复为草稿状态。"""
    try:
        return await ExperienceService(session).restore(experience_id)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.get("/{experience_id}/deletion-impact", response_model=DeletionImpactResponse)
async def deletion_impact(
    experience_id: int, session: Session
) -> DeletionImpactResponse:
    """预览待永久删除操作的稳定影响结构。"""
    try:
        return await ExperienceService(session).deletion_impact(experience_id)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.delete("/{experience_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_experience(
    experience_id: int, session: Session
) -> Response:
    """确认影响后永久删除已归档经历。"""
    try:
        await ExperienceService(session).permanently_delete(experience_id)
        try:
            await get_ai_chat_service().delete_subject(
                "ExperienceAdapter", {"type": "experience", "id": str(experience_id)}
            )
        except Exception:
            logger.exception(
                "Experience deleted but AI Chat cleanup failed: experience=%s",
                experience_id,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/{experience_id}/evidence",
    response_model=ExperienceDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_evidence(
    experience_id: int, request: EvidenceCreateRequest, session: Session
) -> ExperienceDetail:
    """追加一条所属证据，并返回刷新后的经历详情。"""
    try:
        return await EvidenceService(session).create(experience_id, request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.patch(
    "/{experience_id}/evidence/{evidence_id}", response_model=ExperienceDetail
)
async def patch_evidence(
    experience_id: int,
    evidence_id: int,
    request: EvidenceUpdate,
    session: Session,
) -> ExperienceDetail:
    """只能通过证据所属经历编辑该证据。"""
    try:
        return await EvidenceService(session).patch(experience_id, evidence_id, request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.delete(
    "/{experience_id}/evidence/{evidence_id}", response_model=ExperienceDetail
)
async def delete_evidence(
    experience_id: int,
    evidence_id: int,
    session: Session,
    expected_revision: int,
    expected_collection_revision: int,
) -> ExperienceDetail:
    """删除所属证据，并原子移除其关系表归属。"""
    try:
        return await EvidenceService(session).delete(
            experience_id,
            evidence_id,
            expected_revision=expected_revision,
            expected_collection_revision=expected_collection_revision,
        )
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)


@router.put("/{experience_id}/evidence-order", response_model=ExperienceDetail)
async def reorder_evidence(
    experience_id: int,
    request: EvidenceReorder,
    session: Session,
) -> ExperienceDetail:
    """仅当客户端顺序是当前证据 ID 的完整排列时才持久化。"""
    try:
        return await EvidenceService(session).reorder(experience_id, request)
    except (
        ExperienceNotFoundError,
        ExperienceConflictError,
        ExperienceValidationError,
    ) as error:
        _raise_domain_error(error)
