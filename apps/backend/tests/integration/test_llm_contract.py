"""Transport-level contract tests for app.llm.

These exercise the REAL request path in app/llm.py (``complete`` /
``complete_json`` / ``check_llm_health``) instead of mocking
the model client. We stand up a fake HTTP server with ``respx`` and let
LangChain's provider integration issue the request over the wire, providing
regression coverage for the long-standing "Ollama doesn't work" reports and the
local ``openai_compatible`` server path (issue #751).

EVERY test in this module is a TRUE respx HTTP test: the provider client actually
serialises a request, sends it through httpx's transport, and parses the mocked
HTTP response. No LangChain model boundary mocks are used.
"""

import httpx
import pytest
import respx

from app.llm import LLMConfig, check_llm_health, complete, complete_json


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Reset the LangChain model cache between transport tests."""
    import app.llm as llm

    llm._cached_chat_model.cache_clear()


# ---------------------------------------------------------------------------
# Response-body builders mirroring each provider's wire format
# ---------------------------------------------------------------------------


def _openai_chat_completion(content, model="llama-3.1-8b"):
    """An OpenAI Chat Completions response body (openai / openai_compatible)."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _ollama_chat_response(content, model="llama3"):
    """An Ollama /api/chat (non-streaming) response body."""
    return {
        "model": model,
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
    }


# ---------------------------------------------------------------------------
# openai_compatible (llama.cpp / vLLM / LM Studio) — TRUE respx HTTP
# ---------------------------------------------------------------------------


class TestOpenAICompatibleTransport:
    """complete() against a fake OpenAI-compatible server over real HTTP."""

    @respx.mock
    async def test_complete_happy_path_roundtrips_v1_base(self):
        """A /v1 base URL round-trips intact and content flows back.

        Regression guard for issue #751: the OpenAI client must hit
        ``{api_base}/chat/completions`` with the pasted ``/v1`` preserved
        exactly once (no ``/v1/v1`` duplication, no stripped ``/v1``).
        """
        route = respx.post(
            "http://local-llm.test/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=_openai_chat_completion("hello world")))

        cfg = LLMConfig(
            provider="openai_compatible",
            model="llama-3.1-8b",
            api_key="",
            api_base="http://local-llm.test/v1",
        )
        out = await complete("Hello", config=cfg)

        assert out == "hello world"
        assert route.called
        # The normalized URL must be the pasted /v1 base + /chat/completions.
        assert str(route.calls.last.request.url) == (
            "http://local-llm.test/v1/chat/completions"
        )

    @respx.mock
    async def test_complete_strips_thinking_tags_over_the_wire(self):
        """<think>...</think> reasoning is stripped from the transport output.

        deepseek-r1 / qwq style models emit reasoning wrapped in <think> tags
        before the real answer; complete() must return only the answer.
        """
        respx.post("http://local-llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_chat_completion("<think>reasoning here</think>actual answer")
            )
        )

        cfg = LLMConfig(
            provider="openai_compatible",
            model="deepseek-r1",
            api_key="",
            api_base="http://local-llm.test/v1",
        )
        out = await complete("Hello", config=cfg)

        assert out == "actual answer"

    @respx.mock
    async def test_complete_json_parses_fenced_json_over_the_wire(self):
        """complete_json runs the real _extract_json on transport output.

        The model returns JSON wrapped in a ```json code fence (a common LLM
        habit). complete_json must strip the fence and return the parsed dict.
        """
        fenced = '```json\n{"required_skills": ["Python"], "keywords": ["fastapi"]}\n```'
        route = respx.post("http://local-llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_completion(fenced))
        )

        cfg = LLMConfig(
            provider="openai_compatible",
            model="llama-3.1-8b",
            api_key="",
            api_base="http://local-llm.test/v1",
        )
        out = await complete_json("Extract keywords", config=cfg, schema_type="keywords")

        assert out == {"required_skills": ["Python"], "keywords": ["fastapi"]}
        assert route.called


# ---------------------------------------------------------------------------
# ollama — TRUE respx HTTP
# ---------------------------------------------------------------------------


