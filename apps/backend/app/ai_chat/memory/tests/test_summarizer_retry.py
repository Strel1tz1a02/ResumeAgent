"""MemorySummarizer 结构化输出重试测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai_chat.memory.runs import Memory
from app.ai_chat.memory.runs import OriginRun
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import MemoryTokenBudget


async def test_invalid_delete_value_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Model:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.responses = [
                '{"operations":[{"op":"delete","path":"core.open_questions","value":""}]}',
                '{"operations":[{"op":"delete","path":"core.open_questions"}]}',
            ]

        async def ainvoke(self, messages: list[dict[str, str]]) -> Any:
            self.prompts.append(messages[0]["content"])
            return SimpleNamespace(
                content=self.responses.pop(0), additional_kwargs={}
            )

    model = _Model()
    config = SimpleNamespace(provider="openai", reasoning_effort=None)
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.get_llm_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.get_chat_model",
        lambda *_args, **_kwargs: (model, config),
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.build_memory_token_budget",
        lambda *_args, **_kwargs: MemoryTokenBudget(
            model="provider/model",
            max_tokens=100,
            input_budget=1000,
        ),
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.count_request_tokens",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer._calculate_timeout",
        lambda *_args: 30,
    )

    operations = await MemorySummarizer().summarize(
        Memory(open_questions=["项目经历是否单列"]),
        OriginRun(
            run_id=103,
            kind="user_turn",
            status="completed",
            error_code=None,
            messages=(
                {
                    "role": "user",
                    "status": "completed",
                    "content": "问题已解决，不再是开放问题。",
                },
            ),
            tool_calls=(),
        ),
    )

    assert len(model.prompts) == 2
    assert "delete 只能包含 op 和 path" in model.prompts[1]
    assert operations[0].op == "delete"
    assert operations[0].path == "core.open_questions"
    assert operations[0].value is None
