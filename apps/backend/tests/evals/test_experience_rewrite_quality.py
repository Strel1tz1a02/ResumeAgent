"""经历改写评分器与生产 Tool Call 文案质量评测。"""

from dataclasses import asdict
from statistics import mean
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from app.ai_chat.context import ContextAssembler, ModelContext
from app.ai_chat.streaming.model import AiChatModel, complete_tool_calls
from app.ai_chat.tools.operation import RegisteredTool
from app.experience.prompts.ai_chat import system_prompt
from app.experience.tools.content_change import (
    ContentChangeArguments,
    ContentChangeOperation,
)
from tests.evals.golden.experience_rewrite_cases import EXPERIENCE_REWRITE_CASES
from tests.evals.quality_eval_support import (
    judge_outputs,
    model_metadata,
    require_llm,
)
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import score_rewrite


def test_rewrite_scorer_accepts_faithful_improvement() -> None:
    case = EXPERIENCE_REWRITE_CASES[0]
    score = score_rewrite(
        case["current_content"],
        case["user_request"],
        "负责支付网关性能治理，将 P99 延迟从 420ms 降至 180ms，吞吐量提升 35%。",
        required_fragments=case["required_fragments"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.fact_recall == 1.0
    assert score.grounded_number_precision == 1.0
    assert score.changed is True
    assert score.forbidden_hits == ()


def test_rewrite_scorer_finds_fact_loss_and_invention() -> None:
    case = EXPERIENCE_REWRITE_CASES[0]
    score = score_rewrite(
        case["current_content"],
        case["user_request"],
        "作为架构师带领10人团队，使营收提升 80%。",
        required_fragments=case["required_fragments"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.fact_recall == 0.0
    assert "80%" in score.invented_numbers
    assert {"10人团队", "营收", "架构师"} <= set(score.forbidden_hits)


def test_experience_rewrite_golden_cases_have_unique_names() -> None:
    names = [case["name"] for case in EXPERIENCE_REWRITE_CASES]

    assert len(EXPERIENCE_REWRITE_CASES) >= 2
    assert len(names) == len(set(names))


class _EmptyMemory:
    async def get_history_prompt(self, run_id: int, occupied_tokens: int) -> str:
        del run_id, occupied_tokens
        return ""


async def _rewrite_candidate(case: dict[str, Any]) -> dict[str, Any]:
    """走生产 Prompt、ContextAssembler、Tool Schema 与流式 Tool Call。"""
    registered = RegisteredTool(ContentChangeOperation())
    context: ModelContext = {
        "instructions": system_prompt(case["language"], case["scope"]),
        "domain_sections": [
            {
                "name": "saved_experience",
                "data": {
                    "experience": case["saved_experience"],
                    "scope": {"field": case["scope"]},
                    "scope_status": "complete",
                    "scope_revision": 0,
                },
            }
        ],
        "messages": [{"role": "user", "content": case["user_request"]}],
        "pending_tool_results": [],
    }
    messages = await ContextAssembler(memory=_EmptyMemory()).assemble(  # type: ignore[arg-type]
        run_id=1,
        context=context,
        tools=[convert_to_openai_tool(registered.tool)],
    )
    response = AIMessageChunk(content="")
    async for chunk in AiChatModel().stream(
        messages=messages,
        tools={registered.name: registered.tool},
        tools_enabled=True,
    ):
        response += chunk
    calls = complete_tool_calls(response)
    if len(calls) != 1:
        raise ValueError(
            f"模型应产生一个 content_change Tool Call，实际为 {len(calls)}"
        )
    arguments = ContentChangeArguments.model_validate(calls[0][1]["args"])
    if (
        arguments.scope.field != case["scope"]
        or arguments.scope.evidence_id is not None
    ):
        raise ValueError("模型修改了会话绑定 scope")
    suggested = arguments.suggested_content
    if isinstance(suggested, BaseModel):
        suggested = suggested.model_dump(mode="json")
    return {
        "suggested_content": suggested,
        "visible_text": response.content,
        "scope": arguments.scope.model_dump(mode="json"),
    }


@pytest.mark.eval
async def test_experience_rewrite_quality_meets_golden_thresholds() -> None:
    """真实模型必须通过生产 content_change Tool 给出忠实改写。"""
    config = require_llm()  # 必须先 gate。
    thresholds = {
        "minimum_fact_recall": 1.0,
        "minimum_grounded_number_precision": 1.0,
        "forbidden_hits": 0,
        "minimum_judge_overall": 4,
        "minimum_judge_grounding": 4,
        "minimum_instruction_fulfillment": 4,
    }
    reports: list[dict[str, Any]] = []
    judge_inputs: list[dict[str, Any]] = []
    for case in EXPERIENCE_REWRITE_CASES:
        try:
            candidate = await _rewrite_candidate(case)
            suggested = candidate["suggested_content"]
            score = score_rewrite(
                case["current_content"],
                case["user_request"],
                suggested,
                required_fragments=case["required_fragments"],
                forbidden_fragments=case["forbidden_fragments"],
            )
            type_matches = (
                isinstance(suggested, str)
                if case["expected_type"] == "str"
                else isinstance(suggested, list)
            )
            reports.append(
                {
                    "name": case["name"],
                    "objective_metrics": asdict(score),
                    "type_matches": type_matches,
                    "candidate": candidate,
                }
            )
            judge_inputs.append(
                {
                    "case_name": case["name"],
                    "task": case["user_request"],
                    "source": {
                        "current_content": case["current_content"],
                        "saved_experience": case["saved_experience"],
                    },
                    "candidate": suggested,
                }
            )
        except Exception as error:  # noqa: BLE001 - 继续其余案例并统一报告
            reports.append(
                {"name": case["name"], "error": f"{type(error).__name__}: {error}"}
            )

    try:
        judgments = await judge_outputs("experience-rewrite", judge_inputs)
        judgment_by_name = {item.case_name: item for item in judgments}
        for report in reports:
            if report["name"] in judgment_by_name:
                report["judge"] = judgment_by_name[report["name"]].model_dump(
                    mode="json"
                )
    except Exception as error:  # noqa: BLE001 - Judge 错误也属于评测结果
        judgments = []
        reports.append({"name": "judge", "error": f"{type(error).__name__}: {error}"})

    completed = [item for item in reports if "objective_metrics" in item]
    summary = {
        "completed_cases": len(completed),
        "total_cases": len(EXPERIENCE_REWRITE_CASES),
        "judged_cases": len(judgments),
        "mean_fact_recall": mean(
            item["objective_metrics"]["fact_recall"] for item in completed
        )
        if completed
        else 0.0,
    }
    path = write_quality_report(
        "experience-rewrite",
        model=model_metadata(config),
        thresholds=thresholds,
        cases=reports,
        summary=summary,
    )
    assert len(completed) == len(EXPERIENCE_REWRITE_CASES), path
    assert len(judgments) == len(EXPERIENCE_REWRITE_CASES), path
    judgment_by_name = {item.case_name: item for item in judgments}
    for report in completed:
        metrics = report["objective_metrics"]
        judgment = judgment_by_name[report["name"]]
        assert report["type_matches"] is True, path
        assert metrics["changed"] is True, path
        assert metrics["fact_recall"] >= thresholds["minimum_fact_recall"], path
        assert (
            metrics["grounded_number_precision"]
            >= thresholds["minimum_grounded_number_precision"]
        ), path
        assert not metrics["forbidden_hits"], path
        assert judgment.grounding >= thresholds["minimum_judge_grounding"], path
        assert (
            judgment.instruction_fulfillment
            >= thresholds["minimum_instruction_fulfillment"]
        ), path
        assert judgment.overall >= thresholds["minimum_judge_overall"], path
        assert not judgment.unsupported_claims, path
