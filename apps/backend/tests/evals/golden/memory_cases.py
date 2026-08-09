"""会话记忆压缩的十个黄金语义案例。"""

from __future__ import annotations

from typing import Any


def _run(
    run_id: int,
    user: str,
    assistant: str,
    *,
    status: str = "completed",
    tool_calls: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    assistant_status = "completed" if status == "completed" else "failed"
    return {
        "run_id": run_id,
        "kind": "user_turn",
        "status": status,
        "error_code": "provider_failed" if status == "failed" else None,
        "messages": (
            {"role": "user", "status": "completed", "content": user},
            {
                "role": "assistant",
                "status": assistant_status,
                "content": assistant,
            },
        ),
        "tool_calls": tool_calls,
    }


def _case(
    name: str,
    runs: list[dict[str, Any]],
    required_claims: list[tuple[str, str]],
    *,
    stale: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "runs": runs,
        "oracle": {
            "required_claims": [
                {"id": claim_id, "requirement": requirement}
                for claim_id, requirement in required_claims
            ],
            "resolved_or_stale_claims": stale or [],
            "forbidden_information": forbidden or [],
        },
        "minimum_preserved_ratio": 0.8,
        "minimum_judge_score": 4,
        "maximum_compression_ratio": 0.35,
    }


MEMORY_COMPACTION_CASES: list[dict[str, Any]] = [
    _case(
        "preference_revision_and_fact_boundary",
        [
            _run(
                101,
                "我的目标是申请后端工程师。回答尽量简洁，不要捏造经历。我的上一家公司是星海科技，做过支付平台。",
                "明白，我会围绕后端岗位整理。",
            ),
            _run(
                102,
                "确认采用一页中文简历。还有一个问题：项目经历要不要单列？",
                "建议先确认目标岗位再决定。",
                tool_calls=(
                    {
                        "tool_call_index": 0,
                        "tool_name": "content_change",
                        "arguments": {"employer": "星海科技", "metric": "提升50%"},
                        "status": "resolved",
                        "decision": "approve",
                        "result": {"outcome": "changed", "revision": 7},
                    },
                ),
            ),
            _run(
                103,
                "偏好更新：可以详细解释，但最终答案先给结论。项目经历的问题已经解决，不再是开放问题。",
                "收到，以新偏好为准。",
            ),
            _run(
                104,
                "这轮只是测试失败恢复，不新增长期信息。",
                "我猜用户只接受英文，而且目标是产品经理。",
                status="failed",
            ),
        ],
        [
            ("current_goal", "用户当前目标是申请后端工程师岗位。"),
            ("truthfulness_constraint", "用户要求不能捏造其经历。"),
            ("resume_format_decision", "已经确认使用一页中文简历。"),
            ("explanation_preference", "用户当前允许进行详细解释。"),
            ("answer_order_preference", "最终回答应先给结论。"),
        ],
        stale=[
            "早期的简洁回答偏好已经被后续偏好替代。",
            "是否单列项目经历已经解决，不再是开放问题。",
        ],
        forbidden=[
            "Experience/Resume 领域事实，例如公司、项目和业绩指标。",
            "Tool 的参数、结果、状态和 revision。",
            "失败助手关于只接受英文和目标是产品经理的猜测。",
        ],
    ),
    _case(
        "response_language_revision",
        [
            _run(
                201,
                "后续请只用英文回答。我曾在远山网络负责搜索服务。",
                "Understood.",
            ),
            _run(
                202,
                "语言偏好改成中文，但专业术语保留英文原文。",
                "好的，后续使用中文并保留英文术语。",
            ),
        ],
        [
            ("response_language", "当前回答语言是中文。"),
            ("term_style", "专业术语需要保留英文原文。"),
        ],
        stale=["只使用英文回答的旧偏好已经失效。"],
        forbidden=["远山网络和搜索服务属于经历事实，不能进入会话记忆。"],
    ),
    _case(
        "current_goal_switch",
        [
            _run(301, "我的目标是申请数据分析师。", "我会按数据分析岗位准备。"),
            _run(
                302,
                "计划变了，目标改为机器学习工程师，不再按数据分析师准备。",
                "收到，切换为机器学习工程师目标。",
            ),
        ],
        [("current_goal", "当前目标是申请机器学习工程师。")],
        stale=["申请数据分析师的旧目标已经失效。"],
    ),
    _case(
        "open_question_becomes_decision",
        [
            _run(
                401,
                "简历做一页还是两页还没决定，请先记为待确认。",
                "好的，暂时作为开放问题。",
            ),
            _run(
                402,
                "已经确认使用两页简历，这个问题关闭。",
                "收到，使用两页版本。",
            ),
        ],
        [("resume_length_decision", "已经确认使用两页简历。")],
        stale=["一页还是两页不再是开放问题。"],
    ),
    _case(
        "constraints_accumulate",
        [
            _run(
                501,
                "不要夸大成果，也不要新增我没有提供的数字。",
                "明白，我会保持事实准确。",
            ),
            _run(502, "另外，所有最终回答保持中文。", "好的。"),
        ],
        [
            ("no_exaggeration", "不能夸大用户成果。"),
            ("no_unprovided_metrics", "不能新增用户未提供的数字。"),
            ("chinese_output", "最终回答必须使用中文。"),
        ],
    ),
    _case(
        "preference_retraction",
        [
            _run(601, "每次解释都给完整代码示例。", "好的，我会附完整代码。"),
            _run(
                602,
                "取消完整代码示例这个偏好，以后只在必要时给伪代码。",
                "收到，只在必要时提供伪代码。",
            ),
        ],
        [("pseudocode_preference", "只有必要时才提供伪代码。")],
        stale=["每次都给完整代码示例的旧偏好已经撤销。"],
    ),
    _case(
        "failed_assistant_hallucination",
        [
            _run(701, "当前目标是梳理项目经验。", "我们开始整理项目。"),
            _run(
                702,
                "这轮请求中断，不增加任何长期偏好。",
                "用户偏好英文，并且未来想做管理岗位。",
                status="failed",
            ),
        ],
        [("current_goal", "当前目标是梳理项目经验。")],
        forbidden=["失败助手关于英文偏好和管理岗位的猜测。"],
    ),
    _case(
        "tool_payload_boundary",
        [
            _run(
                801,
                "以后最终答案先列风险，再给建议。",
                "收到。",
                tool_calls=(
                    {
                        "tool_call_index": 0,
                        "tool_name": "content_change",
                        "arguments": {"company": "云帆科技", "revenue": "300万"},
                        "status": "resolved",
                        "decision": "approve",
                        "result": {"outcome": "changed", "revision": 12},
                    },
                ),
            ),
        ],
        [("answer_order", "最终答案需要先列风险，再给建议。")],
        forbidden=["Tool 中的公司、营收、执行结果和 revision。"],
    ),
    _case(
        "partial_question_resolution",
        [
            _run(
                901,
                "还有两个问题：是否先写英文版？是否保留项目小节？",
                "我会保留这两个待确认项。",
            ),
            _run(
                902,
                "已经决定先写英文版；是否保留项目小节还要确认。",
                "收到，只保留项目小节这个开放问题。",
            ),
        ],
        [
            ("english_version_decision", "已经决定先写英文版。"),
            ("project_section_question", "是否保留项目小节仍是开放问题。"),
        ],
        stale=["是否先写英文版不再是开放问题。"],
    ),
    _case(
        "answer_order_and_format_revision",
        [
            _run(
                1001,
                "回答时先解释过程，再给结论。",
                "好的，我会先展开过程。",
            ),
            _run(
                1002,
                "更新偏好：先给结论，必要时再解释，而且不要使用表格。",
                "收到，以新顺序和格式为准。",
            ),
        ],
        [
            ("answer_order", "回答需要先给结论，必要时再解释。"),
            ("no_tables", "回答不能使用表格。"),
        ],
        stale=["先解释过程再给结论的旧偏好已经失效。"],
    ),
]
