"""Golden conversation for measuring memory retention, safety, and shrinkage."""

from __future__ import annotations

from typing import Any


MEMORY_COMPACTION_CASE: dict[str, Any] = {
    "name": "preference_revision_and_fact_boundary",
    "runs": [
        {
            "run_id": 101,
            "kind": "user_turn",
            "status": "completed",
            "error_code": None,
            "messages": (
                {
                    "role": "user",
                    "status": "completed",
                    "content": (
                        "我的目标是申请后端工程师。回答尽量简洁，不要捏造经历。"
                        "我的上一家公司是星海科技，做过支付平台。"
                    ),
                },
                {
                    "role": "assistant",
                    "status": "completed",
                    "content": "明白，我会围绕后端岗位整理。",
                },
            ),
            "tool_calls": (),
        },
        {
            "run_id": 102,
            "kind": "user_turn",
            "status": "completed",
            "error_code": None,
            "messages": (
                {
                    "role": "user",
                    "status": "completed",
                    "content": (
                        "确认采用一页中文简历。还有一个问题：项目经历要不要单列？"
                    ),
                },
                {
                    "role": "assistant",
                    "status": "completed",
                    "content": "建议先确认目标岗位再决定。",
                },
            ),
            "tool_calls": (
                {
                    "tool_call_index": 0,
                    "tool_name": "content_change",
                    "arguments": {"employer": "星海科技", "metric": "提升50%"},
                    "status": "resolved",
                    "decision": "approve",
                    "result": {"outcome": "changed", "revision": 7},
                },
            ),
        },
        {
            "run_id": 103,
            "kind": "user_turn",
            "status": "completed",
            "error_code": None,
            "messages": (
                {
                    "role": "user",
                    "status": "completed",
                    "content": (
                        "偏好更新：可以详细解释，但最终答案先给结论。"
                        "项目经历的问题已经解决，不再是开放问题。"
                    ),
                },
                {
                    "role": "assistant",
                    "status": "completed",
                    "content": "收到，以新偏好为准。",
                },
            ),
            "tool_calls": (),
        },
        {
            "run_id": 104,
            "kind": "user_turn",
            "status": "failed",
            "error_code": "provider_failed",
            "messages": (
                {
                    "role": "user",
                    "status": "completed",
                    "content": "这轮只是测试失败恢复，不新增长期信息。",
                },
                {
                    "role": "assistant",
                    "status": "failed",
                    "content": "我猜用户只接受英文，而且目标是产品经理。",
                },
            ),
            "tool_calls": (),
        },
    ],
    "expected_fact_aliases": [
        ["后端工程师", "后端岗位"],
        ["不要捏造经历", "不捏造经历", "不得编造经历"],
        ["一页中文简历", "中文一页简历"],
        ["详细解释", "解释可以详细"],
        ["先给结论", "结论优先"],
    ],
    "forbidden_phrases": [
        "星海科技",
        "支付平台",
        "提升50%",
        "revision 7",
        "尽量简洁",
        "项目经历要不要单列",
        "只接受英文",
        "产品经理",
    ],
    "minimum_fact_recall": 0.8,
    "maximum_compression_ratio": 0.35,
}
