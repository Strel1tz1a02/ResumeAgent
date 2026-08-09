"""用独立、无 Tools 的结构化调用生成 Memory Operations。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.operations import MemoryDocument, MemoryOperation
from app.ai_chat.memory.run_bundles import RunBundle
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.token_budget import (
    build_memory_token_budget,
    count_request_tokens,
)
from app.llm import _calculate_timeout, get_router


class MemoryOperationsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[MemoryOperation] = Field(default_factory=list)


_MAX_CONTENT_ATTEMPTS = 2


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _response_text(response: Any) -> str:
    """读取 LiteLLM 非流式响应中的正文。"""
    choices = _get(response, "choices", []) or []
    if not choices:
        raise ValueError("memory summary returned no choices")
    content = _get(_get(choices[0], "message", {}), "content")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        parts = [
            text
            for item in content
            if isinstance((text := _get(item, "text")), str)
        ]
        if parts:
            return "".join(parts)
    raise ValueError("memory summary returned no text")


def _prompt(parent: MemoryDocument, bundle: RunBundle) -> str:
    return """你是会话记忆维护器。你只能输出 JSON，不得调用工具。

基于完整的上一版 MEMORY 和一个终态 RUN，返回最小 Operations。

输出必须是一个 JSON 对象，顶层只能包含 operations：

{
  "operations": []
}

operations 中的每一项必须严格符合以下一种形状。下面是格式示例，不是要求同时输出。

更新已有字段：

{
  "op": "update",
  "path": "core.<field> 或 other.<key>",
  "value": "字符串或字符串数组"
}

新增 other 字段：

{
  "op": "add",
  "path": "other.<key>",
  "value": "字符串或字符串数组"
}

删除或清空字段：

{
  "op": "delete",
  "path": "core.<field> 或 other.<key>"
}

规则：
- Core 固定字段：current_goal、constraints、preferences、confirmed_decisions、open_questions。
- current_goal 的 value 必须是字符串；其他 Core 字段的 value 必须是完整字符串数组。
- add 只能创建尚不存在的 other 字段；update 必须替换整个已有字段。
- delete core 表示清空字段；delete other 表示删除字段。
- delete 只能包含 op 和 path，绝对不能携带 value，包括空字符串和 null。
- 同一个 path 在一次输出中最多出现一次。
- 只记录用户明确表达或双方已经确认、且后续仍需要的信息。
- Experience/Resume 事实、scope、revision、Tool 参数/结果、助手猜测不得进入记忆。
- 不预测下一问；不重要内容返回空 operations。
- other 必须是单层 snake_case，值只能是字符串或字符串数组。

MEMORY
""" + json.dumps(parent.model_dump(mode="json"), ensure_ascii=False) + """
END_MEMORY

RUN
""" + json.dumps(
        {
            "run_id": bundle.run_id,
            "kind": bundle.kind,
            "status": bundle.status,
            "error_code": bundle.error_code,
            "messages": bundle.messages,
            "tool_calls": bundle.tool_calls,
        },
        ensure_ascii=False,
    ) + """
END_RUN
"""


class MemorySummarizer:
    """把一个终态 Run 压缩成基于 Parent 的 Operations。"""

    async def summarize(self, parent: MemoryDocument, bundle: RunBundle) -> list[MemoryOperation]:
        prompt = _prompt(parent, bundle)
        spec = build_memory_token_budget(
            {},
            tools_enabled=False,
            requested_output=memory_settings.ai_chat_summary_output_reserve,
            configured_input_cap=memory_settings.ai_chat_summary_input_cap,
        )
        try:
            router, config = get_router()
            correction = ""
            for attempt in range(_MAX_CONTENT_ATTEMPTS):
                attempt_prompt = prompt + correction
                tokens = count_request_tokens(
                    spec,
                    [{"role": "user", "content": attempt_prompt}],
                )
                if tokens > spec.input_budget:
                    raise MemoryContextFullError("summary_run_too_large")
                kwargs: dict[str, Any] = {
                    "model": "primary",
                    "messages": [{"role": "user", "content": attempt_prompt}],
                    "stream": False,
                    "max_tokens": spec.max_tokens,
                    "timeout": _calculate_timeout(
                        "completion",
                        spec.max_tokens,
                        config.provider,
                    ),
                }
                if config.reasoning_effort:
                    kwargs["reasoning_effort"] = config.reasoning_effort
                response = await router.acompletion(**kwargs)
                try:
                    result = json.loads(_response_text(response))
                    return MemoryOperationsResult.model_validate(result).operations
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    if attempt + 1 >= _MAX_CONTENT_ATTEMPTS:
                        raise
                    correction = (
                        "\n\n上一轮输出不符合 Memory Operations Schema，请从原始 MEMORY "
                        "和 RUN 重新生成完整 JSON，不要修补上一轮文本。特别注意：delete "
                        "只能包含 op 和 path，不能携带 value。\n校验错误："
                        + str(exc)[:800]
                    )
            raise MemoryCompactionError("memory summary attempts exhausted")
        except MemoryContextFullError:
            raise
        except Exception as exc:
            raise MemoryCompactionError(str(exc)) from exc
