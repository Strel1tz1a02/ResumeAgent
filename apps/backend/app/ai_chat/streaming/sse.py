"""Runtime Event 的唯一 SSE 编码和错误收敛实现。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from app.ai_chat.streaming.events import RuntimeEvent

_SSE_PREAMBLE = ":" + (" " * 2048) + "\n\n"


def encode_runtime_event(event: RuntimeEvent, sequence: int) -> str:
    """将一个 Runtime Event 编码为包含统一信封的原子 SSE 记录。"""
    sequenced = RuntimeEvent(
        type=event.type,
        payload=dict(event.payload),
        run_id=event.run_id,
        sequence=event.sequence if event.sequence is not None else sequence,
    )
    return (
        f"event: {sequenced.type}\n"
        f"data: {json.dumps(sequenced.envelope(), ensure_ascii=False)}\n\n"
    )


async def stream_runtime_events(
    events: AsyncIterator[RuntimeEvent],
    *,
    logger: logging.Logger,
) -> AsyncIterator[str]:
    """统一消费 Runtime 流，并把未处理异常收敛为 ``run.failed``。"""
    yield _SSE_PREAMBLE
    sequence = 0
    current_run_id: int | str | None = None
    try:
        async for event in events:
            sequence += 1
            if event.run_id is not None:
                current_run_id = event.run_id
            yield encode_runtime_event(event, sequence)
    except Exception as error:  # noqa: BLE001 - 公共流只暴露稳定错误
        logger.exception("Agent Runtime SSE stream failed")
        sequence += 1
        error_code = getattr(error, "code", None)
        yield encode_runtime_event(
            RuntimeEvent(
                "run.failed",
                {
                    "code": (
                        error_code
                        if isinstance(error_code, str) and error_code
                        else "runtime_execution_failed"
                    )
                },
                run_id=current_run_id,
            ),
            sequence,
        )


def runtime_sse_response(
    events: AsyncIterator[RuntimeEvent],
    *,
    logger: logging.Logger,
) -> StreamingResponse:
    """创建关闭代理缓冲的统一 Runtime SSE 响应。"""
    return StreamingResponse(
        stream_runtime_events(
            events,
            logger=logger,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
