"""使用 Instructor 把临时文本解析为未持久化的经历草稿。"""

from __future__ import annotations

import logging

import instructor
from instructor import Mode

from app.config_cache import get_content_language
from app.experience.prompts.import_text import SYSTEM_PROMPT, import_text_prompt
from app.experience.schemas.experiences import ExperienceGlobalSave
from app.llm import (
    DEFAULT_JSON_MAX_TOKENS,
    _calculate_timeout,
    get_model_name,
    get_router,
    get_safe_max_tokens,
)

logger = logging.getLogger(__name__)
MAX_LOGGED_COMPLETION_CHARS = 20_000
IMPORT_MAX_RETRIES = 2


def _completion_for_log(error: Exception) -> str:
    """提取 Instructor 最后一次原始响应，并限制诊断日志大小。"""
    completion = getattr(error, "last_completion", None)
    if completion is None:
        return "<not available>"
    try:
        if hasattr(completion, "model_dump_json"):
            value = completion.model_dump_json()
        else:
            value = repr(completion)
    except (AttributeError, TypeError, ValueError):
        value = repr(completion)
    if len(value) <= MAX_LOGGED_COMPLETION_CHARS:
        return value
    return value[:MAX_LOGGED_COMPLETION_CHARS] + "...<truncated>"


class ExperienceTextExtractionError(RuntimeError):
    """文本在重试后仍无法转换为合法的经历草稿。"""


class ExperienceTextExtractor:
    """通过现有 LiteLLM Router 返回经过 Pydantic 校验的导入草稿。"""

    async def extract(self, text: str) -> ExperienceGlobalSave:
        """解析文本并返回对象；本方法不访问数据库。"""
        language = get_content_language()
        router, config = get_router()
        model_name = get_model_name(config)
        max_tokens = get_safe_max_tokens(model_name, DEFAULT_JSON_MAX_TOKENS)
        client = instructor.from_litellm(
            router.acompletion,
            mode=Mode.JSON,
            async_client=True,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": import_text_prompt(text, language)},
        ]
        kwargs: dict[str, object] = {
            "model": "primary",
            "messages": messages,
            "response_model": ExperienceGlobalSave,
            "max_retries": IMPORT_MAX_RETRIES,
            "max_tokens": max_tokens,
            "timeout": _calculate_timeout("json", max_tokens, config.provider),
        }
        if config.reasoning_effort:
            kwargs["reasoning_effort"] = config.reasoning_effort
        try:
            return await client.create(**kwargs)
        except Exception as error:
            raw_completion = _completion_for_log(error)
            logger.exception(
                "经历文本解析失败：provider=%s model=primary error_type=%s "
                "last_completion=%s",
                config.provider,
                type(error).__name__,
                raw_completion,
            )
            raise ExperienceTextExtractionError(
                "experience text could not be parsed"
            ) from error
