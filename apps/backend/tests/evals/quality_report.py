"""真实能力 eval 的无敏感信息 JSON 报告。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_quality_report(
    capability: str,
    *,
    model: dict[str, Any] | None,
    thresholds: dict[str, Any],
    cases: list[dict[str, Any]],
    summary: dict[str, Any],
) -> Path:
    """在断言前落盘，使失败的真实输出仍可复盘。"""
    timestamp = datetime.now(UTC)
    output_dir = Path(__file__).parent / "results" / "quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}_{capability}.json"
    payload = {
        "capability": capability,
        "model": model,
        "summary": summary,
        "thresholds": thresholds,
        "cases": cases,
        "metadata": {
            "report_version": "1",
            "generated_at": timestamp.isoformat(),
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path.resolve()
