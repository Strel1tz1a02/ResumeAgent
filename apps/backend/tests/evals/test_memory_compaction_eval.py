"""On-demand LLM-judged evaluation for conversation-memory compaction."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_chat.memory.operations import apply_operations
from app.ai_chat.memory.runs import Memory, OriginRun
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import (
    build_memory_token_budget,
    count_text_tokens,
)
from app.llm import LLMConfig, complete_json, get_llm_config
from tests.evals.golden.memory_cases import MEMORY_COMPACTION_CASES


class ClaimJudgment(BaseModel):
    """Judge verdict for one manually labelled semantic claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: Literal["preserved", "partial", "missing", "contradicted"]
    evidence: str | None = None
    reason: str


class MemoryJudgment(BaseModel):
    """Structured result returned by the independent evaluator prompt."""

    model_config = ConfigDict(extra="forbid")

    claim_assessments: list[ClaimJudgment]
    unsupported_claims: list[str] = Field(default_factory=list)
    stale_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    overall_score: int = Field(ge=1, le=5)
    summary: str


def _needs_key() -> LLMConfig:
    """Skip before constructing a request unless a usable provider is configured."""
    try:
        config = get_llm_config()
    except Exception as exc:
        pytest.skip(f"could not read LLM config ({exc}); skipping memory eval")
    if not config.api_key and config.provider not in ("ollama", "openai_compatible"):
        pytest.skip("no LLM key configured; set one to run memory evals")
    return config


def _token_count(value: object) -> int:
    spec = build_memory_token_budget()
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return count_text_tokens(spec, payload)


def _judge_prompt(case: dict[str, object], memory: dict[str, object]) -> str:
    """Build a semantic evaluation prompt without keyword or alias matching."""
    return """你是独立的会话记忆质量评审员。请判断最终 MEMORY 是否准确表达原始 RUNS 的当前有效状态。

评审原则：
1. ORACLE 是人工标注的语义要求，不要求 MEMORY 使用相同措辞。
2. 对每个 required_claims 必须返回一条 assessment，不能遗漏或增加 claim_id。
3. preserved 表示语义完整；partial 表示信息被弱化或只有部分；missing 表示未表达；contradicted 表示含义相反。
4. evidence 必须逐字引用 MEMORY；没有证据时必须为 null，不能引用原始 RUNS。
5. unsupported_claims 列出 MEMORY 中没有原始用户依据的声明。
6. stale_claims 列出已经被更新、撤销或解决却仍留在 MEMORY 的声明。
7. forbidden_claims 列出进入 MEMORY 的领域事实、Tool 数据或助手猜测。
8. 不要因为 MEMORY 没保存领域事实而扣分；这些信息本来就不属于会话记忆。
9. overall_score 使用 1-5 分：5 表示完整、当前、无越界；任何矛盾或 forbidden_claim 都不得高于 2 分。
10. reason 和 evidence 都要简短，每项不超过 60 个汉字，避免输出被截断。
11. 顶层只能包含示例中的 6 个字段。不要直接返回 MEMORY，也不要返回 Operations。

只返回以下 JSON：
{
  "claim_assessments": [
    {"claim_id":"...","verdict":"preserved|partial|missing|contradicted","evidence":"...或null","reason":"..."}
  ],
  "unsupported_claims": [],
  "stale_claims": [],
  "forbidden_claims": [],
  "overall_score": 1,
  "summary": "..."
}

ORIGINAL_RUNS
""" + json.dumps(case["runs"], ensure_ascii=False) + """
END_ORIGINAL_RUNS

ORACLE
""" + json.dumps(case["oracle"], ensure_ascii=False) + """
END_ORACLE

MEMORY
""" + json.dumps(memory, ensure_ascii=False) + """
END_MEMORY
"""


