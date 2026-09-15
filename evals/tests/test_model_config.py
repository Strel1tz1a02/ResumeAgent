import pytest

from evaluation.model_config import get_eval_model


def test_eval_model_maps_separate_credentials(monkeypatch):
    monkeypatch.setenv("EVAL_MODEL", "judge-model")
    monkeypatch.setenv("EVAL_API_KEY", "judge-key")
    monkeypatch.setenv("EVAL_API_BASE", "https://judge.example/v1")
    assert get_eval_model() == "judge-model"
    assert __import__("os").environ["OPENAI_API_KEY"] == "judge-key"
    assert __import__("os").environ["OPENAI_BASE_URL"] == "https://judge.example/v1"


def test_eval_model_requires_credentials(monkeypatch):
    monkeypatch.delenv("EVAL_MODEL", raising=False)
    monkeypatch.delenv("EVAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MODEL"):
        get_eval_model()


def test_eval_model_falls_back_to_agent_credentials(monkeypatch):
    monkeypatch.delenv("EVAL_MODEL", raising=False)
    monkeypatch.delenv("EVAL_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "shared-model")
    monkeypatch.setenv("LLM_API_KEY", "shared-key")
    assert get_eval_model() == "shared-model"
    assert __import__("os").environ["OPENAI_API_KEY"] == "shared-key"
