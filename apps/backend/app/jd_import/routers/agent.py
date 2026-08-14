"""JD 导入 Agent 的 SSE 接口。"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.ai_chat.container import get_ai_chat_service
from app.ai_chat.errors import (
    ConversationNotFoundError,
    IdempotencyConflictError,
    ProposalStateError,
)
from app.ai_chat.streaming.events import AiChatEvent
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.schemas import (
    JDConversationResponse,
    JDImportAgentRequest,
    JDQuestionResolutionRequest,
)

router = APIRouter(prefix="/jd-imports", tags=["JD Import Agent"])
_PREAMBLE = ":" + (" " * 2048) + "\n\n"


async def _stream(events: AsyncIterator[AiChatEvent]) -> AsyncIterator[str]:
    yield _PREAMBLE
    try:
        async for event in events:
            yield f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
    except Exception:  # noqa: BLE001 - public stream exposes only a stable code
        yield 'event: jd.import.failed\ndata: {"code":"graph_execution_failed"}\n\n'


def _sse(events: AsyncIterator[AiChatEvent]) -> StreamingResponse:
    return StreamingResponse(_stream(events), media_type="text/event-stream")


@router.post("/conversations", response_model=JDConversationResponse, status_code=201)
async def create_conversation() -> JDConversationResponse:
    conversation_id = await get_ai_chat_service().create_conversation(
        "JDImportAdapter", {"type": "jd_import", "id": "new"}, {}
    )
    return JDConversationResponse(conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/imports")
async def import_jd(conversation_id: int, request: JDImportAgentRequest) -> StreamingResponse:
    parse_mixed_input(request.content)
    return _sse(get_ai_chat_service().stream_message(
        conversation_id, request.content, request.client_message_id
    ))


@router.post("/conversations/{conversation_id}/question-batches/{batch_id}/resolve")
async def resolve_questions(
    conversation_id: int, batch_id: str, request: JDQuestionResolutionRequest
) -> StreamingResponse:
    answer = {**request.model_dump(mode="json"), "batch_id": batch_id}
    try:
        events = get_ai_chat_service().resolve_question_batch(conversation_id, batch_id, answer)
        return _sse(events)
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error
    except (IdempotencyConflictError, ProposalStateError) as error:
        raise HTTPException(status_code=409, detail="question batch conflict") from error
