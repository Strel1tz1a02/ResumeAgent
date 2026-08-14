"""经历文本 Instructor 提取器的对象契约测试。"""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest
from app.experience.schemas.experiences import ExperienceGlobalSave
from app.experience.services.experience_text_extractor import (
    ExperienceTextExtractionError,
    ExperienceTextExtractor,
)
from litellm import ModelResponse


async def test_extractor_requests_typed_object_with_validation_retries() -> None:
    """提取器必须让 Instructor 直接返回 Pydantic 对象并启用两次重试。"""
    expected = ExperienceGlobalSave.model_validate(
        {
            "experience": {"kind": "project", "title": "Agent"},
            "evidence_items": [{"action": "Built parser"}],
        }
    )
    create = AsyncMock(return_value=expected)
    instructor_client = SimpleNamespace(create=create)
    router = SimpleNamespace(acompletion=AsyncMock())
    config = SimpleNamespace(
        provider="openai", model="gpt-4o-mini", reasoning_effort=None
    )

    with (
        patch(
            "app.experience.services.experience_text_extractor.get_router",
            return_value=(router, config),
        ),
        patch(
            "app.experience.services.experience_text_extractor.instructor.from_litellm",
            return_value=instructor_client,
        ) as from_litellm,
        patch(
            "app.experience.services.experience_text_extractor.get_content_language",
            return_value="zh",
        ),
    ):
        result = await ExperienceTextExtractor().extract("做了一个解析器")

    assert result is expected
    from_litellm.assert_called_once()
    kwargs = create.await_args.kwargs
    assert kwargs["response_model"] is ExperienceGlobalSave
    assert kwargs["max_retries"] == 2
    assert kwargs["max_tokens"] == 8_192
    assert kwargs["timeout"] == 360
    assert kwargs["model"] == "primary"
    assert "做了一个解析器" in kwargs["messages"][1]["content"]


async def test_extractor_retries_invalid_pydantic_output_with_real_instructor() -> None:
    """第一次对象校验失败时，Instructor 应携带错误再次请求模型。"""
    calls = 0

    async def fake_completion(**_kwargs: object) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert "stream" not in _kwargs
        content = (
            '{"experience":{"kind":"invalid"},"evidence_items":[]}'
            if calls == 1
            else '{"experience":{"kind":"project","title":"Agent"},"evidence_items":[]}'
        )

        return ModelResponse(
            model="fake",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        )

    router = SimpleNamespace(acompletion=fake_completion)
    config = SimpleNamespace(
        provider="openai", model="gpt-4o-mini", reasoning_effort=None
    )
    with (
        patch(
            "app.experience.services.experience_text_extractor.get_router",
            return_value=(router, config),
        ),
        patch(
            "app.experience.services.experience_text_extractor.get_content_language",
            return_value="zh",
        ),
        patch(
            "app.experience.services.experience_text_extractor.logger.info"
        ) as log_info,
    ):
        result = await ExperienceTextExtractor().extract("做了一个 Agent")

    assert result.experience.title == "Agent"
    assert calls == 2
    assert log_info.call_count == 2
    assert sum("原始回答" in call.args[0] for call in log_info.call_args_list) == 2
    assert all(call.args[4] == 8_192 for call in log_info.call_args_list)
    assert all(call.args[5] is None for call in log_info.call_args_list)
    assert any("Agent" in call.args[6] for call in log_info.call_args_list)


async def test_extractor_logs_last_raw_completion_on_failure() -> None:
    """最终失败应同时记录异常栈和 Instructor 的最后一次原始响应。"""
    source_text = "待解析的经历文本"
    error = RuntimeError("provider unavailable")
    error.last_completion = SimpleNamespace(  # type: ignore[attr-defined]
        model_dump_json=lambda: '{"choices":[{"message":{"content":"raw output"}}]}'
    )
    create = AsyncMock(side_effect=error)
    instructor_client = SimpleNamespace(create=create)
    router = SimpleNamespace(acompletion=AsyncMock())
    config = SimpleNamespace(
        provider="deepseek", model="deepseek-chat", reasoning_effort="minimal"
    )

    with (
        patch(
            "app.experience.services.experience_text_extractor.get_router",
            return_value=(router, config),
        ),
        patch(
            "app.experience.services.experience_text_extractor.instructor.from_litellm",
            return_value=instructor_client,
        ),
        patch(
            "app.experience.services.experience_text_extractor.logger.exception"
        ) as log_exception,
        pytest.raises(ExperienceTextExtractionError),
    ):
        await ExperienceTextExtractor().extract(source_text)

    assert create.await_args.kwargs["max_tokens"] == 32_768
    assert create.await_args.kwargs["timeout"] == 1_440
    assert create.await_args.kwargs["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in create.await_args.kwargs
    log_exception.assert_called_once_with(
        "经历文本解析失败：request_id=%s provider=%s model=primary "
        "requested_max_tokens=%s error_type=%s model_answer=%s "
        "last_completion=%s",
        ANY,
        "deepseek",
        32_768,
        "RuntimeError",
        '"raw output"',
        '{"choices":[{"message":{"content":"raw output"}}]}',
    )
