"""Unit tests for LLM capability helpers in app.llm."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.llm import (
    LLMConfig,
    _appears_truncated,
    _build_messages,
    _get_retry_temperature,
    _supports_temperature,
)


class TestBuildMessages:
    """LangChain 消息模板必须保持业务图请求格式。"""

    def test_builds_user_message_without_system_prompt(self):
        assert _build_messages("你好 {name}") == [
            {"role": "user", "content": "你好 {name}"}
        ]

    def test_builds_system_and_user_messages_in_order(self):
        assert _build_messages("用户内容", "系统内容") == [
            {"role": "system", "content": "系统内容"},
            {"role": "user", "content": "用户内容"},
        ]

    def test_empty_system_prompt_is_omitted(self):
        assert _build_messages("用户内容", "") == [
            {"role": "user", "content": "用户内容"}
        ]


# ---------------------------------------------------------------------------
# _supports_temperature
# ---------------------------------------------------------------------------


class TestSupportsTemperature:
    """Tests for _supports_temperature()."""

    def test_none_temperature_returns_true(self):
        """When temperature is None, the caller isn't setting a value — allow."""
        assert _supports_temperature("gpt-4", None) is True

    def test_ollama_always_true(self):
        """Ollama models support temperature even when not in registry."""
        assert _supports_temperature("ollama/llama3", 0.7) is True
        assert _supports_temperature("ollama_chat/llama3", 0.7) is True

    def test_openai_gpt4_supports_temperature(self):
        assert _supports_temperature("gpt-4", 0.7) is True

    def test_unknown_model_keeps_temperature(self):
        assert _supports_temperature("some-model", 0.7) is True

    def test_opus4_deprecated_temperature(self):
        """Anthropic Opus 4.x deprecated temperature entirely."""
        assert _supports_temperature("anthropic/claude-opus-4-7", 0.7) is False
        # Also check with temperature=1 — still deprecated
        assert _supports_temperature("anthropic/claude-opus-4-7", 1.0) is False

    def test_kimi_k26_only_allows_one(self):
        """Moonshot kimi-k2.6 only allows temperature=1."""
        assert _supports_temperature("openai/kimi-k2.6", 0.7) is False
        assert _supports_temperature("openai/kimi-k2.6", 1.0) is True

    def test_case_insensitive_model_name(self):
        """Provider-specific checks are case-insensitive."""
        assert _supports_temperature("Anthropic/Claude-Opus-4-7", 0.7) is False
        assert _supports_temperature("OPENAI/KIMI-K2.6", 0.7) is False
        assert _supports_temperature("openai/KIMI-K2.6", 1.0) is True


# ---------------------------------------------------------------------------
# _get_retry_temperature
# ---------------------------------------------------------------------------


class TestGetRetryTemperature:
    """Tests for _get_retry_temperature()."""

    def test_openai_progression(self):
        """Standard retry temperature progression for supported models."""
        assert _get_retry_temperature("gpt-4", 0) == 0.1
        assert _get_retry_temperature("gpt-4", 1) == 0.3
        assert _get_retry_temperature("gpt-4", 2) == 0.5
        assert _get_retry_temperature("gpt-4", 3) == 0.7
        assert _get_retry_temperature("gpt-4", 10) == 0.7  # clamped

    def test_opus4_returns_none(self):
        """Opus 4 doesn't support temperature → None on all retries."""
        assert _get_retry_temperature("anthropic/claude-opus-4-7", 0) is None
        assert _get_retry_temperature("anthropic/claude-opus-4-7", 3) is None

    def test_kimi_k26_returns_one(self):
        """Kimi K2.6 only allows temperature=1 → always 1.0."""
        assert _get_retry_temperature("openai/kimi-k2.6", 0) == 1.0
        assert _get_retry_temperature("openai/kimi-k2.6", 1) == 1.0
        assert _get_retry_temperature("openai/kimi-k2.6", 5) == 1.0

    def test_custom_base_temp(self):
        """Custom base_temp is respected for supported models."""
        assert _get_retry_temperature("gpt-4", 0, base_temp=0.2) == 0.2
        assert _get_retry_temperature("gpt-4", 1, base_temp=0.2) == 0.3


# ---------------------------------------------------------------------------
# _appears_truncated
# ---------------------------------------------------------------------------


