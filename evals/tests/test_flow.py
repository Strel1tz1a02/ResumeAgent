import asyncio
import sys
from types import SimpleNamespace

import pytest

from evaluation.flow import run_cases


@pytest.fixture(autouse=True)
def sdk(monkeypatch):
    # 只替代 SDK 数据容器；不调用模型、不伪造实际业务评估结果。
    monkeypatch.setitem(sys.modules, "deepeval.test_case",
                        SimpleNamespace(LLMTestCase=lambda **kw: SimpleNamespace(**kw),
                                        ToolCall=lambda **kw: kw))


class Metric:
    __name__ = "test-metric"
    score = 1.0
    reason = "test-only"

    def measure(self, case):
        assert case.actual_output == "answer"
        assert case.expected_output == "reference"
        assert case.tools_called == [{"name": "search"}]

    def is_successful(self):
        return True


def test_flow_preserves_evidence_and_keeps_reference_from_agent():
    received = []

    async def agent(value):
        received.append(value)
        return {"actual_output": "answer", "tools_called": [{"name": "search"}],
                "retrieval_context": ["evidence"]}

    result = asyncio.run(run_cases(
        [{"input": "question", "expected_output": "reference"}], agent, lambda: [Metric()]))
    assert received == ["question"]
    assert result[0]["status"] == "passed"
    assert result[0]["output"]["retrieval_context"] == ["evidence"]
    assert result[0]["metrics"][0]["score"] == 1


def test_execution_and_scoring_errors_do_not_drop_cases():
    class BrokenMetric(Metric):
        def measure(self, case):
            raise RuntimeError("judge unavailable")

    def agent(value):
        if value == "broken":
            raise RuntimeError("agent unavailable")
        return "answer"

    results = asyncio.run(run_cases(
        [{"input": "broken"}, {"input": "ok"}], agent, lambda: [BrokenMetric()]))
    assert len(results) == 2
    assert results[0]["error"]["stage"] == "execution"
    assert results[1]["metrics"][0]["error"] == "judge unavailable"
    assert all(row["status"] == "error" for row in results)


def test_empty_metrics_cannot_report_pass():
    results = asyncio.run(run_cases([{"input": "q"}], lambda _: "answer", lambda: []))
    assert results[0]["status"] == "error"


def test_langfuse_receives_output_and_score():
    from contextlib import contextmanager
    updates, scores = [], []

    class Span:
        trace_id = "trace-1"
        def update(self, **kw):
            updates.append(kw)
        def score(self, **kw):
            scores.append(kw)

    class Client:
        @contextmanager
        def start_as_current_observation(self, **kw):
            yield Span()

    results = asyncio.run(run_cases(
        [{"input": "q", "expected_output": "reference"}],
        lambda _: {"actual_output": "answer", "tools_called": [{"name": "search"}]},
        lambda: [Metric()], client=Client()))
    assert results[0]["trace_id"] == "trace-1"
    assert updates[0]["output"]["actual_output"] == "answer"
    assert scores[0]["value"] == 1


def test_cli_writes_results_and_returns_failure(tmp_path, monkeypatch):
    import json
    from evaluation.cli import main

    data = tmp_path / "cases.jsonl"
    data.write_text('{"input": "question"}\n', encoding="utf-8")
    output = tmp_path / "results.json"
    monkeypatch.setitem(sys.modules, "test_suite",
                        SimpleNamespace(run_agent=lambda _: "answer", create_metrics=lambda: []))
    monkeypatch.setattr(sys, "argv", ["evals", str(data), "--suite", "test_suite",
                                    "--output", str(output)])
    assert main() == 1
    results = json.loads(output.read_text(encoding="utf-8"))
    assert results[0]["status"] == "error"
    assert results[0]["output"]["actual_output"] == "answer"
