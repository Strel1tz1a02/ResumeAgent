import asyncio
import json

import pytest

from evaluation.suites import resume_generation


def test_resume_generation_agent_rejects_non_json_input():
    with pytest.raises(ValueError, match="input 必须是 JSON 字符串"):
        asyncio.run(resume_generation.run_agent("not-json"))


def test_resume_generation_metrics_are_explicitly_pending():
    with pytest.raises(NotImplementedError, match="定义评估方法"):
        resume_generation.create_metrics()


def test_resume_generation_agent_rejects_non_object_json():
    with pytest.raises(ValueError, match="JSON 必须是对象"):
        asyncio.run(resume_generation.run_agent("[]"))


def test_resume_generation_agent_calls_real_http_boundary(monkeypatch):
    received = []

    async def fake_request(payload):
        received.append(payload)
        return {
            "run_id": "run-1",
            "resume_data": {"summary": "真实结果"},
            "provenance": {"bullets": []},
            "validation": {"valid": True},
        }

    monkeypatch.setattr(resume_generation, "_request_preview", fake_request)
    result = asyncio.run(resume_generation.run_agent('{"jd_information_id": 1, "mode": "llm"}'))
    assert json.loads(result["actual_output"]) == {"summary": "真实结果"}
    assert result["run_id"] == "run-1"
    assert received == [{"jd_information_id": 1, "mode": "llm"}]