async def _judge_memory(
    case: dict[str, object],
    memory: dict[str, object],
    max_attempts: int = 3,
) -> tuple[MemoryJudgment | None, list[dict[str, object]]]:
    """Retry on both malformed JSON and judge-schema drift."""
    attempts: list[dict[str, object]] = []
    prompt = _judge_prompt(case, memory)
    for attempt_number in range(1, max_attempts + 1):
        retry_hint = ""
        if attempt_number > 1:
            retry_hint = (
                "\n\n上一次输出无效。重新独立完成评审：必须返回全部 required_claims，"
                "顶层必须是 claim_assessments、unsupported_claims、stale_claims、"
                "forbidden_claims、overall_score、summary，禁止直接复制 MEMORY。"
            )
        try:
            raw = await complete_json(
                prompt + retry_hint,
                system_prompt="你是严格、独立、以证据为准的会话记忆评审员。",
                max_tokens=4096,
                retries=0,
                schema_type="memory_judge",
            )
        except Exception as exc:
            attempts.append(
                {
                    "attempt": attempt_number,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        try:
            judgment = MemoryJudgment.model_validate(raw)
        except ValidationError as exc:
            attempts.append(
                {
                    "attempt": attempt_number,
                    "raw_judgment": raw,
                    "validation_error": str(exc),
                }
            )
            continue
        attempts.append(
            {
                "attempt": attempt_number,
                "raw_judgment": raw,
                "valid": True,
            }
        )
        return judgment, attempts
    return None, attempts


def _claim_set_is_exact(
    judgment: MemoryJudgment, expected_ids: set[str]
) -> bool:
    actual_ids = [item.claim_id for item in judgment.claim_assessments]
    return (
        len(actual_ids) == len(set(actual_ids))
        and set(actual_ids) == expected_ids
    )


def _write_report(
    report: dict[str, object],
    result_root: Path | None = None,
) -> Path:
    """Persist one immutable, timestamped report and return its path."""
    root = result_root or Path(__file__).parent / "results" / "memory"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    case = report["case"]
    case_name = str(case["name"] if isinstance(case, dict) else case)
    case_name = case_name.replace("_", "-")
    path = root / f"{timestamp}_{case_name}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_memory_judgment_schema_rejects_unknown_verdict() -> None:
    """Keep judge output machine-checkable even though its reasoning is semantic."""
    with pytest.raises(ValidationError):
        MemoryJudgment.model_validate(
            {
                "claim_assessments": [
                    {
                        "claim_id": "goal",
                        "verdict": "looks_good",
                        "evidence": None,
                        "reason": "invalid verdict",
                    }
                ],
                "overall_score": 5,
                "summary": "invalid",
            }
        )


def test_memory_golden_suite_has_ten_unique_cases() -> None:
    names = [case["name"] for case in MEMORY_COMPACTION_CASES]

    assert len(names) == 10
    assert len(set(names)) == 10
    assert all(case["oracle"]["required_claims"] for case in MEMORY_COMPACTION_CASES)


def test_report_is_persisted_as_utf8_json(tmp_path: Path) -> None:
    report = {
        "model": {"provider": "test"},
        "metrics": {"passed": True},
        "checks": {},
        "case": {"name": "中文_case", "version": 1},
        "data": {},
        "metadata": {"schema_version": 2},
    }
    path = _write_report(report, tmp_path)

    assert path.parent == tmp_path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == report
    assert list(loaded) == [
        "model",
        "metrics",
        "checks",
        "case",
        "data",
        "metadata",
    ]


async def test_judge_retries_when_model_returns_memory_instead_of_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {"current_goal": "错误地直接返回 Memory"},
        {
            "claim_assessments": [
                {
                    "claim_id": "goal",
                    "verdict": "preserved",
                    "evidence": "目标",
                    "reason": "语义完整",
                }
            ],
            "unsupported_claims": [],
            "stale_claims": [],
            "forbidden_claims": [],
            "overall_score": 5,
            "summary": "通过",
        },
    ]

    async def fake_complete_json(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    monkeypatch.setattr(
        "tests.evals.test_memory_compaction_eval.complete_json",
        fake_complete_json,
    )
    judgment, attempts = await _judge_memory(
        {"runs": [], "oracle": {}},
        {},
    )

    assert judgment is not None and judgment.overall_score == 5
    assert len(attempts) == 2
    assert "validation_error" in attempts[0]
    assert attempts[1]["valid"] is True


@pytest.mark.eval
@pytest.mark.parametrize(
    "case",
    MEMORY_COMPACTION_CASES,
    ids=lambda case: case["name"],
)
async def test_llm_judges_real_memory_compaction_quality(
    case: dict[str, Any],
) -> None:
    config = _needs_key()  # Must remain first: no ungated provider call.

    document = Memory()
    summarizer = MemorySummarizer()
    bundles = [OriginRun(**run) for run in case["runs"]]
    source_runs = [bundle.history_record() for bundle in bundles]
    source_tokens = _token_count(source_runs)
    compaction_trace: list[dict[str, object]] = []

    for bundle in bundles:
        try:
            operations = await summarizer.summarize(document, bundle)
        except Exception as exc:
            partial_memory = document.content_json()
            partial_tokens = _token_count(partial_memory)
            failure_report: dict[str, object] = {
                "model": {
                    "provider": config.provider,
                    "model": config.model,
                    "reasoning_effort": config.reasoning_effort,
                },
                "metrics": {
                    "passed": False,
                    "expected_claim_count": len(
                        case["oracle"]["required_claims"]
                    ),
                    "preserved_claim_count": None,
                    "preserved_ratio": None,
                    "judge_score": None,
                    "unsupported_claim_count": None,
                    "stale_claim_count": None,
                    "forbidden_claim_count": None,
                    "source_tokens": source_tokens,
                    "memory_tokens": partial_tokens,
                    "saved_tokens": source_tokens - partial_tokens,
                    "compression_ratio": partial_tokens / source_tokens,
                    "token_reduction_ratio": 1 - partial_tokens / source_tokens,
                    "judge_attempt_count": 0,
                },
                "checks": {"compaction_completed": False},
                "case": {
                    "name": case["name"],
                    "version": case["version"],
                    "thresholds": {
                        "minimum_preserved_ratio": case[
                            "minimum_preserved_ratio"
                        ],
                        "minimum_judge_score": case["minimum_judge_score"],
                        "maximum_compression_ratio": case[
                            "maximum_compression_ratio"
                        ],
                    },
                },
                "data": {
                    "source_runs": source_runs,
                    "oracle": case["oracle"],
                    "memory": partial_memory,
                    "compaction_trace": compaction_trace,
                    "compaction_failure": {
                        "run_id": bundle.run_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    "judgment": None,
                    "judge_attempts": [],
                },
                "metadata": {
                    "schema_version": 2,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            report_path = _write_report(failure_report)
            pytest.fail(f"memory compaction failed; report={report_path}")
        document = apply_operations(
            document,
            operations,
            run_id=bundle.run_id,
        )
        compaction_trace.append(
            {
                "run_id": bundle.run_id,
                "operations": [
                    operation.model_dump(mode="json", exclude_none=True)
                    for operation in operations
                ],
                "memory": document.content_json(),
            }
        )

    memory = document.content_json()
    memory_tokens = _token_count(memory)
    compression_ratio = memory_tokens / source_tokens
    token_reduction_ratio = 1 - compression_ratio
    saved_tokens = source_tokens - memory_tokens
    judgment, judge_attempts = await _judge_memory(case, memory)
    if judgment is None:
        invalid_report: dict[str, object] = {
            "model": {
                "provider": config.provider,
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
            },
            "metrics": {
                "passed": False,
                "expected_claim_count": len(
                    case["oracle"]["required_claims"]
                ),
                "preserved_claim_count": None,
                "preserved_ratio": None,
                "judge_score": None,
                "unsupported_claim_count": None,
                "stale_claim_count": None,
                "forbidden_claim_count": None,
                "source_tokens": source_tokens,
                "memory_tokens": memory_tokens,
                "saved_tokens": saved_tokens,
                "compression_ratio": compression_ratio,
                "token_reduction_ratio": token_reduction_ratio,
                "judge_attempt_count": len(judge_attempts),
            },
            "checks": {"judge_schema_valid": False},
            "case": {
                "name": case["name"],
                "version": case["version"],
                "thresholds": {
                    "minimum_preserved_ratio": case["minimum_preserved_ratio"],
                    "minimum_judge_score": case["minimum_judge_score"],
                    "maximum_compression_ratio": case["maximum_compression_ratio"],
                },
            },
            "data": {
                "source_runs": source_runs,
                "oracle": case["oracle"],
                "memory": memory,
                "compaction_trace": compaction_trace,
                "judgment": None,
                "judge_attempts": judge_attempts,
            },
            "metadata": {
                "schema_version": 2,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        report_path = _write_report(invalid_report)
        pytest.fail(f"judge failed after all attempts; report={report_path}")

    required_claims = case["oracle"]["required_claims"]
    expected_ids = {item["id"] for item in required_claims}
    claim_contract_ok = _claim_set_is_exact(judgment, expected_ids)
    preserved = sum(
        item.verdict == "preserved"
        for item in judgment.claim_assessments
    )
    preserved_ratio = preserved / len(expected_ids)
    checks = {
        "claim_contract": claim_contract_ok,
        "preserved_ratio": preserved_ratio >= case["minimum_preserved_ratio"],
        "no_unsupported_claims": judgment.unsupported_claims == [],
        "no_stale_claims": judgment.stale_claims == [],
        "no_forbidden_claims": judgment.forbidden_claims == [],
        "judge_score": judgment.overall_score >= case["minimum_judge_score"],
        "compression_ratio": (
            compression_ratio <= case["maximum_compression_ratio"]
        ),
    }

    report: dict[str, object] = {
        "model": {
            "provider": config.provider,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
        },
        "metrics": {
            "passed": all(checks.values()),
            "expected_claim_count": len(expected_ids),
            "preserved_claim_count": preserved,
            "preserved_ratio": preserved_ratio,
            "judge_score": judgment.overall_score,
            "unsupported_claim_count": len(judgment.unsupported_claims),
            "stale_claim_count": len(judgment.stale_claims),
            "forbidden_claim_count": len(judgment.forbidden_claims),
            "source_tokens": source_tokens,
            "memory_tokens": memory_tokens,
            "saved_tokens": saved_tokens,
            "compression_ratio": compression_ratio,
            "token_reduction_ratio": token_reduction_ratio,
            "judge_attempt_count": len(judge_attempts),
        },
        "checks": checks,
        "case": {
            "name": case["name"],
            "version": case["version"],
            "thresholds": {
                "minimum_preserved_ratio": case["minimum_preserved_ratio"],
                "minimum_judge_score": case["minimum_judge_score"],
                "maximum_compression_ratio": case["maximum_compression_ratio"],
            },
        },
        "data": {
            "source_runs": source_runs,
            "oracle": case["oracle"],
            "memory": memory,
            "compaction_trace": compaction_trace,
            "judgment": judgment.model_dump(mode="json"),
            "judge_attempts": judge_attempts,
        },
        "metadata": {
            "schema_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    report_path = _write_report(report)
    print(
        json.dumps(
            {"report_path": str(report_path), **report},
            ensure_ascii=False,
        )
    )

    assert claim_contract_ok, report
    assert preserved_ratio >= case["minimum_preserved_ratio"], report
    assert judgment.unsupported_claims == [], report
    assert judgment.stale_claims == [], report
    assert judgment.forbidden_claims == [], report
    assert judgment.overall_score >= case["minimum_judge_score"], report
    assert compression_ratio <= case["maximum_compression_ratio"], report
