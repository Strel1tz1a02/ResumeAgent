"""经历业务专用 AI Chat Router 与 SSE 映射。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app import database as database_module
from app.ai_chat.container import get_ai_chat_service
from app.ai_chat.streaming.events import AiChatEvent
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

# 部分反向代理会在收到足够字节前攒住小型响应块。先发送一段 SSE 注释，
# 让代理尽早建立流式传输；注释不会被前端当作业务事件处理。
_SSE_PREAMBLE = ":" + (" " * 2048) + "\n\n"


def _encode_sse(event: AiChatEvent) -> str:
    """将内部事件编码为一条原子 SSE 记录。"""
    return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


def _business_event(event: AiChatEvent) -> AiChatEvent | None:
    """隐藏通用传输事件并映射成经历业务事件名。"""
    if event.event == "proposal.requested":
        tool_name = event.data.get("tool_name")
        if isinstance(tool_name, str):
            return AiChatEvent(f"{tool_name}.requested", event.data)
    if event.event == "run.failed":
        code = event.data.get("code")
        if code not in {"context_full", "memory_compaction_failed"}:
            code = "response_failed"
        payload = {"code": code}
        reason = event.data.get("reason")
        if isinstance(reason, str):
            payload["reason"] = reason
        return AiChatEvent("chat.error", payload)
    return event


async def _stream(events: AsyncIterator[AiChatEvent]) -> AsyncIterator[str]:
    """消费通用流；异常只记录服务端并返回稳定业务错误。"""
    yield _SSE_PREAMBLE
    try:
        async for event in events:
            mapped = _business_event(event)
            if mapped is not None:
                yield _encode_sse(mapped)
    except Exception:
        logger.exception("Experience AI Chat stream failed")
        yield _encode_sse(AiChatEvent("chat.error", {"code": "response_failed"}))


def _sse(events: AsyncIterator[AiChatEvent]) -> StreamingResponse:
    """创建禁止代理缓冲的 SSE 响应。"""
    return StreamingResponse(
        _stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/conversations",
    response_model=ConversationCreateResponse, # 自动将返回值转Json
    status_code=status.HTTP_201_CREATED, # 成功时返回 201
)
async def create_conversation( request: ConversationCreateRequest,) -> ConversationCreateResponse:
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
async def stream_message( conversation_id: int, request: MessageRequest) -> StreamingResponse:
    """发送一条用户消息并返回本轮 SSE。"""
    return _sse(
        get_ai_chat_service().stream_message(conversation_id, request.content, request.client_message_id)
    )


@router.post("/proposals/{proposal_id}/resolve")
async def resolve_proposal(
    proposal_id: int, request: ProposalResolutionRequest
) -> StreamingResponse:
    """审批后开启新 SSE，恢复相同 checkpoint 并完成无模型收尾。"""
    return _sse(
        get_ai_chat_service().resolve_proposal(
            proposal_id, request.decision, request.client_resolution_id
        )
    )


@router.post("/conversations/{conversation_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_conversation(
    conversation_id: int, request: ConversationCloseRequest
) -> None:
    """结束当前会话；之后不能恢复或继续。"""
    await get_ai_chat_service().close_conversation(conversation_id, request.reason)
