"""经历业务专用 AI Chat Router 与 SSE 映射。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app import database as database_module
from app.ai_chat.container import get_ai_chat_service
from app.ai_chat.protocol import ResolveInteractionCommand
from app.ai_chat.streaming.sse import runtime_sse_response
from app.config_cache import get_content_language
from app.experience.schemas import (
    ConversationCloseRequest,
    ConversationCreateRequest,
    ConversationCreateResponse,
    MessageRequest,
    ProposalResolutionRequest,
)
from app.experience.services.experience_field_service import ExperienceFieldService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/experience-ai-chat", tags=["experience-ai-chat"])


def _sse(events) -> StreamingResponse:  # type: ignore[no-untyped-def]
    """创建禁止代理缓冲的 SSE 响应。"""
    return runtime_sse_response(
        events,
        logger=logger,
    )


@router.post(
    "/conversations",
    response_model=ConversationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    request: ConversationCreateRequest,
) -> ConversationCreateResponse:
    """验证字段绑定并创建不可恢复的当前会话。"""
    try:
        service = get_ai_chat_service()
        scope = request.scope.model_dump(mode="json")
        conversation_id = await service.create_conversation(
            adapter_name="ExperienceAdapter",
            subject={"type": "experience", "id": str(request.experience_id)},
            scope=scope,
            language=get_content_language(),
        )
        async with database_module.db.session() as session:
            snapshot_key = (
                "evidence_new"
                if request.scope.field == "evidence"
                else request.scope.field
            )
            snapshot = await ExperienceFieldService(session).snapshot(
                request.experience_id, snapshot_key, None
            )
        return ConversationCreateResponse(
            conversation_id=conversation_id,
            scope=request.scope,
            field_status=snapshot.status,
            revision=snapshot.revision,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="AI chat is unavailable") from error


@router.post("/conversations/{conversation_id}/opening")
async def stream_opening(conversation_id: int) -> StreamingResponse:
    """流式执行字段会话开场白。"""
    return _sse(get_ai_chat_service().stream_opening(conversation_id))


@router.post("/conversations/{conversation_id}/messages")
async def stream_message(
    conversation_id: int,
    request: MessageRequest,
) -> StreamingResponse:
    """发送一条用户消息并返回本轮 SSE。"""
    return _sse(
        get_ai_chat_service().stream_message(
            conversation_id,
            request.content,
            request.client_message_id,
        )
    )


@router.post("/runs/{run_id}/interactions/{interaction_id}/resolve")
async def resolve_interaction(
    run_id: int,
    interaction_id: int,
    request: ProposalResolutionRequest,
) -> StreamingResponse:
    """通过统一 Interaction 命令固化审批并恢复所属 Graph。"""
    return _sse(
        get_ai_chat_service().resolve_interaction(
            ResolveInteractionCommand(
                run_id=run_id,
                interaction_id=interaction_id,
                kind="approval",
                client_resolution_id=request.client_resolution_id,
                payload={"decision": request.decision},
            )
        )
    )


@router.post(
    "/conversations/{conversation_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def close_conversation(
    conversation_id: int, request: ConversationCloseRequest
) -> None:
    """结束当前会话；之后不能恢复或继续。"""
    await get_ai_chat_service().close_conversation(conversation_id, request.reason)
