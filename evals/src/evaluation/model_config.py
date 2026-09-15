"""评估模型凭据映射；具体指标仍由业务定义。"""

from __future__ import annotations

import os

from pathlib import Path

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPOSITORY_ROOT / "apps" / "backend" / ".env", override=False)


def get_eval_model() -> str:
    """返回 DeepEval 指标可用的 OpenAI 模型名。"""
    model = (os.getenv("EVAL_MODEL") or os.getenv("LLM_MODEL", "")).strip()
    api_key = (os.getenv("EVAL_API_KEY") or os.getenv("LLM_API_KEY", "")).strip()
    if not model:
        raise RuntimeError("缺少 EVAL_MODEL 或 LLM_MODEL")
    if not api_key:
        raise RuntimeError("缺少 EVAL_API_KEY 或 LLM_API_KEY")
    os.environ["OPENAI_API_KEY"] = api_key
    api_base = (os.getenv("EVAL_API_BASE") or os.getenv("LLM_API_BASE", "")).strip()
    if api_base:
        os.environ["OPENAI_BASE_URL"] = api_base
    return model
