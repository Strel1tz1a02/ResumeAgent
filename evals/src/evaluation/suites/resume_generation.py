"""Resume Matcher 简历生成 Agent 的评估调用入口。"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


async def _request_preview(payload: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    timeout = float(os.getenv("AGENT_TIMEOUT_SECONDS", "300"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        response = await client.post("/api/v1/resume-generations/preview", json=payload)
        response.raise_for_status()
        return response.json()


async def run_agent(input: str) -> dict[str, Any]:
    """通过正式 HTTP API 调用真实简历生成 Agent。"""
    try:
        payload = json.loads(input)
    except json.JSONDecodeError as exc:
        raise ValueError(
            '简历生成案例的 input 必须是 JSON 字符串，例如 '
            '{"jd_information_id": 1, "mode": "llm"}'
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("简历生成案例的 input JSON 必须是对象")

    preview = await _request_preview(payload)
    resume_data = preview["resume_data"]
    return {
        "actual_output": json.dumps(resume_data, ensure_ascii=False, indent=2),
        "run_id": preview["run_id"],
        "resume_data": resume_data,
        "provenance": preview["provenance"],
        "validation": preview["validation"],
    }


def create_metrics():
    """评估方法由后续任务定义；未定义时明确拒绝运行。"""
    raise NotImplementedError("请在 resume_generation suite 的 create_metrics() 中定义评估方法")
