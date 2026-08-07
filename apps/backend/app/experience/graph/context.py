"""将已保存经历与聊天记录构造成模型上下文。"""

from __future__ import annotations

import json

from app.ai_chat.tools.results import PendingToolResult
from app.ai_chat.types import JsonObject


def tool_result_messages(item: PendingToolResult) -> list[JsonObject]:
    """为 pending Tool Result 重建合法 assistant/tool 消息对。"""
    provider_id = item.get("provider_tool_call_id") or f"ai-chat-tool:{item['tool_call_id']}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": provider_id,
                    "type": "function",
                    "function": {
                        "name": item["tool_name"],
                        "arguments": json.dumps(item["arguments"], ensure_ascii=False),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": provider_id,
            "content": json.dumps(item["result"], ensure_ascii=False),
        },
    ]


def build_model_messages(
    *,
    prompt: str,
    detail: JsonObject,
    target: JsonObject,
    target_status: str,
    target_revision: int,
    history: list[JsonObject],
    pending: list[PendingToolResult],
) -> list[JsonObject]:
    """按稳定顺序组合 Prompt、结构化事实、历史和待补传 Tool Result。"""
    messages: list[JsonObject] = [
        {"role": "system", "content": prompt},
        {
            "role": "system",
            "content": "SAVED_EXPERIENCE_DATA\n"
            + json.dumps(
                {
                    "experience": detail,
                    # ref_id 是前端/持久化绑定字段，不是模型 Tool 参数。这里只暴露
                    # 会话字段名，避免模型把 ref_id 复制成 evidence_id。
                    "target": {"key": target.get("key")},
                    "target_status": target_status,
                    "target_revision": target_revision,
                },
                ensure_ascii=False,
            )
            + "\nEND_SAVED_EXPERIENCE_DATA",
        },
    ]
    trailing_user: JsonObject | None = None
    if pending and history and history[-1].get("role") == "user":
        trailing_user = history[-1]
        history = history[:-1]
    messages.extend(history)
    for result in pending:
        messages.extend(tool_result_messages(result))
    if trailing_user is not None:
        messages.append(trailing_user)
    return messages
