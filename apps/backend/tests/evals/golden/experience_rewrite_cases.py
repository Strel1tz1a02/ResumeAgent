"""按能力域维护的人工黄金样本。"""

from __future__ import annotations

_SAVED_EXPERIENCE = {
    "experience_id": 7,
    "kind": "work",
    "title": "支付平台稳定性改造",
    "organization": "星河科技",
    "role": "后端工程师",
    "location": "上海",
    "start_date": "2024-03",
    "end_date": "2025-01",
    "is_current": False,
    "background": "负责支付网关接口优化，解决大促期间超时问题。",
    "technologies": ["Python", "FastAPI"],
    "tags": ["性能优化"],
    "notes": None,
    "status": "ready",
    "completeness": 95,
    "evidence_items": [
        {
            "id": 71,
            "background": "大促期间接口超时",
            "action": "使用异步接口和缓存重构查询链路",
            "result": "P99 延迟从 420ms 降至 180ms，吞吐量提升 35%",
        }
    ],
}

EXPERIENCE_REWRITE_CASES = [
    {
        "name": "background-professional-rewrite",
        "language": "zh",
        "scope": "background",
        "saved_experience": _SAVED_EXPERIENCE,
        "current_content": _SAVED_EXPERIENCE["background"],
        "user_request": "请把背景改写得专业、精炼，并补入已确认事实：支付网关 P99 延迟从 420ms 降到 180ms，吞吐量提升 35%。不要添加其他事实。",
        "required_fragments": ["支付网关", "420ms", "180ms", "35%"],
        "forbidden_fragments": ["10人团队", "营收", "架构师", "字节跳动"],
        "expected_type": "str",
    },
    {
        "name": "technology-list-rewrite",
        "language": "zh",
        "scope": "technologies",
        "saved_experience": _SAVED_EXPERIENCE,
        "current_content": _SAVED_EXPERIENCE["technologies"],
        "user_request": "确认还使用了 Redis。请保留 Python、FastAPI 并加入 Redis；没有使用 Kubernetes。",
        "required_fragments": ["Python", "FastAPI", "Redis"],
        "forbidden_fragments": ["Kubernetes"],
        "expected_type": "list",
    },
]
