"""经历模块自有模型上下文的组装测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.experience.graph.context import prepare_request_messages


async def test_experience_context_injects_memory_after_system_messages(
    monkeypatch,
) -> None:
    counted: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
    tools = [{"type": "function", "function": {"name": "change"}}]

    def fake_count(_budget, messages, request_tools):  # type: ignore[no-untyped-def]
        counted.append((messages, request_tools))
        return 123 if len(counted) == 1 else 200

    class _HistoryMemory:
        async def get_history_prompt(self, run_id, occupied_token):  # type: ignore[no-untyped-def]
            assert run_id == 7
            assert occupied_token == 123
            return '{"memory":{"current_goal":"remembered"},"runs":[]}'

    monkeypatch.setattr(
        "app.experience.graph.context.count_request_tokens",
        fake_count,
    )
    monkeypatch.setattr(
        "app.experience.graph.context.build_memory_token_budget",
        lambda: SimpleNamespace(input_budget=1000),
    )
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "system", "content": "domain truth"},
        {"role": "user", "content": "current question"},
    ]

    prepared = await prepare_request_messages(
        run_id=7,
        messages=messages,
        memory=_HistoryMemory(),  # type: ignore[arg-type]
        tools=tools,
    )

    assert [message["role"] for message in prepared] == [
        "system",
        "system",
        "user",
        "user",
    ]
    assert "remembered" in str(prepared[2]["content"])
    assert prepared[3] == messages[2]
    assert "remembered" not in str(counted[0][0][2]["content"])
    assert counted[0][1] == tools
    assert counted[1] == (prepared, tools)
