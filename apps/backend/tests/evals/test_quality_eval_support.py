"""质量 Eval 的独立 Judge 配置契约。"""

from typing import Any

import pytest

from app.llm import LLMConfig
from tests.evals import quality_eval_support as support

_JUDGE_ENV_NAMES = (
    "RM_EVAL_JUDGE_PROVIDER",
    "RM_EVAL_JUDGE_MODEL",
    "RM_EVAL_JUDGE_API_BASE",
    "RM_EVAL_JUDGE_REASONING_EFFORT",
)


@pytest.fixture(autouse=True)
def _clear_judge_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _runtime_config() -> LLMConfig:
    return LLMConfig(
        provider="deepseek",
        model="deepseek-generator",
        api_key="runtime-key",
        api_base="https://runtime.invalid/v1",
        reasoning_effort="minimal",
    )


def test_eval_judge_defaults_to_runtime_model() -> None:
    runtime = _runtime_config()

    judge = support.get_eval_judge_config(runtime)

    assert judge is runtime
    assert support.judge_model_relation(runtime, judge) == "same_runtime_model"


def test_eval_judge_can_use_distinct_model_on_same_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_config()
    monkeypatch.setenv("RM_EVAL_JUDGE_MODEL", "deepseek-judge")

    judge = support.get_eval_judge_config(runtime)

    assert judge.model == "deepseek-judge"
    assert judge.api_key == runtime.api_key
    assert judge.api_base == runtime.api_base
    assert judge.reasoning_effort == runtime.reasoning_effort
    assert support.judge_model_relation(runtime, judge) == "independent_model"


def test_eval_judge_same_model_with_different_effort_is_not_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_config()
    monkeypatch.setenv("RM_EVAL_JUDGE_MODEL", runtime.model)
    monkeypatch.setenv("RM_EVAL_JUDGE_REASONING_EFFORT", "high")

    judge = support.get_eval_judge_config(runtime)

    assert (
        support.judge_model_relation(runtime, judge)
        == "same_model_different_configuration"
    )


def test_eval_judge_provider_override_uses_its_stored_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_config()
    monkeypatch.setenv("RM_EVAL_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("RM_EVAL_JUDGE_MODEL", "gpt-judge")
    monkeypatch.setattr(
        support,
        "load_config_file",
        lambda: {"api_keys": {"openai": "judge-key"}},
    )

    judge = support.get_eval_judge_config(runtime)

    assert judge.provider == "openai"
    assert judge.model == "gpt-judge"
    assert judge.api_key == "judge-key"
    assert judge.api_base is None
    assert support.judge_model_relation(runtime, judge) == "independent_model"


def test_eval_judge_override_requires_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RM_EVAL_JUDGE_PROVIDER", "openai")

    with pytest.raises(ValueError, match="RM_EVAL_JUDGE_MODEL"):
        support.get_eval_judge_config(_runtime_config())


async def test_judge_outputs_uses_supplied_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    judge_config = _runtime_config().model_copy(update={"model": "deepseek-judge"})

    async def fake_complete_json(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "cases": [
                {
                    "case_name": "case-a",
                    "relevance": 4,
                    "grounding": 5,
                    "clarity": 4,
                    "instruction_fulfillment": 4,
                    "overall": 4,
                    "unsupported_claims": [],
                    "reasons": "内容接地且符合任务。",
                }
            ]
        }

    monkeypatch.setattr(support, "complete_json", fake_complete_json)

    result = await support.judge_outputs(
        "resume-generation",
        [{"case_name": "case-a", "candidate": {}}],
        config=judge_config,
    )

    assert captured["config"] is judge_config
    assert result[0].case_name == "case-a"
