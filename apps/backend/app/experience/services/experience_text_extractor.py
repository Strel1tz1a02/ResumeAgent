"""使用 Instructor 把临时文本解析为未持久化的经历草稿。"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

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
IMPORT_MAX_RETRIES = 2
DEEPSEEK_IMPORT_MAX_TOKENS = 32_768


def _completion_for_log(completion: object | None) -> str:
    """把 LiteLLM 原始响应完整序列化，供导入故障诊断。"""
    if completion is None:
        return "<not available>"
    try:
        if hasattr(completion, "model_dump_json"):
            value = completion.model_dump_json()
        else:
            value = repr(completion)
    except (AttributeError, TypeError, ValueError):
        value = repr(completion)
    return value


def _model_answer_for_log(completion: object | None) -> str:
    """提取第一条 assistant 消息正文，并编码为单行完整 JSON 字符串。"""
    if completion is None:
        return "<not available>"
    try:
        payload = completion
        if not isinstance(payload, dict) and not hasattr(payload, "choices"):
            payload = json.loads(payload.model_dump_json())  # type: ignore[attr-defined]
        choices = _field(payload, "choices")
        choice = choices[0]
        message = _field(choice, "message")
        content = _field(message, "content")
        return json.dumps(content, ensure_ascii=False, default=str)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return "<not available>"


def _field(value: object, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _logged_completion(
    completion: Callable[..., Awaitable[object]],
    *,
    request_id: str,
    provider: str,
) -> Callable[..., Awaitable[object]]:
    """保持非流式调用，记录 Instructor 每次请求取得的原始响应。"""
    attempt = 0

    async def call(**kwargs: Any) -> object:
        nonlocal attempt
        attempt += 1
        current_attempt = attempt
        response = await completion(**kwargs)
        logger.info(
            "经历导入模型原始回答：request_id=%s attempt=%s provider=%s "
            "requested_max_tokens=%s request_thinking=%s model_answer=%s "
            "raw_response=%s",
            request_id,
            current_attempt,
            provider,
            kwargs.get("max_tokens"),
            kwargs.get("thinking"),
            _model_answer_for_log(response),
            _completion_for_log(response),
        )
        return response

    return call


class ExperienceTextExtractionError(RuntimeError):
    """文本在重试后仍无法转换为合法的经历草稿。"""


class ExperienceTextExtractor:
    """通过现有 LiteLLM Router 返回经过 Pydantic 校验的导入草稿。"""

    async def extract(self, text: str) -> ExperienceGlobalSave:
        """解析文本并返回对象；本方法不访问数据库。"""
        language = get_content_language()
        router, config = get_router()
        request_id = uuid4().hex
        model_name = get_model_name(config)
        # LiteLLM 的内置注册表仍把 DeepSeek V4 Flash 输出限制标为 8192，
        # 但当前 DeepSeek API 支持更长输出。导入长经历时
        # 使用官方集成建议的 32K 默认输出额度，避免在正文生成前耗尽推理 token。
        max_tokens = (
            DEEPSEEK_IMPORT_MAX_TOKENS
            if config.provider == "deepseek"
            else get_safe_max_tokens(model_name, DEFAULT_JSON_MAX_TOKENS)
        )
        client = instructor.from_litellm(
            _logged_completion(
                router.acompletion,
                request_id=request_id,
                provider=config.provider,
            ),
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
        if config.provider == "deepseek":
            # DeepSeek V4 的思考模式无法单独设置推理预算；长经历会在正文
            # 生成前耗尽约 8K 推理 token。结构化导入关闭思考以保证输出 JSON。
            kwargs["thinking"] = {"type": "disabled"}
        elif config.reasoning_effort:
            kwargs["reasoning_effort"] = config.reasoning_effort
        try:
            return await client.create(**kwargs)
        except Exception as error:
            last_completion = getattr(error, "last_completion", None)
            raw_completion = _completion_for_log(last_completion)
            logger.exception(
                "经历文本解析失败：request_id=%s provider=%s model=primary "
                "requested_max_tokens=%s error_type=%s model_answer=%s "
                "last_completion=%s",
                request_id,
                config.provider,
                max_tokens,
                type(error).__name__,
                _model_answer_for_log(last_completion),
                raw_completion,
            )
            raise ExperienceTextExtractionError(
                "experience text could not be parsed"
            ) from error
