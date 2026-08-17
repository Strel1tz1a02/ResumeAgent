"""经历文本 LangChain 结构化提取器的对象契约测试。"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.experience.schemas.experiences import ExperienceGlobalSave
from app.experience.services.experience_text_extractor import (
    ExperienceTextExtractionError,
    ExperienceTextExtractor,
)
from app.llm import LLMConfig


class _StructuredModel:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[object] = []

    async def ainvoke(self, messages: object) -> object:
        self.calls.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Model:
    def __init__(self, structured: _StructuredModel) -> None:
        self.structured = structured
        self.schema: type[ExperienceGlobalSave] | None = None
        self.include_raw = False

    def with_structured_output(
        self,
        schema: type[ExperienceGlobalSave],
        *,
        include_raw: bool,
    ) -> _StructuredModel:
        self.schema = schema
        self.include_raw = include_raw
        return self.structured


async def test_extractor_requests_typed_object() -> None:
    """提取器必须通过 LangChain 请求 Pydantic 结构化输出。"""
    expected = ExperienceGlobalSave.model_validate(
        {
            "experience": {"kind": "project", "title": "Agent"},
            "evidence_items": [{"action": "Built parser"}],
        }
    )
    structured = _StructuredModel([expected])
    model = _Model(structured)
    config = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="test")

    with (
        patch(
            "app.experience.services.experience_text_extractor.get_llm_config",
            return_value=config,
        ),
        patch(
            "app.experience.services.experience_text_extractor.get_chat_model",
            return_value=(model, config),
        ) as get_model,
        patch(
            "app.experience.services.experience_text_extractor.get_content_language",
            return_value="zh",
        ),
    ):
        result = await ExperienceTextExtractor().extract("做了一个解析器")

    assert result is expected
    assert model.schema is ExperienceGlobalSave
    assert model.include_raw is True
    assert get_model.call_args.kwargs["max_tokens"] == 8_192
    assert get_model.call_args.kwargs["timeout"] == 360
    assert "做了一个解析器" in structured.calls[0][1]["content"]  # type: ignore[index]


async def test_extractor_retries_invalid_structured_output() -> None:
    """第一次结构校验失败时应携带修复提示再次请求模型。"""
    expected = ExperienceGlobalSave.model_validate(
        {"experience": {"kind": "project", "title": "Agent"}, "evidence_items": []}
    )
    structured = _StructuredModel(
        [
            {
                "raw": AIMessage(content='{"experience":{"kind":"invalid"}}'),
                "parsed": None,
                "parsing_error": ValueError("invalid kind"),
            },
            {
                "raw": AIMessage(content='{"experience":{"kind":"project"}}'),
                "parsed": expected,
                "parsing_error": None,
            },
        ]
    )
    model = _Model(structured)
    config = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="test")
    with (
        patch(
            "app.experience.services.experience_text_extractor.get_llm_config",
            return_value=config,
        ),
        patch(
            "app.experience.services.experience_text_extractor.get_chat_model",
            return_value=(model, config),
        ),
        patch(
            "app.experience.services.experience_text_extractor.get_content_language",
            return_value="zh",
        ),
    ):
        result = await ExperienceTextExtractor().extract("做了一个 Agent")

    assert result.experience.title == "Agent"
    assert len(structured.calls) == 2
    assert "上次输出未通过结构校验" in structured.calls[1][1]["content"]  # type: ignore[index]


async def test_extractor_logs_last_raw_completion_on_failure() -> None:
    """最终失败应记录 LangChain 的最后一次原始响应。"""
    raw = AIMessage(content="raw output")
    outcomes = [
        {"raw": raw, "parsed": None, "parsing_error": RuntimeError("invalid")}
        for _ in range(3)
    ]
    structured = _StructuredModel(outcomes)
    model = _Model(structured)
    config = LLMConfig(
        provider="deepseek",
        model="deepseek-chat",
        api_key="test",
        reasoning_effort="minimal",
    )

    with (
        patch(
            "app.experience.services.experience_text_extractor.get_llm_config",
            return_value=config,
        ),
        patch(
            "app.experience.services.experience_text_extractor.get_chat_model",
            return_value=(model, config),
        ) as get_model,
        patch(
            "app.experience.services.experience_text_extractor.logger.error"
        ) as log_error,
        pytest.raises(ExperienceTextExtractionError),
    ):
        await ExperienceTextExtractor().extract("待解析的经历文本")

    assert len(structured.calls) == 3
    assert get_model.call_args.kwargs["max_tokens"] == 32_768
    assert get_model.call_args.kwargs["timeout"] == 1_440
    assert get_model.call_args.kwargs["disable_reasoning"] is True
    log_error.assert_called_once()
    assert log_error.call_args.args[2] == "deepseek"
    assert "raw output" in log_error.call_args.args[-1]
