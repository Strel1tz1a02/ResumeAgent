"""用独立、无 Tools 的结构化调用生成 Memory Operations。"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.operations import MemoryDocument, MemoryOperation
from app.ai_chat.memory.run_bundles import RunBundle
from app.ai_chat.memory.token_budget import (
    build_memory_token_budget,
    count_request_tokens,
)
from app.config import settings
from app.llm import complete_json


class MemoryOperationsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[MemoryOperation] = Field(default_factory=list)


def _prompt(parent: MemoryDocument, bundle: RunBundle) -> str:
    return """你是会话记忆维护器。你只能输出 JSON，不得调用工具。

基于完整的上一版 MEMORY 和一个已完成 RUN，返回最小 Operations：
{"operations":[{"op":"add|update|delete","path":"core.<field>|other.<key>","value":"..."}]}

规则：
- Core 固定字段：current_goal、constraints、preferences、confirmed_decisions、open_questions。
- add 只能创建 other；update 替换整个字段；delete core 只清空内容，delete other 删除字段。
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
            "messages": bundle.messages,
            "tool_outcomes": bundle.tool_outcomes,
        },
        ensure_ascii=False,
    ) + """
END_RUN
"""


class MemorySummarizer:
    """把一个完整 Run 压缩成基于 Parent 的 Operations。"""

    async def summarize(
        self, parent: MemoryDocument, bundle: RunBundle
    ) -> list[MemoryOperation]:
        prompt = _prompt(parent, bundle)
        spec = build_memory_token_budget(
            {},
            tools_enabled=False,
            requested_output=settings.ai_chat_summary_output_reserve,
            configured_input_cap=settings.ai_chat_summary_input_cap,
        )
        tokens = count_request_tokens(spec, [{"role": "user", "content": prompt}])
        if tokens > spec.input_budget:
            raise MemoryContextFullError("summary_run_too_large")
        try:
            result = await complete_json(
                prompt,
                max_tokens=spec.max_tokens,
                schema_type="memory",
            )
            return MemoryOperationsResult.model_validate(result).operations
        except MemoryContextFullError:
            raise
        except Exception as exc:
            raise MemoryCompactionError(str(exc)) from exc
