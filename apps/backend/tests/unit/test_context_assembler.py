"""统一模型上下文组装器测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.ai_chat.context import ContextAssembler
from app.ai_chat.memory import token_budget


async def test_context_assembler_owns_order_memory_tools_and_budget(monkeypatch) -> None:
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
        "app.ai_chat.context.assembler.count_request_tokens",
        fake_count,
    )
    monkeypatch.setattr(
        "app.ai_chat.context.assembler.build_structured_token_budget",
        lambda: SimpleNamespace(input_budget=1000),
    )
    prepared = await ContextAssembler(_HistoryMemory()).assemble(  # type: ignore[arg-type]
        run_id=7,
        context={
            "instructions": "policy",
            "domain_sections": [
                {"name": "experience", "data": {"value": "domain truth"}}
            ],
            "messages": [{"role": "user", "content": "current question"}],
            "pending_tool_results": [
                {
                    "tool_call_id": 9,
                    "provider_tool_call_id": "call-9",
                    "tool_name": "change",
                    "arguments": {"value": "before"},
                    "result": {"value": "after"},
                }
            ],
        },
        tools=tools,
    )

    assert [message["role"] for message in prepared] == [
        "system",
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert "domain truth" in str(prepared[1]["content"])
    assert "remembered" in str(prepared[2]["content"])
    assert prepared[-1]["content"] == "current question"
    assert "remembered" not in str(counted[0][0][2]["content"])
    assert counted[0][1] == tools
    assert counted[1] == (prepared, tools)


def test_structured_context_uses_same_data_boundary_and_budget(monkeypatch) -> None:
    counted: list[list[dict[str, Any]]] = []

    def fake_count(_budget, messages, _tools):  # type: ignore[no-untyped-def]
        counted.append(messages)
        return 20

    monkeypatch.setattr(
        "app.ai_chat.context.assembler.count_request_tokens",
        fake_count,
    )
    monkeypatch.setattr(
        "app.ai_chat.context.assembler.build_memory_token_budget",
        lambda: SimpleNamespace(input_budget=1000),
    )

    context = ContextAssembler.assemble_structured(
        instructions="Only use supplied facts.",
        domain_sections=[
            {"name": "resume_generation_input", "data": {"company": "Acme"}}
        ],
    )

    assert context.system_prompt == "Only use supplied facts."
    assert "UNTRUSTED_DOMAIN_DATA name=resume_generation_input" in context.prompt
    assert '"company": "Acme"' in context.prompt
    assert "Acme" not in context.system_prompt
    assert counted == [
        [
            {"role": "system", "content": context.system_prompt},
            {"role": "user", "content": context.prompt},
        ]
    ]


def test_structured_budget_uses_known_model_input_limit(monkeypatch) -> None:
    captured: list[int] = []
    expected = SimpleNamespace(input_budget=999_488)

    monkeypatch.setattr(token_budget, "_model_limits", lambda: (1_000_000, 384_000))
    monkeypatch.setattr(
        token_budget,
        "build_memory_token_budget",
        lambda *, configured_input_cap: (
            captured.append(configured_input_cap) or expected
        ),
    )

    result = token_budget.build_structured_token_budget()

    assert result is expected
    assert captured == [1_000_000]


def test_structured_budget_keeps_safe_cap_for_unknown_model(monkeypatch) -> None:
    captured: list[int] = []

    monkeypatch.setattr(token_budget, "_model_limits", lambda: (None, None))
    monkeypatch.setattr(
        token_budget,
        "build_memory_token_budget",
        lambda *, configured_input_cap: captured.append(configured_input_cap),
    )

    token_budget.build_structured_token_budget()

    assert captured == [token_budget.memory_settings.ai_chat_input_cap]
