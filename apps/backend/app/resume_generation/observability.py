"""简历生成链路的结构化诊断日志。"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_generation_trace(
    event: str,
    *,
    run_id: str | None,
    search_round: int | None,
    payload: dict[str, Any],
) -> None:
    """把单个阶段产物写为单行 JSON，便于按 Run 和轮次检索。

    Args:
        event: 稳定事件名。
        run_id: 简历生成 Run ID；为空时跳过，避免产生不可关联的敏感日志。
        search_round: 当前检索/评分轮次。
        payload: 已可 JSON 序列化的阶段产物。
    """
    if not run_id:
        return
    record = {
        "event": event,
        "schema_version": 1,
        "run_id": run_id,
        "search_round": search_round,
        **payload,
    }
    logger.info(
        json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
