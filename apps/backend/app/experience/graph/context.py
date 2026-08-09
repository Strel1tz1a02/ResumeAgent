"""将已保存经历与聊天记录构造成模型上下文。"""

from __future__ import annotations

import json

from app.ai_chat.types import JsonObject


def tool_result_messages(item: JsonObject) -> list[JsonObject]:
    """为待补传的工具结果重建合法的助手与工具消息对。"""
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
    scope: JsonObject,
    scope_status: str,
    scope_revision: int,
    history: list[JsonObject],
    pending: list[JsonObject],
) -> list[JsonObject]:
    """按稳定顺序组合提示词、结构化事实、历史和待补传的工具结果。"""
    messages: list[JsonObject] = [
        {"role": "system", "content": prompt},
        {
            "role": "system",
            "content": "SAVED_EXPERIENCE_DATA\n"
            + json.dumps(
                {
                    "experience": detail,
                    # ref_id 是前端与持久化的绑定字段，不是模型工具参数。这里只暴露
                    # 会话字段名，避免模型把 ref_id 复制成 evidence_id。
                    "scope": {"field": scope.get("field")},
                    "scope_status": scope_status,
                    "scope_revision": scope_revision,
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