class TestOllamaTransport:
    """complete() against a fake Ollama daemon over real HTTP."""

    @respx.mock
    async def test_complete_happy_path(self):
        """Ollama returns content via /api/chat and complete() surfaces it."""
        chat_route = respx.post("http://ollama.test:11434/api/chat").mock(
            return_value=httpx.Response(200, json=_ollama_chat_response("ollama says hi"))
        )

        cfg = LLMConfig(
            provider="ollama",
            model="llama3",
            api_key="",
            api_base="http://ollama.test:11434",
        )
        out = await complete("Hello", config=cfg)

        assert out == "ollama says hi"
        assert chat_route.called
        # The completion must target the user-configured host's /api/chat.
        assert str(chat_route.calls.last.request.url) == (
            "http://ollama.test:11434/api/chat"
        )

    @respx.mock
    async def test_complete_json_over_the_wire(self):
        """complete_json works against Ollama's /api/chat wire format."""
        body = '{"required_skills": ["Go"], "keywords": ["k8s"]}'
        chat_route = respx.post("http://ollama.test:11434/api/chat").mock(
            return_value=httpx.Response(200, json=_ollama_chat_response(body))
        )

        cfg = LLMConfig(
            provider="ollama",
            model="llama3",
            api_key="",
            api_base="http://ollama.test:11434",
        )
        out = await complete_json("Extract", config=cfg, schema_type="keywords")

        assert out == {"required_skills": ["Go"], "keywords": ["k8s"]}
        assert chat_route.called


# ---------------------------------------------------------------------------
# check_llm_health — TRUE respx HTTP
# ---------------------------------------------------------------------------


class TestCheckHealthTransport:
    """check_llm_health through the real LangChain provider integration."""

    @respx.mock
    async def test_health_success(self):
        """A 200 with content marks the provider healthy."""
        route = respx.post("http://local-llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_completion("pong"))
        )

        cfg = LLMConfig(
            provider="openai_compatible",
            model="llama-3.1-8b",
            api_key="",
            api_base="http://local-llm.test/v1",
        )
        res = await check_llm_health(config=cfg)

        assert res["healthy"] is True
        assert res["provider"] == "openai_compatible"
        assert route.called

    @respx.mock
    async def test_health_empty_content_is_unhealthy(self):
        """A 200 with empty content is reported unhealthy (error_code set)."""
        respx.post("http://local-llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_completion(""))
        )

        cfg = LLMConfig(
            provider="openai_compatible",
            model="llama-3.1-8b",
            api_key="",
            api_base="http://local-llm.test/v1",
        )
        res = await check_llm_health(config=cfg)

        assert res["healthy"] is False
        assert res["error_code"] == "empty_content"

    @respx.mock
    async def test_health_failure_scrubs_api_key_from_error_detail(self):
        """A 401 yields healthy=False, an error_code, and a key-scrubbed detail.

        The fake provider echoes the configured ``sk-`` key in its error body
        (as the real OpenAI API does). With ``include_details=True`` the
        upstream message is surfaced as ``error_detail`` — but every ``sk-``
        token MUST be redacted so a Settings-page viewer can't read the key
        back out.
        """
        leaking_key = "sk-abcd1234efgh5678ijkl9012"
        respx.post("http://api.openai.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": {
                        "message": (
                            f"Incorrect API key provided: {leaking_key}. "
                            "You can find your API key at ..."
                        ),
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
            )
        )

        cfg = LLMConfig(
            provider="openai",
            model="gpt-4",
            api_key=leaking_key,
            api_base="http://api.openai.test/v1",
        )
        res = await check_llm_health(config=cfg, include_details=True)

        assert res["healthy"] is False
        # A provider auth failure (401) falls through to the generic failure
        # code — assert the specific value, not just "truthy", so a silent
        # rename of the code is caught.
        assert res["error_code"] == "health_check_failed"
        # The raw key must never reach the client, even partially.
        detail = res.get("error_detail") or ""
        assert leaking_key not in detail
        assert "sk-abcd1234" not in detail
        assert "<redacted>" in detail