class TestAppearsTruncated:
    """Tests for _appears_truncated() with schema_type awareness."""

    # --- resume schema ---

    def test_resume_empty_work_experience(self):
        """Empty workExperience array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [],
            "education": [{"degree": "BS"}],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_empty_education(self):
        """Empty education array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_empty_skills(self):
        """Empty skills array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [{"degree": "BS"}],
            "skills": [],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_valid(self):
        """Well-formed resume with all sections present is not truncated."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [{"degree": "BS"}],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is False

    def test_resume_missing_fields_not_empty(self):
        """Missing fields are not the same as empty arrays — not flagged."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            # education and skills omitted
        }
        assert _appears_truncated(data, schema_type="resume") is False

    # --- enrichment schema ---

    def test_enrichment_missing_keys(self):
        """Missing required keys in enrichment output is suspicious."""
        data = {"analysis_summary": "Good resume"}
        assert _appears_truncated(data, schema_type="enrichment") is True

    def test_enrichment_empty_arrays(self):
        """Empty items_to_enrich and questions are valid (resume already strong)."""
        data = {
            "items_to_enrich": [],
            "questions": [],
            "analysis_summary": "Already strong",
        }
        assert _appears_truncated(data, schema_type="enrichment") is False

    def test_enrichment_populated(self):
        """Populated enrichment output is not truncated."""
        data = {
            "items_to_enrich": [{"item_id": "exp_0"}],
            "questions": [{"question_id": "q_0"}],
            "analysis_summary": "Needs work",
        }
        assert _appears_truncated(data, schema_type="enrichment") is False

    # --- diff schema ---

    def test_diff_empty_changes(self):
        """Empty changes array in diff output is valid (no changes needed)."""
        data = {"changes": [], "strategy_notes": "No changes needed"}
        assert _appears_truncated(data, schema_type="diff") is False

    def test_diff_populated(self):
        """Populated diff output is not truncated."""
        data = {"changes": [{"path": "summary", "action": "replace"}]}
        assert _appears_truncated(data, schema_type="diff") is False

    # --- keywords schema ---

    def test_keywords_empty(self):
        """Empty keyword lists are valid (sparse job description)."""
        data = {"required_skills": [], "preferred_skills": [], "keywords": []}
        assert _appears_truncated(data, schema_type="keywords") is False

    # --- default / unknown schema ---

    def test_default_schema_acts_like_resume(self):
        """Default schema_type behaves like 'resume' for backwards compatibility."""
        data = {"workExperience": [], "education": [{"degree": "BS"}]}
        assert _appears_truncated(data) is True

    def test_unknown_schema_no_heuristics(self):
        """Unknown schema types have no truncation heuristics."""
        data = {"anything": []}
        assert _appears_truncated(data, schema_type="custom") is False


# ---------------------------------------------------------------------------
# complete_json JSON mode fallback
# ---------------------------------------------------------------------------


class _BoundModel:
    def __init__(self, model, kwargs):  # type: ignore[no-untyped-def]
        self.model = model
        self.kwargs = kwargs

    async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
        return await self.model.invoke_with(messages, self.kwargs)


class _FakeModel:
    def __init__(self, *responses):  # type: ignore[no-untyped-def]
        self.responses = list(responses)
        self.calls = []

    def bind(self, **kwargs):  # type: ignore[no-untyped-def]
        return _BoundModel(self, kwargs)

    async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
        return await self.invoke_with(messages, {})

    async def invoke_with(self, messages, kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestCompleteJsonFallback:
    """Tests for JSON mode fallback in complete_json()."""

    @pytest.mark.asyncio
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_parse_error(self, mock_supports_json):
        """When JSON mode returns invalid JSON, fallback to prompt-only mode.

        First call: JSON mode enabled → returns malformed JSON (trailing comma)
          → _extract_json succeeds → json.loads fails → JSONDecodeError
        Second call: JSON mode disabled → returns valid JSON → success
        """
        mock_supports_json.return_value = True
        model = _FakeModel(
            AIMessage(content='{"items_to_enrich": [], "questions": [],}'),
            AIMessage(
                content='{"items_to_enrich": [], "questions": [], "analysis_summary": "ok"}'
            ),
        )
        config = LLMConfig(
            provider="openrouter", model="openai/gpt-5.4", api_key="test"
        )

        from app.llm import complete_json

        with patch("app.llm.get_chat_model", return_value=(model, config)):
            result = await complete_json(
                prompt="Test prompt",
                config=config,
                schema_type="enrichment",
                retries=2,
            )

        assert result == {
            "items_to_enrich": [],
            "questions": [],
            "analysis_summary": "ok",
        }
        # Verify JSON mode was used on first call but not second
        assert model.calls[0].get("response_format") == {"type": "json_object"}
        assert "response_format" not in model.calls[1]

    @pytest.mark.asyncio
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_response_format_rejection(
        self, mock_supports_json
    ):
        """Issue #857: an OpenAI-compatible server (e.g. LM Studio) rejects
        ``response_format={"type": "json_object"}`` with a 400.

        First call: JSON mode enabled → server raises ``BadRequestError``
          ("'response_format.type' must be 'json_schema' or 'text'").
        Second call: JSON mode disabled → returns valid JSON → success.

        Before the fix the 400 was re-raised immediately (the existing fallback
        only handled malformed JSON, not rejection of the parameter itself),
        so the wizard turn failed with a 500.
        """
        mock_supports_json.return_value = True
        rejection = RuntimeError(
            "OpenAIException - Error code: 400 - "
            "{'error': \"'response_format.type' must be 'json_schema' or 'text'\"}"
        )
        model = _FakeModel(rejection, AIMessage(content='{"answer": "ok"}'))
        config = LLMConfig(
            provider="openai_compatible",
            model="gemma-4-e2b",
            api_key="",
            api_base="http://localhost:1234/v1",
        )

        from app.llm import complete_json

        with patch("app.llm.get_chat_model", return_value=(model, config)):
            result = await complete_json(
                prompt="Test prompt",
                config=config,
                schema_type="resume",
                retries=2,
            )

        assert result == {"answer": "ok"}
        # JSON mode was sent on the first (rejected) call, dropped on the retry.
        assert model.calls[0].get("response_format") == {"type": "json_object"}
        assert "response_format" not in model.calls[1]

    @pytest.mark.asyncio
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_varied_rejection_wording(
        self, mock_supports_json
    ):
        """The fallback must trigger across provider wording, not just LM Studio's.

        Guards against narrowing the heuristic so much that a genuine
        response_format rejection phrased as "not supported" is missed (which
        would re-introduce issue #857 for that provider).
        """
        mock_supports_json.return_value = True
        rejection = RuntimeError(
            "OpenAIException - Error code: 400 - "
            "{'error': 'response_format json_object is not supported by this model'}"
        )
        model = _FakeModel(rejection, AIMessage(content='{"answer": "ok"}'))
        config = LLMConfig(
            provider="openai_compatible",
            model="some-local-model",
            api_key="",
            api_base="http://localhost:1234/v1",
        )

        from app.llm import complete_json

        with patch("app.llm.get_chat_model", return_value=(model, config)):
            result = await complete_json(
                prompt="Test prompt", config=config, schema_type="resume", retries=2
            )

        assert result == {"answer": "ok"}
        assert "response_format" not in model.calls[1]

    @pytest.mark.asyncio
    @patch("app.llm._supports_json_mode")
    async def test_unrelated_bad_request_is_not_swallowed(self, mock_supports_json):
        """A 400 unrelated to response_format must still propagate, not retry.

        Uses a context-length error that *also names* response_format — the
        false-positive case raised in review (cubic/Kilo). Dropping JSON mode
        would not help, so the fallback must NOT fire and the error must surface.
        """
        mock_supports_json.return_value = True
        rejection = RuntimeError(
            "OpenAIException - Error code: 400 - {'error': 'maximum context "
            "length exceeded while using response_format=json_object'}"
        )
        model = _FakeModel(rejection)
        config = LLMConfig(provider="openai", model="gpt-4o", api_key="test")

        from app.llm import complete_json

        with patch("app.llm.get_chat_model", return_value=(model, config)):
            with pytest.raises(RuntimeError):
                await complete_json(
                    prompt="Test prompt",
                    config=config,
                    schema_type="resume",
                    retries=2,
                )

        # No retry: an unrelated 400 fails fast (Router already handles retries).
        assert len(model.calls) == 1


# ---------------------------------------------------------------------------
# complete() dynamic timeout
# ---------------------------------------------------------------------------


class TestCompleteDynamicTimeout:
    """Tests for complete() using _calculate_timeout()."""

    @pytest.mark.asyncio
    @patch("app.llm._calculate_timeout")
    async def test_uses_calculate_timeout(self, mock_calc_timeout):
        """complete() passes provider and max_tokens to _calculate_timeout."""
        mock_calc_timeout.return_value = 180
        model = _FakeModel(AIMessage(content="Hello"))
        config = LLMConfig(provider="deepseek", model="deepseek-chat", api_key="test")

        from app.llm import complete

        with patch("app.llm.get_llm_config", return_value=config), patch(
            "app.llm.get_chat_model", return_value=(model, config)
        ) as get_model:
            await complete(prompt="Hi", max_tokens=8192)

        mock_calc_timeout.assert_called_once_with("completion", 8192, "deepseek")
        assert get_model.call_args.kwargs["timeout"] == 180
        assert len(model.calls) == 1
