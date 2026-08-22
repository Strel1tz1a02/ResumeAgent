"""使用 LangChain 结构化输出把临时文本解析为未持久化的经历草稿。"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from app.ai_chat.context import ContextAssembler
from app.config_cache import get_content_language
from app.experience.prompts.import_text import SYSTEM_PROMPT, import_text_instruction
from app.experience.schemas.experiences import ExperienceGlobalSave
from app.llm import (
    _calculate_timeout,
    _extract_message_text,
    get_chat_model,
    get_configured_max_tokens,
    get_llm_config,
    get_model_name,
)

logger = logging.getLogger(__name__)
IMPORT_MAX_RETRIES = 2


def _completion_for_log(completion: object | None) -> str:
    """把 LangChain 原始响应完整序列化，供导入故障诊断。"""
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
    """提取 assistant 消息正文，并编码为单行完整 JSON 字符串。"""
    if completion is None:
        return "<not available>"
    try:
        content = _extract_message_text(completion)
        return json.dumps(content, ensure_ascii=False, default=str)
    except (AttributeError, TypeError, ValueError):
        return "<not available>"


class ExperienceTextExtractionError(RuntimeError):
    """文本在重试后仍无法转换为合法的经历草稿。"""


class ExperienceTextExtractor:
    """通过 LangChain 结构化输出返回经过 Pydantic 校验的导入草稿。"""

    async def extract(self, text: str) -> ExperienceGlobalSave:
        """解析文本并返回对象；本方法不访问数据库。"""
        language = get_content_language()
        config = get_llm_config()
        request_id = uuid4().hex
        model_name = get_model_name(config)
        max_tokens = get_configured_max_tokens(config)
        model, _ = get_chat_model(
            config,
            max_tokens=max_tokens,
            timeout=_calculate_timeout("json", max_tokens, config.provider),
            disable_reasoning=config.provider == "deepseek",
        )
        structured = model.with_structured_output(
            ExperienceGlobalSave,
            include_raw=True,
        )
        context = ContextAssembler.assemble_structured(
            instructions=f"{SYSTEM_PROMPT}\n{import_text_instruction(language)}",
            domain_sections=[
                {"name": "experience_import_text", "data": {"text": text}}
            ],
        )
        messages = [
            {"role": "system", "content": context.system_prompt},
            {"role": "user", "content": context.prompt},
        ]
        last_error: Exception | None = None
        last_completion: object | None = None
        for attempt in range(IMPORT_MAX_RETRIES + 1):
            try:
                result = await structured.ainvoke(messages)
                if isinstance(result, ExperienceGlobalSave):
                    return result
                if not isinstance(result, dict):
                    raise ValueError("structured output returned an invalid result")
                last_completion = result.get("raw")
                parsed = result.get("parsed")
                error = result.get("parsing_error")
                logger.info(
                    "经历导入模型原始回答：request_id=%s attempt=%s provider=%s "
                    "requested_max_tokens=%s model_answer=%s raw_response=%s",
                    request_id,
                    attempt + 1,
                    config.provider,
                    max_tokens,
                    _model_answer_for_log(last_completion),
                    _completion_for_log(last_completion),
                )
                if isinstance(parsed, ExperienceGlobalSave):
                    return parsed
                if isinstance(error, Exception):
                    raise error
                raise ValueError("structured output did not return parsed data")
            except Exception as error:
                last_error = error
                if attempt < IMPORT_MAX_RETRIES:
                    messages[-1]["content"] = context.prompt + (
                        "\n\n上次输出未通过结构校验，请重新输出完整且合法的对象。"
                    )
                    continue
        logger.error(
            "经历文本解析失败：request_id=%s provider=%s model=%s "
            "requested_max_tokens=%s error_type=%s model_answer=%s "
            "last_completion=%s",
            request_id,
            config.provider,
            model_name,
            max_tokens,
            type(last_error).__name__ if last_error else "Unknown",
            _model_answer_for_log(last_completion),
            _completion_for_log(last_completion),
        )
        raise ExperienceTextExtractionError(
            "experience text could not be parsed"
        ) from last_error
