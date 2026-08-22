"""JD 导入 Agent 的 SSE 接口。"""

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.ai_chat.container import get_ai_chat_service
from app.ai_chat.protocol import ResolveInteractionCommand
from app.ai_chat.streaming.sse import runtime_sse_response
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.schemas import (
    JDConversationResponse,
    JDImportAgentRequest,
    JDQuestionResolutionRequest,
)

router = APIRouter(prefix="/jd-imports", tags=["JD Import Agent"])
logger = logging.getLogger(__name__)


def _sse(events) -> StreamingResponse:  # type: ignore[no-untyped-def]
    return runtime_sse_response(
        events,
        logger=logger,
    )


@router.post("/conversations", response_model=JDConversationResponse, status_code=201)
async def create_conversation() -> JDConversationResponse:
    conversation_id = await get_ai_chat_service().create_conversation(
        "JDImportAdapter", {"type": "jd_import", "id": "new"}, {}
    )
    return JDConversationResponse(conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/imports")
async def import_jd(
    conversation_id: int,
    request: JDImportAgentRequest,
) -> StreamingResponse:
    parse_mixed_input(request.content)
    return _sse(
        get_ai_chat_service().stream_message(
            conversation_id,
            request.content,
            request.client_message_id,
        )
    )


@router.post("/runs/{run_id}/interactions/{interaction_id}/resolve")
async def resolve_questions(
    run_id: int,
    interaction_id: int,
    request: JDQuestionResolutionRequest,
) -> StreamingResponse:
    events = get_ai_chat_service().resolve_interaction(
        ResolveInteractionCommand(
            run_id=run_id,
            interaction_id=interaction_id,
            kind="question_batch",
            client_resolution_id=request.client_resolution_id,
            payload=request.model_dump(
                mode="json", exclude={"client_resolution_id"}
            ),
        )
    )
    return _sse(events)
